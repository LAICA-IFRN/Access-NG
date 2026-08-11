"""
Cerberos ESP32-C3 (FECHO) - MicroPython MQTT + UART

Firmware para o Cerberos ESP32-C3 dedicado a abrir a fechadura, apelidado de
"FECHO" pela equipe de hardware. Roda na mesma placa ESP32-C3 do Caronte
(Hardware/Autenticador/main.py), só que cumprindo o papel de fechadura:
LEDs de feedback, relé da tranca e display OLED opcional.

--- Arquivos no dispositivo ----------------------------------------------

  boot.py             -> supervisor mínimo (era boot_esp32c3.py no repo)
  device_defaults.py  -> DEFAULTS/SENSITIVE_KEYS (era device_defaults_esp32c3.py)
  main.py             -> este arquivo (era main_esp32c3.py no repo)
  accessng/           -> pacote compartilhado (config/wifi/recovery/
                          provisioning/ota/watchdog), instalado uma vez,
                          não atualizado por OTA nesta fase
  bibliotecas/         -> vendorizadas (umqtt, sh1106), buscadas
                          automaticamente por accessng.ota.
                          ensure_dependencies() na primeira vez que
                          faltarem

Continua respondendo a comandos remotos via MQTT (portal web), e opcionalmente
recebe pedidos de liberação vindos do Caronte via UART - ver seção UART
abaixo.

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

    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "RELAY_PIN"          : 6,
    "RELAY_ACTIVE_MS"    : 2000,
    "RELAY_COOLDOWN_MS"  : 3000,

    "UART_ENABLED"       : false,
    "UART_ID"            : 1,
    "UART_TX_PIN"        : 21,
    "UART_RX_PIN"        : 20,
    "UART_BAUDRATE"      : 9600,

    "OLED_ENABLED"       : true,
    "OLED_SCL_PIN"       : 7,
    "OLED_SDA_PIN"       : 8,
    "OLED_WIDTH"         : 128,
    "OLED_HEIGHT"        : 64,
    "OLED_ADDR"          : 60,

    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}

Se config.json estiver ausente/inválido, boot.py já intercepta antes deste
arquivo rodar e sobe o portal de provisionamento (AP AccessNG-FECHO-XXXX
em 192.168.4.1) - este arquivo sempre roda com um config.json válido.

--- Pinagem ESP32-C3 (FECHO) --------------------------------------------------

  Conforme pinagem definida pela equipe de hardware para o módulo FECHO:

  GPIO 01 -> LED VM  (vermelho)  - feedback de acesso negado
  GPIO 02 -> LED VD3 (verde 3)   - pulso de atividade (heartbeat/tráfego)
  GPIO 03 -> LED VD2 (verde 2)   - link WiFi+MQTT ok (aceso fixo)
  GPIO 04 -> LED VD1 (verde 1)   - feedback de acesso permitido
  GPIO 05 -> Botão PROG (ativo baixo) - reservado para modo AP/provisionamento
             físico; não usado nesta fase (gatilho de recovery é só por
             software - ver docstring de accessng/recovery.py).
  GPIO 06 -> Relé da tranca (ativo alto)
  GPIO 07 -> SCL display OLED (SH1106 I2C, 128x64)
  GPIO 08 -> SDA display OLED (SH1106 I2C, 128x64)
  GPIO 20 -> RX UART (link com o Caronte) - mesma pinagem RS485/UART do
             Caronte ESP32-C3; conectar TX<->RX cruzado entre os dois módulos.
  GPIO 21 -> TX UART (link com o Caronte)

  Atenção (equipe de hardware): não acionar o relé por muito tempo, risco de
  queima da solenóide. RELAY_ACTIVE_MS é sempre limitado a 2000ms no código
  independente do que vier em config.json, e RELAY_COOLDOWN_MS impõe um
  intervalo mínimo entre acionamentos.

--- Display OLED ---------------------------------------------------------------

  OLED_ENABLED (bool, default true) tenta inicializar um display SH1106 I2C
  128x64. Se o display não responder, o firmware loga "[OLED] Indisponível"
  e segue operando normalmente sem display. Driver em bibliotecas/sh1106.py
  no repo, instalado como /sh1106.py no dispositivo (accessng.ota.
  ensure_dependencies() cuida disso).

--- UART (comunicação com o Caronte) -------------------------------------------

  UART_ENABLED (bool, default false) liga o link serial com o Caronte.

  Protocolo homologado com a equipe de hardware:

    7E LEN CMD [dados] CS

    CS fecha a soma de (LEN+CMD+dados+CS) em 0 mod 256 (complemento de 2).

    1. KEEP-ALIVE (Caronte -> FECHO)  : 7E 01 01 FE
    2. ACK (FECHO -> Caronte)         : 7E 01 13 EC
    3. PERMITIDO (FECHO -> Caronte)   : 7E 01 02 FD
    4. NEGADO (FECHO -> Caronte)      : 7E 01 03 FC
    5. ENVIO DE TAG (Caronte -> FECHO): 7E 06 04 [4B TAG] [0x1A ou 0x22] [CS]
       O FECHO não valida a TAG contra lista alguma - quem decide é o
       Caronte; o FECHO só tenta liberar a tranca (respeitando o cooldown).

  Cada TAG recebida via UART também é publicada em
  access-ng/{amb_id}/cerberos/{mac}/uart_tag para auditoria no servidor.

--- OTA (atualização remota) --------------------------------------------------

  Mesmo esquema do Caronte - accessng.ota.check_for_update()/apply_update()/
  confirm_boot_ok(). Rede de segurança: se a versão nova não completar um
  coldstart com sucesso em até 3 boots, boot.py restaura automaticamente
  main.bak e reinicia - dispara para QUALQUER crash-loop, não só update
  pendente (ver accessng/recovery.py).
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
LED_VM_PIN         = cfg("LED_VM_PIN")
LED_VD1_PIN        = cfg("LED_VD1_PIN")
LED_VD2_PIN        = cfg("LED_VD2_PIN")
LED_VD3_PIN        = cfg("LED_VD3_PIN")
RELAY_PIN          = cfg("RELAY_PIN")
RELAY_ACTIVE_MS    = min(cfg("RELAY_ACTIVE_MS"), 2000)
RELAY_COOLDOWN_MS  = cfg("RELAY_COOLDOWN_MS")
UART_ENABLED       = cfg("UART_ENABLED")
UART_ID            = cfg("UART_ID")
UART_TX_PIN        = cfg("UART_TX_PIN")
UART_RX_PIN        = cfg("UART_RX_PIN")
UART_BAUDRATE      = cfg("UART_BAUDRATE")
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

FIRMWARE_VERSAO   = "1.1.3"   # bump manual a cada release publicada - alinhar
                               # com version_esp32c3.json (ver comentário
                               # equivalente em Autenticador/main.py)
OTA_VERSION_PATH  = "Hardware/Fechadura/version_esp32c3.json"
OTA_FIRMWARE_PATH = "Hardware/Fechadura/main_esp32c3.py"
OTA_HOST          = "laica.ifrn.edu.br"
OTA_PORT          = 80

# Bibliotecas que este firmware precisa - accessng.ota.ensure_dependencies()
# busca as que ainda não existirem localmente. Mesma lista declarada em
# Hardware/Fechadura/version_esp32c3.json, campo "bibliotecas".
BIBLIOTECAS = ["umqtt/simple.py", "umqtt/robust.py", "sh1106.py"]

# --- DIAGNÓSTICO -----------------------------------------------------------

# "(boot.py)" é um marcador deliberado - ver o mesmo em Autenticador/main.py:
# migrador e main.py definitivo têm FIRMWARE_VERSAO independentes, então
# esse sufixo em Cerberos.hardware é o sinal confiável no painel admin de
# que a migração deu certo.
HARDWARE_INFO         = "Cerberos ESP32-C3 (FECHO) (boot.py)"
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
    diagnóstico de reconexão (usado no heartbeat) e o feedback visual
    (display/LED) específicos desta aplicação, que accessng.wifi não
    conhece."""
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
        _set_link(True)
        display_message("WIFI", "Conectado", network.WLAN(network.STA_IF).ifconfig()[0])
    else:
        _set_link(False)
        display_message("WIFI", "Falha", "verifique rede")
    return ok


# --- HARDWARE ----------------------------------------------------------------

led_vm  = None
led_vd1 = None
led_vd2 = None
led_vd3 = None
relay   = None
oled    = None
oled_ok = False

_last_unlock_ms = None


def init_gpio():
    global led_vm, led_vd1, led_vd2, led_vd3, relay
    led_vm  = machine.Pin(LED_VM_PIN,  machine.Pin.OUT, value=0)
    led_vd1 = machine.Pin(LED_VD1_PIN, machine.Pin.OUT, value=0)
    led_vd2 = machine.Pin(LED_VD2_PIN, machine.Pin.OUT, value=0)
    led_vd3 = machine.Pin(LED_VD3_PIN, machine.Pin.OUT, value=0)
    relay   = machine.Pin(RELAY_PIN,   machine.Pin.OUT, value=0)
    print("[GPIO] Inicializado")


def _set_link(ok):
    led_vd2.value(1 if ok else 0)


def status_pulse(ms=80):
    led_vd3.value(1)
    time.sleep_ms(ms)
    led_vd3.value(0)


def feedback_permitido():
    led_vd1.value(1)
    time.sleep_ms(300)
    led_vd1.value(0)


def feedback_negado():
    led_vm.value(1)
    time.sleep_ms(300)
    led_vm.value(0)


def unlock_door(source="remote"):
    """Aciona o relé por RELAY_ACTIVE_MS (sempre limitado a 2000ms).
    Recusa se RELAY_COOLDOWN_MS ainda não decorreu desde o último
    acionamento (proteção da solenóide). Retorna True se abriu, False se
    recusado por cooldown."""
    global _last_unlock_ms
    now = time.ticks_ms()
    if _last_unlock_ms is not None and time.ticks_diff(now, _last_unlock_ms) < RELAY_COOLDOWN_MS:
        print("[Lock] Acionamento recusado (%s) - cooldown da solenóide" % source)
        display_message("TRANCA", "Recusado", "aguarde solenoide")
        return False

    print("[Lock] Abrindo porta (%s)..." % source)
    display_message("TRANCA", "Abrindo", source)
    _last_unlock_ms = now
    relay.value(1)
    time.sleep_ms(RELAY_ACTIVE_MS)
    relay.value(0)
    print("[Lock] Porta fechada")
    display_message("TRANCA", "Fechada", "Sistema pronto")
    return True


# --- DISPLAY OLED --------------------------------------------------------------

def init_display():
    """Tenta inicializar o display SH1106. Se não houver display
    fisicamente presente (ou falhar por qualquer motivo), loga e segue
    sem display - display_message() vira no-op em oled_ok=False."""
    global oled, oled_ok
    if not OLED_ENABLED:
        return False
    try:
        from machine import Pin, SoftI2C
        from sh1106 import SH1106_I2C
        i2c = SoftI2C(scl=Pin(OLED_SCL_PIN), sda=Pin(OLED_SDA_PIN), timeout=50000)
        oled = SH1106_I2C(OLED_WIDTH, OLED_HEIGHT, i2c, res=None, addr=OLED_ADDR)
        oled_ok = True
        display_message("ACCESS-NG", "FECHO", "Iniciando...")
        print("[OLED] Inicializado (SH1106)")
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


# --- UART / Protocolo FECHO ---------------------------------------------------

_UART_STX            = 0x7E
_UART_CMD_KEEPALIVE  = 0x01
_UART_CMD_PERMITIDO  = 0x02
_UART_CMD_NEGADO     = 0x03
_UART_CMD_TAG        = 0x04
_UART_CMD_ACK        = 0x13

uart         = None
_uart_rx_buf = bytearray()


def init_uart():
    global uart
    if not UART_ENABLED:
        return
    uart = machine.UART(UART_ID, baudrate=UART_BAUDRATE,
                         tx=machine.Pin(UART_TX_PIN), rx=machine.Pin(UART_RX_PIN))
    print("[UART] Inicializado (id=%d, tx=%d, rx=%d, baud=%d)" %
          (UART_ID, UART_TX_PIN, UART_RX_PIN, UART_BAUDRATE))


def _uart_checksum(body):
    return (-sum(body)) & 0xFF


def _uart_build_frame(cmd, data=b""):
    body = bytes([len(data) + 1, cmd]) + data
    return bytes([_UART_STX]) + body + bytes([_uart_checksum(body)])


def _uart_send(cmd, data=b""):
    if uart is None:
        return
    uart.write(_uart_build_frame(cmd, data))


def _uart_read_frame():
    global _uart_rx_buf
    if uart is None:
        return None
    if uart.any():
        _uart_rx_buf += uart.read(uart.any())

    while _uart_rx_buf:
        if _uart_rx_buf[0] != _UART_STX:
            del _uart_rx_buf[0]
            continue
        if len(_uart_rx_buf) < 2:
            return None
        length = _uart_rx_buf[1]
        frame_len = length + 3
        if len(_uart_rx_buf) < frame_len:
            return None
        frame = bytes(_uart_rx_buf[:frame_len])
        del _uart_rx_buf[:frame_len]
        body = frame[1:2 + length]
        cs   = frame[frame_len - 1]
        if (sum(body) + cs) & 0xFF != 0:
            print("[UART] Quadro com checksum inválido, descartado")
            continue
        return frame[2], frame[3:2 + length]
    return None


def uart_poll():
    """Processa quadros pendentes do Caronte: responde KEEP-ALIVE com ACK
    e TAG com PERMITIDO/NEGADO (após tentar abrir a porta). Publica a TAG
    recebida em /uart_tag para auditoria (melhor esforço)."""
    if not UART_ENABLED:
        return
    frame = _uart_read_frame()
    while frame is not None:
        cmd, data = frame
        if cmd == _UART_CMD_KEEPALIVE:
            _uart_send(_UART_CMD_ACK)
        elif cmd == _UART_CMD_TAG and len(data) == 5:
            tag = ubinascii.hexlify(data[:4]).decode("utf-8").upper()
            tipo = data[4]
            display_message("UART", "TAG recebida", tag)
            allowed = unlock_door("uart")
            _uart_send(_UART_CMD_PERMITIDO if allowed else _UART_CMD_NEGADO)
            if allowed:
                feedback_permitido()
            else:
                feedback_negado()
            publish_uart_tag(tag, tipo, allowed)
        frame = _uart_read_frame()


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

_client = None
_coldstart_result = None
_update_requested = False


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
        topics["uart_tag"] = "%s/%s/cerberos/%s/uart_tag" % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
        topics["config_result"] = "%s/%s/cerberos/%s/config/result" % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
    return topics


def publish_uart_tag(tag, tipo, allowed):
    topic = _topics().get("uart_tag")
    if topic is None or _client is None:
        return
    try:
        _client.publish(topic, json.dumps({
            "mac": DEVICE_MAC, "tag": tag, "tipo": tipo, "allow": allowed,
        }))
    except OSError:
        pass


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
    global _coldstart_result, _update_requested
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
            allowed = unlock_door("mqtt")
            if allowed:
                feedback_permitido()
            else:
                feedback_negado()
        elif data.get("command") == "check_update":
            print("[MQTT] Solicitação de verificação de atualização recebida")
            _update_requested = True
        elif data.get("command") == "reboot":
            print("[MQTT] Comando de reinício recebido - reiniciando...")
            display_message("COMANDO", "Reiniciando", "aguarde...")
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

    client = MQTTClient("cerberos-c3-%s" % _mac_safe(), MQTT_BROKER, **kwargs)
    client.set_callback(_on_message)
    client.connect()
    client.subscribe(_topics()["coldstart_result"])
    _client = client
    print("[MQTT] Conectado ao broker %s:%s" % (MQTT_BROKER, MQTT_PORT))
    display_message("MQTT", "Conectado", MQTT_BROKER)


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
            display_message("COLDSTART", "Publicado", "aguardando...")

            t0 = time.time()
            tick = 0
            while time.time() - t0 < 5:
                _client.check_msg()
                if tick % 5 == 0:
                    led_vd2.value(1 - led_vd2.value())
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
            display_message("COLDSTART OK", "Ambiente %s" % AMBIENTE_ID, "Sistema pronto")
            return

        print("[MQTT] Coldstart negado/sem resposta (%s) - tentando em 15s..." %
              _coldstart_result)
        display_message("COLDSTART", "Sem resposta", "tentando em 15s")
        led_vd2.value(0)
        for _ in range(15):
            led_vd2.value(1 - led_vd2.value())
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
    global DEVICE_MAC, BOOT_COUNT, _update_requested

    print("\n" + "=" * 48)
    print("  CERBEROS ESP32-C3 (FECHO) - MQTT + UART")
    print("=" * 48)

    state = config.load_state()
    BOOT_COUNT = _read_boot_count()
    init_gpio()
    init_uart()
    init_display()

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
        status_pulse(120)
        time.sleep(10)

    ota.ensure_dependencies(OTA_HOST, OTA_PORT, BIBLIOTECAS)

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
    display_message("ACCESS-NG", "Operacional", "Ambiente %s" % AMBIENTE_ID)

    last_ota_check = time.time()
    ota_check_and_maybe_apply(state)

    while True:
        watchdog.feed()
        try:
            if not network.WLAN(network.STA_IF).isconnected():
                print("[WiFi] Reconectando...")
                display_message("WIFI", "Reconectando", WIFI_SSID)
                _set_link(False)
                if connect_wifi():
                    mqtt_connect()
                    do_coldstart()
                    last_heartbeat = time.time()
                else:
                    time.sleep(5)
                    continue

            if UART_ENABLED:
                uart_poll()

            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = time.time()

            if OTA_ENABLED and time.time() - last_ota_check >= OTA_CHECK_INTERVAL:
                ota_check_and_maybe_apply(state)
                last_ota_check = time.time()

            _client.check_msg()

            if _update_requested:
                _update_requested = False
                ota_check_and_maybe_apply(state)
                last_ota_check = time.time()

            time.sleep_ms(20)

        except OSError as e:
            print("[MQTT] Erro de rede: %s - reconectando..." % e)
            display_message("MQTT", "Erro de rede", "reconectando")
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
