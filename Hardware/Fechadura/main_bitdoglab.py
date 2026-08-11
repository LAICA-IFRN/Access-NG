"""
Cerberos + Caronte - MicroPython para BitDogLab V6 (Raspberry Pi Pico W)
Modo MQTT exclusivo

Este firmware combina os dois papéis (Cerberos + Caronte) na mesma placa:
o botão local publica uma TAG fixa (BUTTON_TAG) para autenticação, igual
um Caronte, e o próprio dispositivo libera a fechadura ao receber
autorização, igual um Cerberos.

--- Arquivos no dispositivo ----------------------------------------------

  boot.py             -> supervisor mínimo (era boot_bitdoglab.py no repo)
  device_defaults.py  -> DEFAULTS/SENSITIVE_KEYS (era device_defaults_bitdoglab.py)
  main.py             -> este arquivo (era main_bitdoglab.py no repo)
  accessng/           -> pacote compartilhado (config/wifi/recovery/
                          provisioning/ota/watchdog), instalado uma vez,
                          não atualizado por OTA nesta fase
  bibliotecas/         -> vendorizadas (umqtt, ssd1306), buscadas
                          automaticamente por accessng.ota.
                          ensure_dependencies() na primeira vez que
                          faltarem

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

    "BUTTON_PIN"         : 5,
    "BUTTON_DEBOUNCE_MS" : 50,
    "BUTTON_TAG"         : "btn_local",

    "LED_RED_PIN"        : 13,
    "LED_GREEN_PIN"      : 11,
    "LED_BLUE_PIN"       : 12,
    "RELAY_PIN"          : 15,
    "RELAY_ACTIVE_MS"    : 2000,

    "OLED_ENABLED"       : true,
    "OLED_SCL_PIN"       : 15,
    "OLED_SDA_PIN"       : 14,
    "OLED_WIDTH"         : 128,
    "OLED_HEIGHT"        : 64,
    "OLED_ADDR"          : 60,

    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}

Se config.json estiver ausente/inválido, boot.py já intercepta antes deste
arquivo rodar e sobe o portal de provisionamento (AP
AccessNG-BitDogLab-XXXX em 192.168.4.1) - este arquivo sempre roda com um
config.json válido.

--- Tópicos MQTT ---------------------------------------------------------

  Publica:
    access-ng/coldstart/{mac}                → boot do dispositivo
    access-ng/heartbeat/{mac}                → presença periódica
    access-ng/{amb_id}/caronte/{mac}/tag     → TAG do botão local

  Assina:
    access-ng/coldstart/{mac}/result          → resposta do coldstart
    access-ng/{amb_id}/cerberos/{mac}/command → comando de abertura/check_update
    access-ng/{amb_id}/caronte/{mac}/result   → resultado da autenticação

  O MAC usa '-' no lugar de ':' nos tópicos. Na BitDogLab, o OLED usa
  SCL=15 e SDA=14 - se RELAY_PIN também for 15 ou 14, o relé é desativado
  automaticamente para não conflitar com o display.

--- OTA (atualização remota) --------------------------------------------------

  Mesmo esquema do Caronte/FECHO/Cerberos enxuto - accessng.ota.
  check_for_update()/apply_update()/confirm_boot_ok(). Rede de segurança:
  se a versão nova não completar um coldstart com sucesso em até 3 boots,
  boot.py restaura automaticamente main.bak e reinicia - dispara para
  QUALQUER crash-loop, não só update pendente (ver accessng/recovery.py).
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

_CONFIG_RUNTIME_KEYS = ("HEARTBEAT_INTERVAL", "OTA_CHECK_INTERVAL", "OTA_ENABLED", "BUTTON_TAG")

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
BUTTON_PIN         = cfg("BUTTON_PIN")
BUTTON_DEBOUNCE_MS = cfg("BUTTON_DEBOUNCE_MS")
BUTTON_TAG         = cfg("BUTTON_TAG")
LED_RED_PIN        = cfg("LED_RED_PIN")
LED_GREEN_PIN      = cfg("LED_GREEN_PIN")
LED_BLUE_PIN       = cfg("LED_BLUE_PIN")
RELAY_PIN          = cfg("RELAY_PIN")
RELAY_ACTIVE_MS    = cfg("RELAY_ACTIVE_MS")
OLED_ENABLED       = cfg("OLED_ENABLED")
OLED_SCL_PIN       = cfg("OLED_SCL_PIN")
OLED_SDA_PIN       = cfg("OLED_SDA_PIN")
OLED_WIDTH         = cfg("OLED_WIDTH")
OLED_HEIGHT        = cfg("OLED_HEIGHT")
OLED_ADDR          = cfg("OLED_ADDR")
OTA_ENABLED        = cfg("OTA_ENABLED")
OTA_CHECK_INTERVAL = cfg("OTA_CHECK_INTERVAL")

MQTT_PREFIX = "access-ng"
DEVICE_MAC  = None
AMBIENTE_ID = None
BOOT_COUNT  = None

# --- OTA -----------------------------------------------------------------

FIRMWARE_VERSAO   = "1.3.27"  # bump manual a cada release publicada - alinhar
                               # com version.json (ver comentário equivalente
                               # em Autenticador/main.py)
OTA_VERSION_PATH  = "Hardware/Fechadura/version.json"
OTA_FIRMWARE_PATH = "Hardware/Fechadura/main_bitdoglab.py"
OTA_HOST          = "laica.ifrn.edu.br"
OTA_PORT          = 80

# Bibliotecas que este firmware precisa - accessng.ota.ensure_dependencies()
# busca as que ainda não existirem localmente. Mesma lista declarada em
# Hardware/Fechadura/version.json, campo "bibliotecas".
BIBLIOTECAS = ["umqtt/simple.py", "umqtt/robust.py", "ssd1306.py"]

# --- DIAGNÓSTICO -----------------------------------------------------------

# "(boot.py)" é um marcador deliberado - ver o mesmo em Autenticador/main.py
# e nos outros dois Cerberos já portados: migrador e main.py definitivo têm
# FIRMWARE_VERSAO independentes, então esse sufixo em Cerberos.hardware é
# o sinal confiável no painel admin de que a migração deu certo.
HARDWARE_INFO         = "BitDogLab V6 (Pico W) (boot.py)"
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
    """Sensor interno de temperatura do RP2040 (ADC canal 4)."""
    try:
        sensor = machine.ADC(4)
        volts = sensor.read_u16() * (3.3 / 65535)
        return round(27 - (volts - 0.706) / 0.001721, 1)
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
    diagnóstico de reconexão (usado no heartbeat) e o feedback visual
    (display), específicos desta aplicação, que accessng.wifi não conhece."""
    global _wifi_reconnects, _wifi_last_reconnect_s, _wifi_last_disconnect_status
    if network.WLAN(network.STA_IF).isconnected():
        return True
    if _wifi_last_reconnect_s is not None:
        _wifi_last_disconnect_status = _read_wifi_status()
        _wifi_reconnects += 1
    _wifi_last_reconnect_s = time.time()
    print("[WiFi] Conectando em %s..." % WIFI_SSID)
    display_message("WIFI", "Conectando", WIFI_SSID)
    ok = wifi.try_connect_once(WIFI_SSID, WIFI_PASS)
    if ok:
        display_message("WIFI", "Conectado", network.WLAN(network.STA_IF).ifconfig()[0])
    else:
        display_message("WIFI", "Falha", "verifique rede")
    return ok


# --- HARDWARE ----------------------------------------------------------------

button    = None
led_r     = None
led_g     = None
led_b     = None
relay     = None
oled      = None
oled_ok   = False
_btn_flag = False


def _on_button(_):
    global _btn_flag
    _btn_flag = True


def init_gpio():
    global button, led_r, led_g, led_b, relay
    button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    button.irq(trigger=machine.Pin.IRQ_FALLING, handler=_on_button)
    led_r = machine.PWM(machine.Pin(LED_RED_PIN));   led_r.freq(1000)
    led_g = machine.PWM(machine.Pin(LED_GREEN_PIN)); led_g.freq(1000)
    led_b = machine.PWM(machine.Pin(LED_BLUE_PIN));  led_b.freq(1000)
    if oled_ok and RELAY_PIN in (OLED_SCL_PIN, OLED_SDA_PIN):
        relay = None
        print("[GPIO] RELAY_PIN %s conflita com OLED; relé desativado" % RELAY_PIN)
    else:
        relay = machine.Pin(RELAY_PIN, machine.Pin.OUT, value=0)
    _led(0, 0, 0)
    print("[GPIO] Inicializado")


def _led(r, g, b):
    led_r.duty_u16(r * 257)
    led_g.duty_u16(g * 257)
    led_b.duty_u16(b * 257)


def _pulse(r, g, b, ms):
    _led(r, g, b); time.sleep_ms(ms); _led(0, 0, 0)


def led_ok():     _pulse(0, 255, 0, 400)
def led_denied(): _pulse(255, 0, 0, 1000)


def unlock_door():
    print("[Lock] Abrindo porta...")
    display_message("PORTA", "Abrindo", "aguarde...")
    for _ in range(3):
        _led(0, 0, 255); time.sleep_ms(200)
        _led(0, 0, 0);   time.sleep_ms(100)
    if relay is not None:
        relay.value(1)
        time.sleep_ms(RELAY_ACTIVE_MS)
        relay.value(0)
    else:
        time.sleep_ms(RELAY_ACTIVE_MS)
    print("[Lock] Porta fechada")
    display_message("PORTA", "Fechada", "Sistema pronto")


# --- DISPLAY OLED --------------------------------------------------------------

def init_display():
    global oled, oled_ok
    if not OLED_ENABLED:
        return False
    try:
        from machine import Pin, SoftI2C
        from ssd1306 import SSD1306_I2C
        i2c = SoftI2C(scl=Pin(OLED_SCL_PIN), sda=Pin(OLED_SDA_PIN), timeout=50000)
        oled = SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c, addr=OLED_ADDR)
        oled_ok = True
        display_message("ACCESS-NG", "Cerberos", "Iniciando...")
        print("[OLED] Inicializado")
        return True
    except Exception as e:
        oled = None
        oled_ok = False
        print("[OLED] Indisponível: %s" % e)
        return False


def _wrap_display_line(text, width=16):
    text = str(text)
    lines = []
    for raw in text.split("\n"):
        words = raw.split(" ")
        line = ""
        for word in words:
            if not word:
                continue
            if len(word) > width:
                if line:
                    lines.append(line)
                    line = ""
                while len(word) > width:
                    lines.append(word[:width])
                    word = word[width:]
            candidate = word if not line else line + " " + word
            if len(candidate) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def display_message(title, *lines):
    if not oled_ok or oled is None:
        return
    try:
        oled.fill(0)
        oled.text(str(title)[:16], 0, 0)
        oled.hline(0, 10, OLED_WIDTH, 1)
        y = 16
        for line in lines:
            for wrapped in _wrap_display_line(line):
                if y > OLED_HEIGHT - 8:
                    break
                oled.text(wrapped[:16], 0, y)
                y += 10
            if y > OLED_HEIGHT - 8:
                break
        oled.show()
    except Exception as e:
        print("[OLED] Falha ao atualizar: %s" % e)


# --- DIAGNÓSTICO DE REDE (ping/broker) ------------------------------------------

def _icmp_checksum(data):
    if len(data) % 2:
        data += b'\x00'
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    total = (total >> 16) + (total & 0xffff)
    total += total >> 16
    return ~total & 0xffff


def ping_gateway(count=4, timeout_ms=1000):
    """Envia pings ICMP ao gateway padrao da rede WiFi. Alguns servidores
    bloqueiam ICMP mesmo com MQTT/TCP funcionando - pingar o gateway testa
    o enlace local sem confundir isso com bloqueio de ping no broker."""
    import ustruct

    try:
        wlan = network.WLAN(network.STA_IF)
        host_ip = wlan.ifconfig()[2]
        if not host_ip or host_ip == "0.0.0.0":
            print("[Ping] Gateway padrao indisponivel - pulando ping")
            return
    except Exception as e:
        print("[Ping] Falha ao obter gateway padrao: %s" % e)
        return

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, 1)  # 1 = ICMP
    except Exception as e:
        print("[Ping] Raw socket indisponível (%s) - pulando ping" % e)
        return

    s.settimeout(timeout_ms / 1000)
    pkt_id = time.ticks_us() & 0xFFFF
    ok = 0

    for seq in range(1, count + 1):
        payload = b'access-ng' + bytes(range(32))
        header = ustruct.pack('!BBHHH', 8, 0, 0, pkt_id, seq)
        chksum = _icmp_checksum(header + payload)
        header = ustruct.pack('!BBHHH', 8, 0, chksum, pkt_id, seq)

        t0 = time.ticks_ms()
        try:
            s.sendto(header + payload, (host_ip, 1))
            while True:
                resp = s.recv(1024)
                ihl = (resp[0] & 0x0F) * 4
                r_type, _, _, r_id, r_seq = ustruct.unpack('!BBHHH', resp[ihl:ihl + 8])
                if r_type == 0 and r_id == pkt_id and r_seq == seq:
                    dt = time.ticks_diff(time.ticks_ms(), t0)
                    print("[Ping] %s: seq=%d tempo=%dms" % (host_ip, seq, dt))
                    ok += 1
                    break
        except OSError:
            print("[Ping] %s: seq=%d timeout" % (host_ip, seq))
        time.sleep_ms(200)

    s.close()
    print("[Ping] gateway %s: %d/%d respostas" % (host_ip, ok, count))


def _diag_broker():
    """Resolve o host e testa um socket TCP cru, para diferenciar falha de
    DNS de bloqueio/recusa de conexão pela rede ou pelo broker."""
    try:
        addr = socket.getaddrinfo(MQTT_BROKER, MQTT_PORT)[0][-1]
        print("[Diag] %s -> %s" % (MQTT_BROKER, addr))
    except Exception as e:
        print("[Diag] Falha ao resolver %s: %s" % (MQTT_BROKER, e))
        return
    try:
        s = socket.socket()
        s.connect(addr)
        s.close()
        print("[Diag] Socket TCP cru conectou OK")
    except Exception as e:
        print("[Diag] Socket TCP cru falhou: %s" % e)


# --- OTA -----------------------------------------------------------------

def ota_check_and_maybe_apply(state):
    """Verifica e aplica update do firmware em si; se não havia (ou não
    aplicou), também checa o pacote accessng/ (versionado à parte - ver
    accessng/ota.py). Um update de firmware bem sucedido já reinicia e
    não devolve o controle pra esta função."""
    remote = ota.check_for_update(OTA_HOST, OTA_PORT, OTA_VERSION_PATH,
                                   FIRMWARE_VERSAO, OTA_ENABLED)
    if remote:
        display_message("OTA", "Baixando", remote.get("versao", "?"))
        if ota.apply_update(state, OTA_HOST, OTA_PORT, OTA_FIRMWARE_PATH, remote,
                             target_file="main.py", backup_file="main.bak"):
            config.save_state(state)
            display_message("OTA", "Atualizado", state.get("current_version", "?"))
            time.sleep(1)
            _soft_reset()
        else:
            display_message("OTA", "Falha download", "mantendo atual")
        return

    pkg_remote = ota.check_for_package_update(OTA_HOST, OTA_PORT,
                                               state.get("accessng_version"), OTA_ENABLED)
    if pkg_remote:
        display_message("OTA", "Pacote accessng", pkg_remote.get("versao", "?"))
        if ota.apply_package_update(state, OTA_HOST, OTA_PORT, pkg_remote, BIBLIOTECAS):
            config.save_state(state)
            display_message("OTA", "Pacote atualizado", state.get("accessng_version", "?"))
            time.sleep(1)
            _soft_reset()
        else:
            display_message("OTA", "Falha pacote", "mantendo atual")


# --- MQTT --------------------------------------------------------------------

_client           = None
_unlock_flag      = False
_update_requested = False
_coldstart_result = None


def _mac_safe():
    return DEVICE_MAC.replace(":", "-")


def _t():
    mac = _mac_safe()
    p = MQTT_PREFIX
    topics = {
        "coldstart"       : "%s/coldstart/%s" % (p, mac),
        "coldstart_result": "%s/coldstart/%s/result" % (p, mac),
        "heartbeat"       : "%s/heartbeat/%s" % (p, mac),
    }
    if AMBIENTE_ID is not None:
        amb = str(AMBIENTE_ID)
        topics["tag"] = "%s/%s/caronte/%s/tag" % (p, amb, mac)
        topics["result"] = "%s/%s/caronte/%s/result" % (p, amb, mac)
        topics["command"] = "%s/%s/cerberos/%s/command" % (p, amb, mac)
        topics["config_result"] = "%s/%s/cerberos/%s/config/result" % (p, amb, mac)
    return topics


def _publish_config():
    params = {}
    for key in DEFAULTS:
        persistido = key in _cfg_file
        if key in SENSITIVE_KEYS:
            params[key] = {"persistido": persistido}
        else:
            params[key] = {"valor": globals().get(key, DEFAULTS[key]), "persistido": persistido}
    topic = _t().get("config_result")
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
        display_message("CONFIG", "Erro ao gravar", str(e)[:16])
        return
    print("[Config] Novos parametros gravados, reiniciando:", list(validos.keys()))
    display_message("CONFIG", "Config gravada", "reiniciando...")
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
    global _unlock_flag, _update_requested, _coldstart_result
    topic_str = topic.decode("utf-8")
    topics = _t()
    try:
        data = json.loads(payload)
    except Exception:
        return

    if topic_str == topics["coldstart_result"]:
        _coldstart_result = data

    elif topic_str == topics.get("command"):
        if data.get("command") == "unlock":
            print("[MQTT] Comando de abertura recebido!")
            display_message("COMANDO", "Abertura", "recebida")
            _unlock_flag = True
        elif data.get("command") == "check_update":
            print("[MQTT] Solicitação de verificação de atualização recebida")
            _update_requested = True
        elif data.get("command") == "reboot":
            print("[MQTT] Comando de reinício recebido - reiniciando...")
            display_message("COMANDO", "Reiniciando", "aguarde...")
            time.sleep_ms(300)
            _soft_reset()
        elif data.get("command") == "get_config":
            print("[MQTT] Solicitação de configuração recebida")
            _publish_config()
        elif data.get("command") == "set_config":
            _apply_set_config(data.get("params"))

    elif topic_str == topics.get("result"):
        if data.get("allow"):
            print("[MQTT] Acesso autorizado!")
            display_message("ACESSO", "Autorizado", "abrindo porta")
            unlock_door()
        else:
            print("[MQTT] Acesso negado: %s" % data.get("motivo", ""))
            display_message("ACESSO", "Negado", data.get("motivo", ""))
            led_denied()


def mqtt_connect():
    global _client
    # Nota: esta variante prefere umqtt.robust (ao contrário do Caronte/
    # FECHO/Enxuto, que preferem umqtt.simple - ver comentário equivalente
    # nesses arquivos) - comportamento preservado do firmware original,
    # não alterado nesta migração de arquitetura.
    try:
        from umqtt.robust import MQTTClient
    except ImportError:
        from umqtt.simple import MQTTClient

    kwargs = {"port": MQTT_PORT, "keepalive": 90}
    if MQTT_USER:
        kwargs["user"] = MQTT_USER
        kwargs["password"] = MQTT_PASS
    if MQTT_TLS:
        kwargs["ssl"] = True

    c = MQTTClient("cerberos-%s" % _mac_safe(), MQTT_BROKER, **kwargs)
    c.set_callback(_on_message)
    c.connect()
    c.subscribe(_t()["coldstart_result"])
    _client = c
    print("[MQTT] Conectado ao broker %s:%s" % (MQTT_BROKER, MQTT_PORT))
    display_message("MQTT", "Conectado", MQTT_BROKER)


def do_coldstart():
    global AMBIENTE_ID, _coldstart_result
    while True:
        _coldstart_result = None
        try:
            _client.publish(_t()["coldstart"],
                             json.dumps({"mac": DEVICE_MAC, "chave": DEVICE_KEY,
                                         "versao": FIRMWARE_VERSAO,
                                         "boot_count": BOOT_COUNT, "hardware": HARDWARE_INFO,
                                         "mcu": _read_mcu(), "ssid": WIFI_SSID}),
                             qos=1)
            print("[MQTT] Coldstart publicado, aguardando confirmação...")
            display_message("COLDSTART", "Publicado", "aguardando...")

            t0 = time.time()
            while time.time() - t0 < 5:
                _client.check_msg()
                if _coldstart_result is not None:
                    break
                time.sleep_ms(100)
        except OSError as e:
            print("[MQTT] Erro de rede no coldstart: %s - reconectando..." % e)
            display_message("MQTT", "Erro de rede", "reconectando")
            try:
                mqtt_connect()
            except Exception:
                pass
            time.sleep(5)
            continue

        if _coldstart_result and _coldstart_result.get("status") == "ok":
            AMBIENTE_ID = _coldstart_result.get("ambiente_id")
            _apply_session_config(_coldstart_result.get("config"))
            topics = _t()
            _client.subscribe(topics["command"])
            _client.subscribe(topics["result"])
            print("[MQTT] Coldstart OK - ambiente_id=%s" % AMBIENTE_ID)
            display_message("COLDSTART OK", "Ambiente %s" % AMBIENTE_ID, "Sistema pronto")
            led_ok()
            return

        print("[MQTT] Coldstart negado/sem resposta (%s) - tentando em 15s..." %
              _coldstart_result)
        display_message("COLDSTART", "Sem resposta", "tentando em 15s")
        led_denied()
        time.sleep(15)


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
    _client.publish(_t()["heartbeat"], json.dumps(payload))


def publish_tag():
    print("[MQTT] Publicando TAG do botão...")
    display_message("BOTAO", "Autenticando", BUTTON_TAG)
    _client.publish(_t()["tag"], json.dumps({
        "tag"  : BUTTON_TAG,
        "chave": DEVICE_KEY,
        "mac"  : DEVICE_MAC,
    }))


# --- MAIN --------------------------------------------------------------------

def main():
    global DEVICE_MAC, BOOT_COUNT, _btn_flag, _unlock_flag, _update_requested

    print("\n" + "=" * 48)
    print("  CERBEROS + CARONTE - BitDogLab V6 (MQTT)")
    print("=" * 48)

    state = config.load_state()
    BOOT_COUNT = _read_boot_count()
    # init_display() ANTES de init_gpio(): a checagem de conflito
    # RELAY_PIN/OLED_SCL_PIN/OLED_SDA_PIN em init_gpio() depende de
    # oled_ok já estar definido (mesma ordem do firmware original).
    init_display()
    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config("mac"), ":").decode()
    print("[Device] MAC: %s" % DEVICE_MAC)
    display_message("DISPOSITIVO", "MAC", DEVICE_MAC)

    # Na prática, boot.py já deixou o rádio conectado - este laço serve de
    # rede de segurança caso algo tenha mudado entre um passo e outro.
    while not connect_wifi():
        watchdog.feed()
        led_denied()
        time.sleep(10)

    ping_gateway()

    ota.ensure_dependencies(OTA_HOST, OTA_PORT, BIBLIOTECAS)

    tentativas_mqtt = 0
    while True:
        watchdog.feed()
        _diag_broker()
        try:
            display_message("MQTT", "Conectando", MQTT_BROKER)
            mqtt_connect()
            break
        except Exception as e:
            tentativas_mqtt += 1
            print("[MQTT] Falha na conexão: %s (%d/5) - tentando em 10s..." %
                  (e, tentativas_mqtt))
            display_message("MQTT", "Falha conexao", "tentando em 10s")
            led_denied()
            time.sleep(10)
            if tentativas_mqtt >= 5:
                print("[MQTT] Sem sucesso após 5 tentativas - reiniciando")
                _soft_reset()

    do_coldstart()
    last_heartbeat = time.time()
    ota.confirm_boot_ok(state, FIRMWARE_VERSAO)
    config.save_state(state)
    print("[Main] Operacional\n")
    display_message("ACCESS-NG", "Operacional", "Ambiente %s" % AMBIENTE_ID)

    last_ota_check = time.time()
    ota_check_and_maybe_apply(state)

    while True:
        watchdog.feed()
        try:
            if not network.WLAN(network.STA_IF).isconnected():
                print("[WiFi] Reconectando...")
                display_message("WIFI", "Reconectando", WIFI_SSID)
                if connect_wifi():
                    mqtt_connect()
                    do_coldstart()
                    last_heartbeat = time.time()
                else:
                    time.sleep(5)
                    continue

            if _btn_flag:
                _btn_flag = False
                time.sleep_ms(BUTTON_DEBOUNCE_MS)
                publish_tag()

            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = time.time()

            if OTA_ENABLED and time.time() - last_ota_check >= OTA_CHECK_INTERVAL:
                ota_check_and_maybe_apply(state)
                last_ota_check = time.time()

            _client.check_msg()

            if _unlock_flag:
                _unlock_flag = False
                unlock_door()

            if _update_requested:
                _update_requested = False
                ota_check_and_maybe_apply(state)
                last_ota_check = time.time()

            time.sleep_ms(50)

        except OSError as e:
            print("[MQTT] Erro de rede: %s - reconectando..." % e)
            display_message("MQTT", "Erro de rede", "reconectando")
            try:
                mqtt_connect()
                do_coldstart()
                last_heartbeat = time.time()
            except Exception:
                time.sleep(5)
        except Exception as e:
            print("[Main] Erro: %s" % e)
            display_message("ERRO", "Loop principal", str(e))
            time.sleep(1)


main()
