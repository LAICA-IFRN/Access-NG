"""
Caronte ESP32-C3 - MicroPython MQTT + Wiegand RFID

Firmware para um Caronte com leitor Wiegand no ESP32 SSC C3.
Lê TAGs RFID, publica no broker MQTT e aguarda resultado de autorização.
Não possui Cerberos embutido — apenas leitura e publicação.

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

    "WG_D0_PIN"          : 5,
    "WG_D1_PIN"          : 7,
    "BUZZER_PIN"         : 6,
    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "WG_TIMEOUT_MS"      : 25,
    "AUTH_TIMEOUT_S"     : 5
}

--- Pinagem ESP32 SSC C3 -----------------------------------------------------

  GPIO 01 -> LED VM  (vermelho) — não soldado nesta placa
  GPIO 02 -> LED VD3 (verde 3)  — não soldado nesta placa
  GPIO 03 -> LED VD2 (verde 2)  — não soldado nesta placa
  GPIO 04 -> LED VD1 (verde 1)  — não soldado nesta placa
  GPIO 05 -> Wiegand D0 (ativo baixo)
  GPIO 06 -> Buzzer (ativo alto)
  GPIO 07 -> Wiegand D1 (ativo baixo)

  GPIO 08 -> SDA display OLED  — não soldado nesta placa
  GPIO 09 -> SCL display OLED  — não soldado nesta placa
  GPIO 10 -> Enable RS485      — não soldado nesta placa
  GPIO 20 -> RX RS485          — não soldado nesta placa
  GPIO 21 -> TX RS485          — não soldado nesta placa

--- Protocolo Wiegand --------------------------------------------------------

  D0 idle = HIGH, pulso = LOW (~50 µs) -> bit 0
  D1 idle = HIGH, pulso = LOW (~50 µs) -> bit 1
  Fim da leitura: silêncio > WG_TIMEOUT_MS após o último pulso.
  Suporte: Wiegand 26 bits (mais comum) e fallback para outros formatos.

--- Tópicos MQTT -------------------------------------------------------------

  Publica:
    access-ng/coldstart/{mac}                    -> boot do dispositivo
    access-ng/heartbeat/{mac}                    -> presença periódica
    access-ng/{amb_id}/caronte/{mac}/tag         -> leitura de TAG RFID

  Assina:
    access-ng/coldstart/{mac}/result             -> resposta do coldstart
    access-ng/{amb_id}/caronte/{mac}/result      -> resultado da autenticação

  O MAC usa '-' no lugar de ':' nos tópicos.
"""

import machine
import network
import time
import json
import ubinascii
import micropython

micropython.alloc_emergency_exception_buf(100)


# --- CONFIGURAÇÃO ------------------------------------------------------------

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
    "WG_D0_PIN"          : 5,
    "WG_D1_PIN"          : 7,
    "BUZZER_PIN"         : 6,
    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "WG_TIMEOUT_MS"      : 25,
    "AUTH_TIMEOUT_S"     : 5,
}

try:
    with open("config.json") as f:
        _cfg_file = json.load(f)
    print("[Config] config.json carregado")
except Exception:
    _cfg_file = {}
    print("[Config] Usando valores padrão")


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
WG_D0_PIN          = cfg("WG_D0_PIN")
WG_D1_PIN          = cfg("WG_D1_PIN")
BUZZER_PIN         = cfg("BUZZER_PIN")
LED_VM_PIN         = cfg("LED_VM_PIN")
LED_VD1_PIN        = cfg("LED_VD1_PIN")
LED_VD2_PIN        = cfg("LED_VD2_PIN")
LED_VD3_PIN        = cfg("LED_VD3_PIN")
WG_TIMEOUT_MS      = cfg("WG_TIMEOUT_MS")
AUTH_TIMEOUT_S     = cfg("AUTH_TIMEOUT_S")

MQTT_PREFIX = "access-ng"
DEVICE_MAC  = None
AMBIENTE_ID = None


# --- HARDWARE ----------------------------------------------------------------

buzzer  = None
led_vm  = None
led_vd1 = None
led_vd2 = None
led_vd3 = None
wg_d0   = None
wg_d1   = None

# Buffer Wiegand — bytearray pré-alocado para ser seguro em ISR (sem GC)
_wg_buf     = bytearray(64)
_wg_count   = 0
_wg_last_ms = 0


def _wg_d0_isr(_pin):
    global _wg_count, _wg_last_ms
    if _wg_count < 64:
        _wg_buf[_wg_count] = 0
        _wg_count += 1
    _wg_last_ms = time.ticks_ms()


def _wg_d1_isr(_pin):
    global _wg_count, _wg_last_ms
    if _wg_count < 64:
        _wg_buf[_wg_count] = 1
        _wg_count += 1
    _wg_last_ms = time.ticks_ms()


def init_gpio():
    global buzzer, led_vm, led_vd1, led_vd2, led_vd3, wg_d0, wg_d1
    buzzer  = machine.Pin(BUZZER_PIN,  machine.Pin.OUT, value=0)
    led_vm  = machine.Pin(LED_VM_PIN,  machine.Pin.OUT, value=0)
    led_vd1 = machine.Pin(LED_VD1_PIN, machine.Pin.OUT, value=0)
    led_vd2 = machine.Pin(LED_VD2_PIN, machine.Pin.OUT, value=0)
    led_vd3 = machine.Pin(LED_VD3_PIN, machine.Pin.OUT, value=0)
    wg_d0 = machine.Pin(WG_D0_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    wg_d1 = machine.Pin(WG_D1_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    wg_d0.irq(trigger=machine.Pin.IRQ_FALLING, handler=_wg_d0_isr)
    wg_d1.irq(trigger=machine.Pin.IRQ_FALLING, handler=_wg_d1_isr)
    print("[GPIO] Inicializado")


def beep(ms=100):
    buzzer.value(1)
    time.sleep_ms(ms)
    buzzer.value(0)


def feedback_allow():
    """Dois bipes curtos + LED verde."""
    led_vd1.value(1)
    beep(100)
    time.sleep_ms(80)
    beep(100)
    time.sleep_ms(800)
    led_vd1.value(0)


def feedback_deny():
    """Um bipe longo + LED vermelho."""
    led_vm.value(1)
    beep(600)
    time.sleep_ms(400)
    led_vm.value(0)


def _decode_wiegand(buf, count):
    """Converte bits Wiegand em string hexadecimal maiúscula (TAG)."""
    if count < 4:
        return None
    raw = 0
    for i in range(count):
        raw = (raw << 1) | buf[i]
    if count == 26:
        # P[8 facility][16 card]P
        facility = (raw >> 17) & 0xFF
        card     = (raw >> 1)  & 0xFFFF
        return "%08X" % ((facility << 16) | card)
    if count == 34:
        # P[16 facility][16 card]P
        facility = (raw >> 17) & 0xFFFF
        card     = (raw >> 1)  & 0xFFFF
        return "%08X" % ((facility << 16) | card)
    # Formato desconhecido: remove bits de paridade nas extremidades
    inner = (raw >> 1) & ((1 << (count - 2)) - 1)
    return "%X" % inner


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
        time.sleep(0.5)

    print("[WiFi] Falha")
    return False


# --- MQTT --------------------------------------------------------------------

_client           = None
_coldstart_result = None
_auth_result      = None


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
        topics["tag"]    = "%s/%s/caronte/%s/tag"    % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
        topics["result"] = "%s/%s/caronte/%s/result" % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
    return topics


def _on_message(topic, payload):
    global _coldstart_result, _auth_result
    topic_str = topic.decode("utf-8")
    try:
        data = json.loads(payload)
    except Exception:
        print("[MQTT] Payload inválido")
        return

    topics = _topics()
    if topic_str == topics["coldstart_result"]:
        _coldstart_result = data
    elif topic_str == topics.get("result"):
        _auth_result = data


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

    client = MQTTClient("caronte-%s" % _mac_safe(), MQTT_BROKER, **kwargs)
    client.set_callback(_on_message)
    client.connect()
    client.subscribe(_topics()["coldstart_result"])
    _client = client
    print("[MQTT] Conectado ao broker %s:%s" % (MQTT_BROKER, MQTT_PORT))


def do_coldstart():
    global AMBIENTE_ID, _coldstart_result
    while True:
        _coldstart_result = None
        _client.publish(
            _topics()["coldstart"],
            json.dumps({"mac": DEVICE_MAC, "chave": DEVICE_KEY}),
            qos=1,
        )
        print("[MQTT] Coldstart publicado, aguardando confirmação...")

        t0 = time.time()
        while time.time() - t0 < 5:
            _client.check_msg()
            if _coldstart_result is not None:
                break
            time.sleep_ms(100)

        if _coldstart_result and _coldstart_result.get("status") == "ok":
            AMBIENTE_ID = _coldstart_result.get("ambiente_id")
            _client.subscribe(_topics()["result"])
            print("[MQTT] Coldstart OK - ambiente_id=%s" % AMBIENTE_ID)
            return

        print("[MQTT] Coldstart negado/sem resposta (%s) - tentando em 15s..." %
              _coldstart_result)
        for _ in range(15):
            beep(40)
            time.sleep(1)


def publish_heartbeat():
    uptime_ms = time.ticks_ms()
    _client.publish(_topics()["heartbeat"], json.dumps({
        "mac": DEVICE_MAC,
        "uptime_ms": uptime_ms,
        "uptime_s": uptime_ms // 1000,
    }))


def publish_tag(tag):
    topic = _topics().get("tag")
    if topic is None:
        return
    _client.publish(topic, json.dumps({
        "tag"  : tag,
        "chave": DEVICE_KEY,
    }), qos=1)
    print("[RFID] TAG publicada: %s" % tag)


# --- MAIN --------------------------------------------------------------------

def main():
    global DEVICE_MAC, _auth_result, _wg_count

    print("\n" + "=" * 48)
    print("  CARONTE ESP32-C3 - MQTT + WIEGAND")
    print("=" * 48)

    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config("mac"), ":").decode()
    print("[Device] MAC: %s" % DEVICE_MAC)

    while not connect_wifi():
        beep(120)
        time.sleep(10)

    while True:
        try:
            mqtt_connect()
            do_coldstart()
            break
        except Exception as e:
            print("[MQTT] Falha na conexão: %s - tentando em 10s..." % e)
            beep(120)
            time.sleep(10)

    last_heartbeat = time.time()
    print("[Main] Operacional\n")

    while True:
        try:
            if not network.WLAN(network.STA_IF).isconnected():
                print("[WiFi] Reconectando...")
                if connect_wifi():
                    mqtt_connect()
                    do_coldstart()
                    last_heartbeat = time.time()
                else:
                    time.sleep(5)
                    continue

            # Leitura Wiegand completa: silêncio > WG_TIMEOUT_MS
            if _wg_count > 0 and time.ticks_diff(time.ticks_ms(), _wg_last_ms) > WG_TIMEOUT_MS:
                state = machine.disable_irq()
                count = _wg_count
                _wg_count = 0
                machine.enable_irq(state)

                tag = _decode_wiegand(_wg_buf, count)
                print("[RFID] %d bits lidos -> TAG: %s" % (count, tag))

                if tag:
                    _auth_result = None
                    publish_tag(tag)

                    t0 = time.time()
                    while time.time() - t0 < AUTH_TIMEOUT_S:
                        _client.check_msg()
                        if _auth_result is not None:
                            break
                        time.sleep_ms(100)

                    if _auth_result and _auth_result.get("allow"):
                        feedback_allow()
                    else:
                        feedback_deny()
                    _auth_result = None

            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = time.time()

            _client.check_msg()
            time.sleep_ms(20)

        except OSError as e:
            print("[MQTT] Erro de rede: %s - reconectando..." % e)
            try:
                if not network.WLAN(network.STA_IF).isconnected():
                    connect_wifi()
                mqtt_connect()
                do_coldstart()
                last_heartbeat = time.time()
            except Exception as e2:
                print("[MQTT] Falha na reconexão: %s" % e2)
                time.sleep(5)
        except Exception as e:
            print("[Main] Erro: %s" % e)
            time.sleep(1)


main()
