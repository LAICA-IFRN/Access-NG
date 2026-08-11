"""
Cerberos ESP32 (enxuto) - MicroPython MQTT

Firmware enxuto para um Cerberos ESP32 dedicado a abrir a fechadura. Nao
possui logica de botao/Caronte nem publica TAG para autenticacao - so
relé + LEDs + (opcionalmente) entradas físicas de acionamento.

--- Arquivos no dispositivo ----------------------------------------------

  boot.py             -> supervisor mínimo (era boot_esp32.py no repo)
  device_defaults.py  -> DEFAULTS/SENSITIVE_KEYS (era device_defaults_esp32.py)
  main.py             -> este arquivo (era main_esp32.py no repo)
  accessng/           -> pacote compartilhado (config/wifi/recovery/
                          provisioning/ota/watchdog), instalado uma vez,
                          não atualizado por OTA nesta fase
  bibliotecas/         -> vendorizadas (umqtt), buscadas automaticamente
                          por accessng.ota.ensure_dependencies() na
                          primeira vez que faltarem

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
    "INPUT_ENABLED"      : true,
    "INPUT_PINS"         : [26, 34],
    "INPUT_DEBOUNCE_MS"  : 200,
    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}

Se config.json estiver ausente/inválido, boot.py já intercepta antes deste
arquivo rodar e sobe o portal de provisionamento (AP AccessNG-Cerberos-XXXX
em 192.168.4.1) - este arquivo sempre roda com um config.json válido.
INPUT_PINS (lista) não é editável pelo portal - accessng/provisioning.py
não renderiza campos de lista no formulário genérico (só bool/int/float/
str); continua só configurável direto em config.json.

--- Pinagem ESP32 ------------------------------------------------------------

  GPIO 12 -> LED link vermelho. Aceso quando WiFi + broker MQTT estao OK.
  GPIO 13 -> LED status verde. Pisca quando ha trafego MQTT ou acionamento.
  GPIO 15 -> Rele da fechadura. Ativo alto, tempo maximo 2s.
  GPIO 26 -> Entrada logica para liberar o rele. Ativo baixo.
  GPIO 34 -> Entrada logica para liberar o rele. Ativo baixo.

Observacao: no ESP32, GPIO34 e somente entrada e nao possui pull-up interno.
Use resistor pull-up externo nessa entrada quando o sinal for ativo baixo.

Para desativar a entrada fisica (ex.: pino com ruido/acionamento espurio),
defina "INPUT_ENABLED": false no config.json - nenhum pino e inicializado
nem gera IRQ, sem precisar mexer em INPUT_PINS.

--- OTA (atualização remota) --------------------------------------------------

  Mesmo esquema do Caronte/FECHO - accessng.ota.check_for_update()/
  apply_update()/confirm_boot_ok(). Rede de segurança: se a versão nova
  não completar um coldstart com sucesso em até 3 boots, boot.py restaura
  automaticamente main.bak e reinicia - dispara para QUALQUER crash-loop,
  não só update pendente (ver accessng/recovery.py).
"""

import machine
import network
import socket
import time
import json
import os
import ubinascii
import gc

from device_defaults import DEFAULTS, SENSITIVE_KEYS
from accessng import config, wifi, ota, watchdog


# --- CONFIGURAÇÃO --------------------------------------------------------------

_CONFIG_RUNTIME_KEYS = ("HEARTBEAT_INTERVAL", "OTA_CHECK_INTERVAL", "OTA_ENABLED")

_cfg_file, _cfg_ok = config.load()
print("[Config] config.json carregado" if _cfg_ok else "[Config] Usando valores padrão")


def cfg(key):
    return config.get(_cfg_file, DEFAULTS, key)


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
INPUT_ENABLED      = cfg("INPUT_ENABLED")
INPUT_PINS         = cfg("INPUT_PINS")
INPUT_DEBOUNCE_MS  = cfg("INPUT_DEBOUNCE_MS")
OTA_ENABLED        = cfg("OTA_ENABLED")
OTA_CHECK_INTERVAL = cfg("OTA_CHECK_INTERVAL")

MQTT_PREFIX = "access-ng"
DEVICE_MAC  = None
AMBIENTE_ID = None
BOOT_COUNT  = None

# --- OTA -----------------------------------------------------------------

FIRMWARE_VERSAO   = "1.3.9"   # bump manual a cada release publicada - alinhar
                               # com version_esp32.json (ver comentário
                               # equivalente em Autenticador/main.py)
OTA_VERSION_PATH  = "Hardware/Fechadura/version_esp32.json"
OTA_FIRMWARE_PATH = "Hardware/Fechadura/main_esp32.py"
OTA_HOST          = "laica.ifrn.edu.br"
OTA_PORT          = 80

# Bibliotecas que este firmware precisa - accessng.ota.ensure_dependencies()
# busca as que ainda não existirem localmente. Mesma lista declarada em
# Hardware/Fechadura/version_esp32.json, campo "bibliotecas".
BIBLIOTECAS = ["umqtt/simple.py", "umqtt/robust.py"]

# --- DIAGNÓSTICO -----------------------------------------------------------

# "(boot.py)" é um marcador deliberado - ver o mesmo em Autenticador/main.py
# e Fechadura/main_esp32c3.py: migrador e main.py definitivo têm
# FIRMWARE_VERSAO independentes, então esse sufixo em Cerberos.hardware é
# o sinal confiável no painel admin de que a migração deu certo.
HARDWARE_INFO         = "Cerberos ESP32 DevKit (boot.py)"
HEARTBEAT_DIAG_EVERY  = 10   # rssi/mem_free/cpu_temp/fs_free vão a cada N heartbeats


_SOFT_RESET_FLAG = "soft_reset.flag"


def _read_boot_count():
    """Diagnóstico puro (soft-reset), sem relação com boot_state.json/
    boot_count (esse é o contador de crash-loop de accessng, usado por
    boot.py) - ver comentário equivalente em Autenticador/main.py."""
    try:
        with open(_SOFT_RESET_FLAG):
            is_soft = True
    except OSError:
        is_soft = False

    if is_soft:
        try:
            with open("boot_count.txt") as f:
                n = int(f.read().strip())
        except (OSError, ValueError):
            n = 0
        n += 1
    else:
        n = 0

    try:
        os.remove(_SOFT_RESET_FLAG)
    except OSError:
        pass
    try:
        with open("boot_count.txt", "w") as f:
            f.write(str(n))
    except OSError:
        pass
    return n


def _soft_reset():
    try:
        with open(_SOFT_RESET_FLAG, "w") as f:
            f.write("1")
    except OSError:
        pass
    machine.reset()


def _read_mcu():
    try:
        return os.uname().machine
    except Exception:
        return None


def _read_rssi():
    try:
        return network.WLAN(network.STA_IF).status("rssi")
    except Exception:
        return None


def _read_cpu_temp():
    try:
        import esp32
        return round((esp32.raw_temperature() - 32) * 5 / 9, 1)
    except Exception:
        return None


def _read_fs_stats():
    try:
        s = os.statvfs('/')
        frsize = s[1]
        return s[4] * frsize, s[2] * frsize
    except Exception:
        return None, None


def _read_wifi_status():
    try:
        return network.WLAN(network.STA_IF).status()
    except Exception:
        return None


def _read_wifi_channel():
    try:
        return network.WLAN(network.STA_IF).config("channel")
    except Exception:
        return None


def _read_ap_bssid():
    wlan = network.WLAN(network.STA_IF)
    for getter in (wlan.config, wlan.status):
        try:
            bssid = getter("bssid")
            if bssid:
                return ":".join("%02X" % b for b in bssid)
        except Exception:
            pass
    try:
        if wlan.isconnected():
            for rede in wlan.scan():
                if rede[0].decode("utf-8") == WIFI_SSID:
                    return ubinascii.hexlify(rede[1], ":").decode("utf-8")
    except Exception:
        pass
    return None


_wifi_reconnects = 0
_wifi_last_reconnect_s = None
_wifi_last_disconnect_status = None


def connect_wifi():
    """Wrapper fino sobre accessng.wifi.try_connect_once() - preserva o
    diagnóstico de reconexão (usado no heartbeat), que accessng.wifi não
    conhece."""
    global _wifi_reconnects, _wifi_last_reconnect_s, _wifi_last_disconnect_status
    if network.WLAN(network.STA_IF).isconnected():
        return True
    if _wifi_last_reconnect_s is not None:
        _wifi_last_disconnect_status = _read_wifi_status()
        _wifi_reconnects += 1
    _wifi_last_reconnect_s = time.time()
    print("[WiFi] Conectando em %s..." % WIFI_SSID)
    ok = wifi.try_connect_once(WIFI_SSID, WIFI_PASS)
    _set_link(ok)
    return ok


# --- HARDWARE ----------------------------------------------------------------

led_link     = None
led_status   = None
relay        = None
inputs       = []
_input_pin   = None


def _set_link(ok):
    led_link.value(1 if ok else 0)


def status_pulse(ms=80):
    led_status.value(1)
    time.sleep_ms(ms)
    led_status.value(0)


def _make_input_handler(pin_no):
    ts = [0]  # timestamp por pino - lista pré-alocada, sem GC no IRQ
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
    inputs = [_init_input(pin_no) for pin_no in INPUT_PINS] if INPUT_ENABLED else []
    print("[GPIO] Inicializado (entrada fisica %s)" % ("ativa" if INPUT_ENABLED else "desativada"))


def unlock_door(source="remote"):
    print("[Lock] Abrindo porta (%s)..." % source)
    led_status.value(1)
    relay.value(1)
    time.sleep_ms(RELAY_ACTIVE_MS)
    relay.value(0)
    led_status.value(0)
    print("[Lock] Porta fechada")


# --- OTA -----------------------------------------------------------------

def ota_check_and_maybe_apply(state):
    """Verifica e aplica update do firmware em si; se não havia (ou não
    aplicou), também checa o pacote accessng/ (versionado à parte - ver
    accessng/ota.py). Um update de firmware bem sucedido já reinicia e
    não devolve o controle pra esta função."""
    remote = ota.check_for_update(OTA_HOST, OTA_PORT, OTA_VERSION_PATH,
                                   FIRMWARE_VERSAO, OTA_ENABLED)
    if remote:
        if ota.apply_update(state, OTA_HOST, OTA_PORT, OTA_FIRMWARE_PATH, remote,
                             target_file="main.py", backup_file="main.bak"):
            config.save_state(state)
            time.sleep(1)
            _soft_reset()
        return

    pkg_remote = ota.check_for_package_update(OTA_HOST, OTA_PORT,
                                               state.get("accessng_version"), OTA_ENABLED)
    if pkg_remote:
        if ota.apply_package_update(state, OTA_HOST, OTA_PORT, pkg_remote, BIBLIOTECAS):
            config.save_state(state)
            time.sleep(1)
            _soft_reset()


# --- MQTT --------------------------------------------------------------------

_client = None
_coldstart_result = None
_update_requested = False
_unlock_flag = False


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
        topics["command"] = "%s/%s/cerberos/%s/command" % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
        topics["entrada"] = "%s/%s/cerberos/%s/entrada" % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
        topics["config_result"] = "%s/%s/cerberos/%s/config/result" % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
    return topics


def publish_entrada(pin_no):
    topic = _topics().get("entrada")
    if topic is None:
        return
    _client.publish(topic, json.dumps({
        "mac": DEVICE_MAC,
        "pin": pin_no,
    }))
    print("[Lock] Entrada fisica publicada (pin=%d)" % pin_no)


def _publish_config():
    params = {}
    for key in DEFAULTS:
        persistido = key in _cfg_file
        if key in SENSITIVE_KEYS:
            params[key] = {"persistido": persistido}
        else:
            params[key] = {"valor": globals().get(key, DEFAULTS[key]), "persistido": persistido}
    topic = _topics().get("config_result")
    if topic:
        _client.publish(topic, json.dumps({"mac": DEVICE_MAC, "params": params}))
        print("[Config] Configuração atual reportada")


def _apply_set_config(params):
    validos = {k: v for k, v in (params or {}).items() if k in DEFAULTS}
    if not validos:
        print("[Config] set_config sem parametros validos, ignorando")
        return
    _cfg_file.update(validos)
    try:
        config.save(_cfg_file)
    except Exception as e:
        print("[Config] Erro ao gravar config.json:", e)
        return
    print("[Config] Novos parametros gravados, reiniciando:", list(validos.keys()))
    time.sleep(1)
    _soft_reset()


def _apply_session_config(config_dict):
    if not isinstance(config_dict, dict):
        return
    for key, value in config_dict.items():
        if key not in _CONFIG_RUNTIME_KEYS or key not in DEFAULTS:
            continue
        try:
            globals()[key] = type(DEFAULTS[key])(value)
            print("[Config] %s sobrescrito para %r (somente sessão)" % (key, globals()[key]))
        except Exception:
            pass


def _on_message(topic, payload):
    global _coldstart_result, _update_requested, _unlock_flag
    topic_str = topic.decode("utf-8")
    status_pulse()

    try:
        data = json.loads(payload)
    except Exception:
        print("[MQTT] Payload inválido")
        return

    topics = _topics()
    if topic_str == topics["coldstart_result"]:
        _coldstart_result = data
    elif topic_str == topics.get("command"):
        if data.get("command") in ("unlock", "open", "abrir"):
            print("[MQTT] Comando de abertura recebido")
            _unlock_flag = True
        elif data.get("command") == "check_update":
            print("[MQTT] Solicitação de verificação de atualização recebida")
            _update_requested = True
        elif data.get("command") == "reboot":
            print("[MQTT] Comando de reinício recebido - reiniciando...")
            status_pulse(200)
            time.sleep_ms(300)
            _soft_reset()
        elif data.get("command") == "get_config":
            print("[MQTT] Solicitação de configuração recebida")
            _publish_config()
        elif data.get("command") == "set_config":
            _apply_set_config(data.get("params"))


def mqtt_connect():
    global _client
    try:
        from umqtt.simple import MQTTClient
    except ImportError:
        from umqtt.robust import MQTTClient

    kwargs = {"port": MQTT_PORT, "keepalive": 90}
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
        try:
            _client.publish(
                _topics()["coldstart"],
                json.dumps({
                    "mac": DEVICE_MAC, "chave": DEVICE_KEY, "versao": FIRMWARE_VERSAO,
                    "boot_count": BOOT_COUNT, "hardware": HARDWARE_INFO,
                    "mcu": _read_mcu(), "ssid": WIFI_SSID, "rssi": _read_rssi(),
                }),
                qos=0,
            )
            status_pulse()
            print("[MQTT] Coldstart publicado, aguardando confirmação...")

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
        except OSError as e:
            print("[MQTT] Erro de rede no coldstart: %s - reconectando..." % e)
            try:
                mqtt_connect()
            except Exception:
                pass
            time.sleep(5)
            continue

        if _coldstart_result and _coldstart_result.get("status") == "ok":
            AMBIENTE_ID = _coldstart_result.get("ambiente_id")
            _apply_session_config(_coldstart_result.get("config"))
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


def _format_uptime(uptime_s):
    days, rem = divmod(uptime_s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return "%dT%02d:%02d:%02d" % (days, hours, minutes, seconds)


_boot_time = time.time()

_heartbeat_count = 0
_mem_free_min = None


def publish_heartbeat():
    global _heartbeat_count, _mem_free_min
    uptime_s = time.time() - _boot_time
    payload = {
        "mac": DEVICE_MAC,
        "uptime_s": uptime_s,
        "uptime": _format_uptime(uptime_s),
        "ip": network.WLAN(network.STA_IF).ifconfig()[0],
        "versao": FIRMWARE_VERSAO,
    }
    _heartbeat_count += 1
    if _heartbeat_count % HEARTBEAT_DIAG_EVERY == 1:
        payload["rssi"] = _read_rssi()
        mem_free = gc.mem_free()
        payload["mem_free"] = mem_free
        if _mem_free_min is None or mem_free < _mem_free_min:
            _mem_free_min = mem_free
        payload["mem_free_min"] = _mem_free_min
        payload["cpu_temp"] = _read_cpu_temp()
        payload["wifi_status"] = _read_wifi_status()
        payload["wifi_channel"] = _read_wifi_channel()
        payload["bssid"] = _read_ap_bssid()
        payload["wifi_reconnects"] = _wifi_reconnects
        if _wifi_last_reconnect_s is not None:
            payload["wifi_last_reconnect_s"] = time.time() - _wifi_last_reconnect_s
        if _wifi_last_disconnect_status is not None:
            payload["wifi_last_disconnect_status"] = _wifi_last_disconnect_status
        fs_free, fs_total = _read_fs_stats()
        payload["fs_free"] = fs_free
        payload["fs_total"] = fs_total
    _client.publish(_topics()["heartbeat"], json.dumps(payload))
    status_pulse()


# --- MAIN --------------------------------------------------------------------

def main():
    global DEVICE_MAC, BOOT_COUNT, _input_pin, _unlock_flag, _update_requested

    print("\n" + "=" * 48)
    print("  CERBEROS ESP32 - MQTT")
    print("=" * 48)

    state = config.load_state()
    BOOT_COUNT = _read_boot_count()
    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config("mac"), ":").decode()
    print("[Device] MAC: %s" % DEVICE_MAC)

    # Na prática, boot.py já deixou o rádio conectado - este laço serve de
    # rede de segurança caso algo tenha mudado entre um passo e outro.
    while not connect_wifi():
        watchdog.feed()
        status_pulse(120)
        time.sleep(10)

    ota.ensure_dependencies(OTA_HOST, OTA_PORT, BIBLIOTECAS)

    # Checagem de update ANTES do MQTT, usando só WiFi/HTTP (não depende
    # de MQTT/TLS funcionar) - um bug que quebra a própria conexão MQTT
    # (ex.: biblioteca vendorizada com um import que não existe nessa
    # versão do MicroPython) nunca deixaria o dispositivo alcançar o
    # ota_check_and_maybe_apply() de baixo, que só roda depois do
    # primeiro coldstart bem-sucedido - preso num crash-loop sem nunca
    # chegar na correção que resolveria justamente esse problema.
    ota_check_and_maybe_apply(state)

    tentativas_mqtt = 0
    while True:
        watchdog.feed()
        try:
            mqtt_connect()
            do_coldstart()
            break
        except Exception as e:
            tentativas_mqtt += 1
            print("[MQTT] Falha na conexão: %s (%d/5) - tentando em 10s..." %
                  (e, tentativas_mqtt))
            _set_link(False)
            status_pulse(120)
            time.sleep(10)
            if tentativas_mqtt >= 5:
                print("[MQTT] Sem sucesso após 5 tentativas - reiniciando")
                _soft_reset()

    last_heartbeat = time.time()
    ota.confirm_boot_ok(state, FIRMWARE_VERSAO)
    config.save_state(state)
    print("[Main] Operacional\n")

    last_ota_check = time.time()
    ota_check_and_maybe_apply(state)

    while True:
        watchdog.feed()
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

            if OTA_ENABLED and time.time() - last_ota_check >= OTA_CHECK_INTERVAL:
                ota_check_and_maybe_apply(state)
                last_ota_check = time.time()

            _client.check_msg()

            if _unlock_flag:
                _unlock_flag = False
                unlock_door("mqtt")

            if _update_requested:
                _update_requested = False
                ota_check_and_maybe_apply(state)
                last_ota_check = time.time()

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
                print("[MQTT] Falha na reconexão: %s" % e2)
                time.sleep(5)
        except Exception as e:
            print("[Main] Erro: %s" % e)
            time.sleep(1)


main()
