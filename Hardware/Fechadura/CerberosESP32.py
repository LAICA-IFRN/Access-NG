"""
Cerberos ESP32 - MicroPython MQTT

Firmware enxuto para um Cerberos ESP32 dedicado a abrir a fechadura.
Nao possui logica de botao/Caronte nem publica TAG para autenticacao.

--- config.json --------------------------------------------------------------

{
    "WIFI_SSID"          : "nome-da-rede",
    "WIFI_PASS"          : "senha-da-rede",

    "MQTT_BROKER"        : "broker.exemplo.com",
    "MQTT_PORT"          : 1883,
    "MQTT_USER"          : "",
    "MQTT_PASS"          : "",
    "MQTT_TLS"           : false,

    "DEVICE_KEY"         : "chave-cadastrada-no-banco",
    "HEARTBEAT_INTERVAL" : 25,

    "LED_LINK_PIN"       : 12,
    "LED_STATUS_PIN"     : 13,
    "RELAY_PIN"          : 15,
    "RELAY_ACTIVE_MS"    : 2000,
    "INPUT_PINS"         : [26, 34],
    "INPUT_DEBOUNCE_MS"  : 200
}

--- Pinagem ESP32 ------------------------------------------------------------

  GPIO 12 -> LED link vermelho. Aceso quando WiFi + broker MQTT estao OK.
  GPIO 13 -> LED status verde. Pisca quando ha trafego MQTT ou acionamento.
  GPIO 15 -> Rele da fechadura. Ativo alto, tempo maximo 2s.
  GPIO 26 -> Entrada logica para liberar o rele. Ativo baixo.
  GPIO 34 -> Entrada logica para liberar o rele. Ativo baixo.

Observacao: no ESP32, GPIO34 e somente entrada e nao possui pull-up interno.
Use resistor pull-up externo nessa entrada quando o sinal for ativo baixo.

--- Topicos MQTT -------------------------------------------------------------

  Publica:
    access-ng/coldstart/{mac}                     -> boot do dispositivo
    access-ng/heartbeat/{mac}                     -> presenca periodica
    access-ng/{amb_id}/cerberos/{mac}/entrada     -> acionamento por pino fisico

  Assina:
    access-ng/coldstart/{mac}/result          -> resposta do coldstart
    access-ng/{amb_id}/cerberos/{mac}/command -> comando de abertura

  O MAC usa '-' no lugar de ':' nos topicos.
"""

import machine
import network
import time
import json
import ubinascii


# --- CONFIGURACAO ------------------------------------------------------------

_DEFAULTS = {
    "WIFI_SSID"          : "wIFRN-IoT",
    "WIFI_PASS"          : "deviceiotifrn",
    "MQTT_BROKER"        : "broker.exemplo.com",
    "MQTT_PORT"          : 1883,
    "MQTT_USER"          : "",
    "MQTT_PASS"          : "",
    "MQTT_TLS"           : False,
    "DEVICE_KEY"         : "chave-do-dispositivo",
    "HEARTBEAT_INTERVAL" : 25,
    "LED_LINK_PIN"       : 12,
    "LED_STATUS_PIN"     : 13,
    "RELAY_PIN"          : 15,
    "RELAY_ACTIVE_MS"    : 2000,
    "INPUT_PINS"         : [26, 34],
    "INPUT_DEBOUNCE_MS"  : 200,
}

try:
    with open("config.json") as f:
        _cfg_file = json.load(f)
    print("[Config] config.json carregado")
except Exception:
    _cfg_file = {}
    print("[Config] Usando valores padrao")


def cfg(key):
    default = _DEFAULTS[key]
    value = _cfg_file.get(key, default)
    if isinstance(default, list):
        return value
    return type(default)(value)


WIFI_SSID          = cfg("WIFI_SSID")
WIFI_PASS          = cfg("WIFI_PASS")
MQTT_BROKER        = cfg("MQTT_BROKER")
MQTT_PORT          = cfg("MQTT_PORT")
MQTT_USER          = cfg("MQTT_USER")
MQTT_PASS          = cfg("MQTT_PASS")
MQTT_TLS           = cfg("MQTT_TLS")
DEVICE_KEY         = cfg("DEVICE_KEY")
HEARTBEAT_INTERVAL = cfg("HEARTBEAT_INTERVAL")
LED_LINK_PIN       = cfg("LED_LINK_PIN")
LED_STATUS_PIN     = cfg("LED_STATUS_PIN")
RELAY_PIN          = cfg("RELAY_PIN")
RELAY_ACTIVE_MS    = min(cfg("RELAY_ACTIVE_MS"), 2000)
INPUT_PINS         = cfg("INPUT_PINS")
INPUT_DEBOUNCE_MS  = cfg("INPUT_DEBOUNCE_MS")

MQTT_PREFIX = "access-ng"
DEVICE_MAC  = None
AMBIENTE_ID = None


# --- HARDWARE ----------------------------------------------------------------

led_link      = None
led_status    = None
relay         = None
inputs        = []
_input_pin   = None
_unlock_flag = False


def _set_link(ok):
    led_link.value(1 if ok else 0)


def status_pulse(ms=80):
    led_status.value(1)
    time.sleep_ms(ms)
    led_status.value(0)


def _make_input_handler(pin_no):
    ts = [0]  # timestamp por pino — lista pré-alocada, sem GC no IRQ
    def _handler(_pin):
        global _input_pin
        now = time.ticks_ms()
        if time.ticks_diff(now, ts[0]) >= INPUT_DEBOUNCE_MS:
            ts[0] = now
            _input_pin = pin_no
    return _handler


def _init_input(pin_no):
    try:
        pin = machine.Pin(pin_no, machine.Pin.IN, machine.Pin.PULL_UP)
    except Exception:
        pin = machine.Pin(pin_no, machine.Pin.IN)
    pin.irq(trigger=machine.Pin.IRQ_FALLING, handler=_make_input_handler(pin_no))
    return pin


def init_gpio():
    global led_link, led_status, relay, inputs
    led_link = machine.Pin(LED_LINK_PIN, machine.Pin.OUT, value=0)
    led_status = machine.Pin(LED_STATUS_PIN, machine.Pin.OUT, value=0)
    relay = machine.Pin(RELAY_PIN, machine.Pin.OUT, value=0)
    inputs = [_init_input(pin_no) for pin_no in INPUT_PINS]
    print("[GPIO] Inicializado")


def unlock_door(source="remote"):
    print("[Lock] Abrindo porta (%s)..." % source)
    led_status.value(1)
    relay.value(1)
    time.sleep_ms(RELAY_ACTIVE_MS)
    relay.value(0)
    led_status.value(0)
    print("[Lock] Porta fechada")


# --- WIFI --------------------------------------------------------------------

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print("[WiFi] IP: %s" % wlan.ifconfig()[0])
        return True

    print("[WiFi] Conectando em %s..." % WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
        if wlan.isconnected():
            print("[WiFi] IP: %s" % wlan.ifconfig()[0])
            return True
        led_link.value(1 - led_link.value())
        time.sleep(0.5)

    led_link.value(0)
    print("[WiFi] Falha")
    return False


# --- MQTT --------------------------------------------------------------------

_client = None
_coldstart_result = None


def _mac_safe():
    return DEVICE_MAC.replace(":", "-")


def _topics():
    mac = _mac_safe()
    topics = {
        "coldstart"       : "%s/coldstart/%s" % (MQTT_PREFIX, mac),
        "coldstart_result": "%s/coldstart/%s/result" % (MQTT_PREFIX, mac),
        "heartbeat"       : "%s/heartbeat/%s" % (MQTT_PREFIX, mac),
    }
    if AMBIENTE_ID is not None:
        topics["command"] = "%s/%s/cerberos/%s/command" % (
            MQTT_PREFIX,
            str(AMBIENTE_ID),
            mac,
        )
        topics["entrada"] = "%s/%s/cerberos/%s/entrada" % (
            MQTT_PREFIX,
            str(AMBIENTE_ID),
            mac,
        )
    return topics


def _on_message(topic, payload):
    global _unlock_flag, _coldstart_result
    topic_str = topic.decode("utf-8")
    status_pulse()

    try:
        data = json.loads(payload)
    except Exception:
        print("[MQTT] Payload invalido")
        return

    topics = _topics()
    if topic_str == topics["coldstart_result"]:
        _coldstart_result = data
    elif topic_str == topics.get("command"):
        if data.get("command") in ("unlock", "open", "abrir"):
            print("[MQTT] Comando de abertura recebido")
            _unlock_flag = True


def mqtt_connect():
    global _client
    try:
        from umqtt.robust import MQTTClient
    except ImportError:
        from umqtt.simple import MQTTClient

    kwargs = {"port": MQTT_PORT, "keepalive": 30}
    if MQTT_USER:
        kwargs["user"] = MQTT_USER
        kwargs["password"] = MQTT_PASS
    if MQTT_TLS:
        kwargs["ssl"] = True

    client = MQTTClient("cerberos-%s" % _mac_safe(), MQTT_BROKER, **kwargs)
    client.set_callback(_on_message)
    client.connect()
    client.subscribe(_topics()["coldstart_result"])
    _client = client
    print("[MQTT] Conectado ao broker %s:%s" % (MQTT_BROKER, MQTT_PORT))


def do_coldstart():
    global AMBIENTE_ID, _coldstart_result
    while True:
        _coldstart_result = None
        _set_link(False)
        _client.publish(
            _topics()["coldstart"],
            json.dumps({"mac": DEVICE_MAC, "chave": DEVICE_KEY}),
            qos=1,
        )
        status_pulse()
        print("[MQTT] Coldstart publicado, aguardando confirmacao...")

        t0 = time.time()
        tick = 0
        while time.time() - t0 < 5:
            _client.check_msg()
            if tick % 5 == 0:
                led_link.value(1 - led_link.value())
            tick += 1
            if _coldstart_result is not None:
                break
            time.sleep_ms(100)

        if _coldstart_result and _coldstart_result.get("status") == "ok":
            AMBIENTE_ID = _coldstart_result.get("ambiente_id")
            _client.subscribe(_topics()["command"])
            _set_link(True)
            print("[MQTT] Coldstart OK - ambiente_id=%s" % AMBIENTE_ID)
            return

        print("[MQTT] Coldstart negado/sem resposta (%s) - tentando em 15s..." %
              _coldstart_result)
        led_link.value(0)
        for _ in range(15):
            led_link.value(1 - led_link.value())
            status_pulse(40)
            time.sleep(1)


def publish_entrada(pin_no):
    topic = _topics().get("entrada")
    if topic is None:
        return
    _client.publish(topic, json.dumps({
        "mac": DEVICE_MAC,
        "pin": pin_no,
    }))
    print("[Lock] Entrada fisica publicada (pin=%d)" % pin_no)


def publish_heartbeat():
    uptime_ms = time.ticks_ms()
    _client.publish(_topics()["heartbeat"], json.dumps({
        "mac": DEVICE_MAC,
        "uptime_ms": uptime_ms,
        "uptime_s": uptime_ms // 1000,
    }))
    status_pulse()


# --- MAIN --------------------------------------------------------------------

def main():
    global DEVICE_MAC, _input_pin, _unlock_flag

    print("\n" + "=" * 48)
    print("  CERBEROS ESP32 - MQTT")
    print("=" * 48)

    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config("mac"), ":").decode()
    print("[Device] MAC: %s" % DEVICE_MAC)

    while not connect_wifi():
        _set_link(False)
        status_pulse(120)
        time.sleep(10)

    while True:
        try:
            mqtt_connect()
            do_coldstart()
            break
        except Exception as e:
            print("[MQTT] Falha na conexao: %s - tentando em 10s..." % e)
            _set_link(False)
            status_pulse(120)
            time.sleep(10)

    last_heartbeat = time.time()
    print("[Main] Operacional\n")

    while True:
        try:
            if not network.WLAN(network.STA_IF).isconnected():
                print("[WiFi] Reconectando...")
                _set_link(False)
                if connect_wifi():
                    mqtt_connect()
                    do_coldstart()
                    last_heartbeat = time.time()
                else:
                    time.sleep(5)
                    continue

            if _input_pin is not None:
                pin_no = _input_pin
                _input_pin = None
                unlock_door("entrada")
                publish_entrada(pin_no)

            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = time.time()

            _client.check_msg()

            if _unlock_flag:
                _unlock_flag = False
                unlock_door("mqtt")

            time.sleep_ms(50)

        except OSError as e:
            print("[MQTT] Erro de rede: %s - reconectando..." % e)
            _set_link(False)
            try:
                if not network.WLAN(network.STA_IF).isconnected():
                    connect_wifi()
                mqtt_connect()
                do_coldstart()
                last_heartbeat = time.time()
            except Exception as e2:
                print("[MQTT] Falha na reconexao: %s" % e2)
                time.sleep(5)
        except Exception as e:
            print("[Main] Erro: %s" % e)
            time.sleep(1)


main()
