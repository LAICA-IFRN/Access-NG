"""
Cerberos + Caronte BitDogLab V6 (Pico W) - MIGRADOR para o esquema
boot.py/main.py + accessng/

*** ESTE ARQUIVO É TRANSITÓRIO ***

Existe só para levar, 100% por OTA (sem religar fisicamente o
dispositivo), uma BitDogLab já em campo rodando a versão antiga (arquivo
único, sem boot.py) até o novo esquema descrito em
Hardware/Fechadura/main_bitdoglab.py + Hardware/Fechadura/
boot_bitdoglab.py + Hardware/accessng/. Ele É a aplicação antiga (mesmo
código de sempre - relé/LEDs RGB/OLED/botão local, MQTT, heartbeat, OTA,
diagnóstico de rede) rodando normalmente, mais uma rotina de migração
(_do_migration(), acionada por {"command":"migrate"} no tópico de
comando) que baixa os arquivos novos, valida cada um (compile() -
checagem de sintaxe, não só tamanho/substring) e só troca main.py/
instala boot.py depois que TUDO validar. Depois de trocado, o boot.py
novo (com seu próprio guard de crash-loop) passa a proteger o
dispositivo: se o main.py novo não confirmar saúde em até 3 boots, o
rollback automático restaura ESTE arquivo (migrador) como main.bak - não
a aplicação truly-original - porque este arquivo já é auto-suficiente e
sabe se confirmar saudável sozinho (ver o guarded confirm_boot_ok() perto
do fim de main()).

Mesmo padrão já usado para o Caronte, o FECHO e o Cerberos enxuto - ver a
docstring de Hardware/Autenticador/CaronteESP32C3.py para o desenho
completo do mecanismo de migração; este arquivo só adapta os detalhes
específicos da BitDogLab (RP2040/Pico W, OLED SSD1306, LEDs RGB via PWM,
botão local que publica TAG como um Caronte).

Depois que todos os dispositivos confirmarem a migração, este arquivo
deixa de ser necessário e pode ser removido do repositório (a versão
publicada em Hardware/Fechadura/version.json volta a apontar só para
main_bitdoglab.py).

--- Como confirmar que deu certo (remotamente) ---------------------------

  O sinal confiável: main.py definitivo reporta
  HARDWARE_INFO = "BitDogLab V6 (Pico W) (boot.py)" (este arquivo continua
  reportando só "BitDogLab V6 (Pico W)", sem o sufixo) - esse valor vai
  parar direto em Cerberos.hardware no próximo coldstart. "Contador de
  Boots" também deve incrementar (soft-reset ao trocar de arquivo).

--- Docstring original (BitDogLab antiga) ---------------------------------

Modo MQTT exclusivo. Para o modo REST (HTTP/HTTPS) use
Cerberos_BitDogLab.py.

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

--- Tópicos MQTT ---------------------------------------------------------

  Publica:
    access-ng/coldstart/{mac}                → boot do dispositivo
    access-ng/heartbeat/{mac}                → presença periódica
    access-ng/{amb_id}/caronte/{mac}/tag     → TAG RFID para autenticação

  Assina:
    access-ng/coldstart/{mac}/result          → resposta do coldstart
    access-ng/{amb_id}/cerberos/{mac}/command → comando de abertura/check_update
    access-ng/{amb_id}/caronte/{mac}/result   → resultado da autenticação

  O MAC usa '-' no lugar de ':' nos tópicos.

--- OTA (atualização remota) --------------------------------------------------

  Mesmo esquema dos demais firmwares deste projeto. Rede de segurança: se
  a versão nova não completar um coldstart em até 3 boots, o dispositivo
  restaura main.bak (versão anterior conhecida como boa) e reinicia.
"""

import machine
import network
import socket
import time
import json
import os
import ubinascii
import micropython
import gc

micropython.alloc_emergency_exception_buf(100)


# --- WATCHDOG ------------------------------------------------------------
#
# Rede de segurança contra travamentos de verdade (não apenas exceções, já
# tratadas nos próprios laços) - independente de accessng.watchdog (que só
# existe no dispositivo DEPOIS da migração instalar accessng/); este
# arquivo continua rodando sozinho, sem boot.py, então precisa da própria
# versão autocontida. Timeout curto (8s) de propósito - o RP2040 limita o
# watchdog de hardware a ~8.3s, mesmo raciocínio de accessng/watchdog.py.

_wdt = None


def _wdt_arm(timeout_ms=8000):
    global _wdt
    if _wdt is not None:
        return
    try:
        _wdt = machine.WDT(timeout=timeout_ms)
        print("[WDT] Armado (timeout=%dms)" % timeout_ms)
    except Exception as e:
        print("[WDT] Não foi possível armar:", e)


def _wdt_feed():
    if _wdt is not None:
        try:
            _wdt.feed()
        except Exception:
            pass


# --- CONFIGURAÇÃO ─────────────────────────────────────────────────────────────

_DEFAULTS = {
    "WIFI_SSID"           : "wIFRN-IoT",
    "WIFI_PASS"           : "deviceiotifrn",
    "MQTT_BROKER"         : "broker.exemplo.com",
    "MQTT_PORT"           : 1883,
    "MQTT_USER"           : "",
    "MQTT_PASS"           : "",
    "MQTT_TLS"            : False,
    "DEVICE_KEY"          : "chave-do-dispositivo",
    "HEARTBEAT_INTERVAL"  : 25,
    "BUTTON_PIN"          : 5,
    "BUTTON_DEBOUNCE_MS"  : 50,
    "BUTTON_TAG"          : "btn_local",
    "LED_RED_PIN"         : 13,
    "LED_GREEN_PIN"       : 11,
    "LED_BLUE_PIN"        : 12,
    "RELAY_PIN"           : 15,
    "RELAY_ACTIVE_MS"     : 2000,
    "OLED_ENABLED"        : True,
    "OLED_SCL_PIN"        : 15,
    "OLED_SDA_PIN"        : 14,
    "OLED_WIDTH"          : 128,
    "OLED_HEIGHT"         : 64,
    "OLED_ADDR"           : 0x3C,
    "OTA_ENABLED"         : True,
    "OTA_CHECK_INTERVAL"  : 3600,
}

# Nunca reportados por valor via MQTT (só é possível sobrescrever, não ler).
_CONFIG_SENSITIVE = ('WIFI_PASS', 'DEVICE_KEY', 'MQTT_PASS')
# Únicos que podem ser sobrescritos em memória (sem gravar em config.json) via
# um bloco "config" na resposta do coldstart — os demais dependem de pinos/
# hardware já inicializados antes do coldstart, exigindo reboot para valer.
_CONFIG_RUNTIME_KEYS = ('HEARTBEAT_INTERVAL', 'OTA_CHECK_INTERVAL', 'OTA_ENABLED', 'BUTTON_TAG')

try:
    with open('config.json') as f:
        _cfg_file = json.load(f)
    print("[Config] config.json carregado")
except Exception:
    _cfg_file = {}
    print("[Config] Usando valores padrão")

def cfg(key):
    v = _cfg_file.get(key, _DEFAULTS[key])
    return type(_DEFAULTS[key])(v)

WIFI_SSID          = cfg('WIFI_SSID')
WIFI_PASS          = cfg('WIFI_PASS')
MQTT_BROKER        = cfg('MQTT_BROKER')
MQTT_PORT          = cfg('MQTT_PORT')
MQTT_USER          = cfg('MQTT_USER')
MQTT_PASS          = cfg('MQTT_PASS')
MQTT_TLS           = cfg('MQTT_TLS')
DEVICE_KEY         = cfg('DEVICE_KEY')
HEARTBEAT_INTERVAL = cfg('HEARTBEAT_INTERVAL')
BUTTON_PIN         = cfg('BUTTON_PIN')
BUTTON_DEBOUNCE_MS = cfg('BUTTON_DEBOUNCE_MS')
BUTTON_TAG         = cfg('BUTTON_TAG')
LED_RED_PIN        = cfg('LED_RED_PIN')
LED_GREEN_PIN      = cfg('LED_GREEN_PIN')
LED_BLUE_PIN       = cfg('LED_BLUE_PIN')
RELAY_PIN          = cfg('RELAY_PIN')
RELAY_ACTIVE_MS    = cfg('RELAY_ACTIVE_MS')
OLED_ENABLED       = cfg('OLED_ENABLED')
OLED_SCL_PIN       = cfg('OLED_SCL_PIN')
OLED_SDA_PIN       = cfg('OLED_SDA_PIN')
OLED_WIDTH         = cfg('OLED_WIDTH')
OLED_HEIGHT        = cfg('OLED_HEIGHT')
OLED_ADDR          = cfg('OLED_ADDR')
OTA_ENABLED        = cfg('OTA_ENABLED')
OTA_CHECK_INTERVAL = cfg('OTA_CHECK_INTERVAL')

MQTT_PREFIX = 'access-ng'
DEVICE_MAC  = None
AMBIENTE_ID = None   # obtido a partir da resposta do coldstart
BOOT_COUNT  = None

# --- OTA ────────────────────────────────────────────────────────────────────────

# IMPORTANTE: precisa ser EXATAMENTE igual ao "versao" em
# Hardware/Fechadura/version.json - _valida_payload() confere que esse
# número aparece como substring no arquivo baixado (auto-atualização
# padrão do próprio migrador, igual qualquer OTA de sempre). NÃO precisa
# bater com o FIRMWARE_VERSAO do main.py definitivo (main_bitdoglab.py) -
# os dois evoluem de forma independente, ver _migration_validate_main().
FIRMWARE_VERSAO   = "1.3.27"   # bump manual a cada release publicada
OTA_VERSION_PATH  = "Hardware/Fechadura/version.json"
OTA_FIRMWARE_PATH = "Hardware/Fechadura/Cerberos_BitDogLab_MQTT.py"
OTA_HOST          = "laica.ifrn.edu.br"
OTA_PORT          = 80

# --- DIAGNÓSTICO ────────────────────────────────────────────────────────────────

HARDWARE_INFO         = "BitDogLab V6 (Pico W)"
HEARTBEAT_DIAG_EVERY  = 10   # rssi/mem_free/cpu_temp/fs_free vao a cada N heartbeats


_SOFT_RESET_FLAG = 'soft_reset.flag'


def _read_boot_count():
    try:
        with open(_SOFT_RESET_FLAG):
            is_soft = True
    except OSError:
        is_soft = False

    if is_soft:
        try:
            with open('boot_count.txt') as f:
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
        with open('boot_count.txt', 'w') as f:
            f.write(str(n))
    except OSError:
        pass
    return n


def _soft_reset():
    try:
        with open(_SOFT_RESET_FLAG, 'w') as f:
            f.write('1')
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
        return network.WLAN(network.STA_IF).status('rssi')
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
        return network.WLAN(network.STA_IF).config('channel')
    except Exception:
        return None


def _read_ap_bssid():
    wlan = network.WLAN(network.STA_IF)
    for getter in (wlan.config, wlan.status):
        try:
            bssid = getter('bssid')
            if bssid:
                return ':'.join('%02X' % b for b in bssid)
        except Exception:
            pass
    try:
        if wlan.isconnected():
            for rede in wlan.scan():
                if rede[0].decode('utf-8') == WIFI_SSID:
                    return ubinascii.hexlify(rede[1], ':').decode('utf-8')
    except Exception:
        pass
    return None


_wifi_reconnects = 0
_wifi_last_reconnect_s = None
_wifi_last_disconnect_status = None

# --- HARDWARE ─────────────────────────────────────────────────────────────────

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
        print(f"[OLED] Indisponível: {e}")
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
        print(f"[OLED] Falha ao atualizar: {e}")


# ─── WiFi ──────────────────────────────────────────────────────────────────────

def connect_wifi():
    global _wifi_reconnects, _wifi_last_reconnect_s, _wifi_last_disconnect_status
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print(f"[WiFi] IP: {wlan.ifconfig()[0]}")
        display_message("WIFI", "Conectado", wlan.ifconfig()[0])
        return True

    if _wifi_last_reconnect_s is not None:
        _wifi_last_disconnect_status = _read_wifi_status()
        _wifi_reconnects += 1
    _wifi_last_reconnect_s = time.time()

    print(f"[WiFi] Conectando em {WIFI_SSID}...")
    display_message("WIFI", "Conectando", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
        _wdt_feed()
        if wlan.isconnected():
            print(f"[WiFi] IP: {wlan.ifconfig()[0]}")
            display_message("WIFI", "Conectado", wlan.ifconfig()[0])
            return True
        time.sleep(0.5)
    print("[WiFi] Falha")
    display_message("WIFI", "Falha", "verifique rede")
    return False


# ─── OTA ──────────────────────────────────────────────────────────────────────

def _ota_boot_guard():
    try:
        with open('ota_pending.txt'):
            pass
    except OSError:
        return
    try:
        try:
            with open('ota_boot_attempts.txt') as f:
                tentativas = int(f.read().strip())
        except (OSError, ValueError):
            tentativas = 0
        tentativas += 1
        if tentativas >= 3:
            print("[OTA] Update pendente falhou %d vezes — restaurando main.bak" % tentativas)
            display_message("OTA", "Update falhou", "restaurando versao")
            try:
                os.remove('main.py')
                os.rename('main.bak', 'main.py')
            except OSError:
                pass
            for fname in ('ota_pending.txt', 'ota_boot_attempts.txt'):
                try:
                    os.remove(fname)
                except OSError:
                    pass
            time.sleep(2)
            _soft_reset()
        else:
            print("[OTA] Boot %d/3 com update pendente" % tentativas)
            display_message("OTA", "Verificando boot", "%d/3" % tentativas)
            with open('ota_boot_attempts.txt', 'w') as f:
                f.write(str(tentativas))
    except Exception as e:
        print("[OTA] Erro no boot guard:", e)
        display_message("OTA", "Erro boot guard", str(e)[:16])


def _ota_confirmar_versao_boa():
    havia_pendente = False
    for fname in ('ota_pending.txt', 'ota_boot_attempts.txt'):
        try:
            os.remove(fname)
            havia_pendente = True
        except OSError:
            pass
    if havia_pendente:
        print("[OTA] Versão", FIRMWARE_VERSAO, "confirmada como estável")
        display_message("OTA", "Versao confirmada", FIRMWARE_VERSAO)
        time.sleep(2)


def _http_get(path, host=None, timeout=10):
    return _http_request(host or OTA_HOST, path, timeout=timeout)


def _http_request(host, path, dest_file=None, timeout=10):
    sock = None
    t0 = time.time()
    gc.collect()
    try:
        ai   = socket.getaddrinfo(host, OTA_PORT, 0, socket.SOCK_STREAM)
        addr = ai[0][-1]
        print("[OTA] %s -> %s" % (host, addr))
        sock = socket.socket()
        sock.settimeout(timeout)
        sock.connect(addr)
        if dest_file:
            print("[OTA] TCP conectado, enviando requisição...")

        req = (
            "GET " + path + " HTTP/1.1\r\n"
            "Host: " + host + "\r\n"
            "User-Agent: access-ng-cerberos\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.write(req.encode('utf-8'))

        buf = b""
        status = None
        out = None
        total_bytes = None
        received = 0
        if dest_file:
            out = open(dest_file, 'wb')
        header_done = False
        try:
            while True:
                _wdt_feed()
                chunk = sock.read(1024)
                if not chunk:
                    break
                if not header_done:
                    buf += chunk
                    sep = buf.find(b"\r\n\r\n")
                    if sep == -1:
                        continue
                    header_done = True
                    header_str = buf[:sep].decode('utf-8', 'ignore')
                    status = int(header_str.split('\r\n', 1)[0].split()[1])
                    for line in header_str.split('\r\n')[1:]:
                        if line.lower().startswith('content-length:'):
                            try:
                                total_bytes = int(line.split(':', 1)[1].strip())
                            except ValueError:
                                pass
                    if dest_file:
                        print("[OTA] Resposta recebida (status=%s, tamanho=%s)" %
                              (status, total_bytes if total_bytes is not None else '?'))
                    rest = buf[sep + 4:]
                    buf = b""
                    if out:
                        if rest:
                            out.write(rest)
                            received += len(rest)
                            if total_bytes and received >= total_bytes:
                                break
                    else:
                        buf = rest
                else:
                    if out:
                        out.write(chunk)
                        received += len(chunk)
                        if dest_file:
                            if total_bytes:
                                print("[OTA] Download: %d/%d bytes (%d%%)" %
                                      (received, total_bytes, received * 100 // total_bytes))
                            else:
                                print("[OTA] Download: %d bytes" % received)
                        if total_bytes and received >= total_bytes:
                            break
                    else:
                        buf += chunk
        finally:
            if out:
                out.close()

        if status is None:
            return None, None
        if dest_file and total_bytes is not None and received < total_bytes:
            print("[OTA] Download incompleto: %d/%d bytes" % (received, total_bytes))
            return None, None
        if dest_file:
            print("[OTA] Download concluído: %d bytes" % received)
        return status, (None if out else buf.decode('utf-8', 'ignore'))
    except Exception as e:
        print("[OTA] Erro HTTP (%.1fs):" % (time.time() - t0), e)
        return None, None
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        gc.collect()


def _parse_versao(v):
    try:
        return tuple(int(p) for p in str(v).split('.'))
    except (ValueError, AttributeError):
        return None


def check_for_update():
    if not OTA_ENABLED:
        return None
    status, body = _http_get("/access-ng/ota/" + OTA_VERSION_PATH)
    if status != 200 or not body:
        print("[OTA] Falha ao verificar version.json (status=%s)" % status)
        display_message("OTA", "Falha ao verificar", "tentando depois")
        return None
    try:
        remote = json.loads(body)
    except Exception:
        print("[OTA] version.json inválido")
        display_message("OTA", "version.json", "invalido")
        return None

    remota_versao = remote.get('versao')
    remota_t = _parse_versao(remota_versao)
    atual_t = _parse_versao(FIRMWARE_VERSAO)
    if remota_t is None or atual_t is None:
        if remota_versao == FIRMWARE_VERSAO:
            return None
    elif remota_t <= atual_t:
        return None

    print("[OTA] Nova versão disponível:", remota_versao)
    display_message("OTA", "Nova versao", remota_versao)
    return remote


def _valida_payload(path, versao):
    try:
        if os.stat(path)[6] < 500:
            return False
        needle_fw = b'FIRMWARE_VERSAO'
        needle_ver = str(versao).encode('utf-8')
        found_fw = False
        found_ver = False
        tail = b''
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(512)
                if not chunk:
                    break
                data = tail + chunk
                if not found_fw and needle_fw in data:
                    found_fw = True
                if needle_ver and not found_ver and needle_ver in data:
                    found_ver = True
                if found_fw and found_ver:
                    return True
                tail = data[-64:]
        return False
    except Exception as e:
        print("[OTA] Erro ao validar payload:", e)
        return False


def apply_update(remote):
    try:
        versao = remote.get('versao', '')
        path = "/access-ng/ota/" + OTA_FIRMWARE_PATH
        print("[OTA] Baixando", "http://" + OTA_HOST + path)
        display_message("OTA", "Baixando", versao)
        status, _ = _http_request(OTA_HOST, path, dest_file='main.new', timeout=30)
        if status != 200 or not _valida_payload('main.new', versao):
            print("[OTA] Download inválido (status=%s) — abortando" % status)
            try:
                os.remove('main.new')
            except OSError:
                pass
            display_message("OTA", "Falha download", "mantendo atual")
            return False

        try:
            os.remove('main.bak')
        except OSError:
            pass
        os.rename('main.py', 'main.bak')
        os.rename('main.new', 'main.py')
        with open('ota_pending.txt', 'w') as f:
            f.write(versao)
        try:
            os.remove('ota_boot_attempts.txt')
        except OSError:
            pass

        print("[OTA] Atualizado para", versao, "— reiniciando")
        display_message("OTA", "Atualizado", versao)
        time.sleep(1)
        _soft_reset()
    except Exception as e:
        print("[OTA] Erro ao aplicar atualização:", e)
        display_message("OTA", "Erro ao aplicar", str(e)[:16])
        return False


def ota_check_and_maybe_apply():
    remote = check_for_update()
    if remote:
        apply_update(remote)


# --- MIGRAÇÃO para boot.py/main.py + accessng/ -------------------------------
#
# Acionada por {"command":"migrate"} (ver _on_message). Baixa e valida TUDO
# antes de tocar em qualquer coisa que já funciona - nenhum arquivo existente
# é sobrescrito/trocado até o último já ter passado por download + compile().
# Mesmo mecanismo dos migradores do Caronte/FECHO/Enxuto - ver a docstring
# de Hardware/Autenticador/CaronteESP32C3.py para o desenho completo.

_MIGRATION_SUPPORT_FILES = [
    ("Hardware/accessng/__init__.py", "/accessng/__init__.py"),
    ("Hardware/accessng/config.py", "/accessng/config.py"),
    ("Hardware/accessng/wifi.py", "/accessng/wifi.py"),
    ("Hardware/accessng/recovery.py", "/accessng/recovery.py"),
    ("Hardware/accessng/provisioning.py", "/accessng/provisioning.py"),
    ("Hardware/accessng/ota.py", "/accessng/ota.py"),
    ("Hardware/accessng/watchdog.py", "/accessng/watchdog.py"),
    ("Hardware/bibliotecas/umqtt/__init__.py", "/umqtt/__init__.py"),
    ("Hardware/bibliotecas/umqtt/simple.py", "/umqtt/simple.py"),
    ("Hardware/bibliotecas/umqtt/robust.py", "/umqtt/robust.py"),
    ("Hardware/bibliotecas/ssd1306.py", "/ssd1306.py"),
    ("Hardware/Fechadura/device_defaults_bitdoglab.py", "/device_defaults.py"),
]
# boot.py baixado com nome provisório - só vira boot.py de verdade depois de
# TODO o resto (inclusive o main.py novo) já ter validado.
_MIGRATION_BOOT_FILE = ("Hardware/Fechadura/boot_bitdoglab.py", "/boot.py.new", "/boot.py")
_MIGRATION_MAIN_FILE = ("Hardware/Fechadura/main_bitdoglab.py", "/main_target.new")


def _migration_mkdir(path):
    parts = path.split("/")[1:-1]
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            os.mkdir(cur)
        except OSError:
            pass


def _migration_compile_check(path):
    gc.collect()
    try:
        with open(path) as f:
            source = f.read()
        compile(source, path, "exec")
        return True
    except Exception as e:
        print("[Migração] Erro de sintaxe em %s: %s" % (path, e))
        return False


def _migration_validate_main(path):
    """Validação leve (streaming, em blocos de 512 bytes) do main.py
    definitivo: só confirma tamanho mínimo e a presença da assinatura
    "FIRMWARE_VERSAO", sem exigir um número de versão específico - mesmo
    raciocínio dos outros três migradores."""
    try:
        if os.stat(path)[6] < 500:
            return False
        needle = b"FIRMWARE_VERSAO"
        tail = b""
        with open(path, "rb") as f:
            while True:
                chunk = f.read(512)
                if not chunk:
                    break
                data = tail + chunk
                if needle in data:
                    return True
                tail = data[-64:]
        return False
    except Exception as e:
        print("[Migração] Erro ao validar main.py:", e)
        return False


def _migration_download(repo_path, dest_path, validate=_migration_compile_check):
    _migration_mkdir(dest_path)
    print("[Migração] Baixando %s -> %s" % (repo_path, dest_path))
    status, _ = _http_request(OTA_HOST, "/access-ng/ota/" + repo_path,
                               dest_file=dest_path, timeout=20)
    gc.collect()
    if status != 200:
        print("[Migração] Falha ao baixar %s (status=%s)" % (repo_path, status))
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return False
    if not validate(dest_path):
        print("[Migração] Falha ao validar %s" % dest_path)
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return False
    return True


def _do_migration():
    """Baixa e valida os arquivos do novo esquema (accessng/bibliotecas/
    device_defaults.py/boot.py/main.py); só instala/troca algo se TODOS
    tiverem passado. Nunca propaga exceção - qualquer falha só aborta a
    migração (o dispositivo continua rodando normalmente com este
    arquivo, pronto pra uma nova tentativa via {"command":"migrate"})."""
    try:
        print("[Migração] Iniciando...")
        display_message("MIGRAÇÃO", "Iniciando...")
        for repo_path, dest_path in _MIGRATION_SUPPORT_FILES:
            if not _migration_download(repo_path, dest_path):
                print("[Migração] Abortada")
                display_message("MIGRAÇÃO", "Abortada", dest_path)
                return False

        boot_repo, boot_staged, boot_final = _MIGRATION_BOOT_FILE
        if not _migration_download(boot_repo, boot_staged):
            print("[Migração] Abortada")
            display_message("MIGRAÇÃO", "Abortada", "boot.py")
            return False

        main_repo, main_staged = _MIGRATION_MAIN_FILE
        # main.py definitivo é grande o bastante pra estourar memória no
        # compile() (mesmo problema visto em campo no Caronte) - validação
        # por streaming (não amarrada a nenhum número de versão específico).
        if not _migration_download(main_repo, main_staged, validate=_migration_validate_main):
            print("[Migração] Abortada")
            display_message("MIGRAÇÃO", "Abortada", "main.py")
            return False

        print("[Migração] Todos os arquivos validados - instalando...")
        display_message("MIGRAÇÃO", "Instalando...")

        # accessng/bibliotecas/device_defaults.py já foram gravados direto
        # nos nomes finais (são adições puras, nada existente é
        # sobrescrito). Só falta promover boot.py e trocar main.py.
        try:
            os.remove(boot_final)
        except OSError:
            pass
        os.rename(boot_staged, boot_final)

        # Mesmo padrão já usado pelo OTA normal (main.py -> main.bak, novo
        # -> main.py): se o main.py novo não confirmar saúde, o boot.py
        # que acabou de ser instalado restaura ESTE arquivo (o migrador)
        # via main.bak - não a aplicação original de antes da migração.
        try:
            os.remove("main.bak")
        except OSError:
            pass
        os.rename("main.py", "main.bak")
        os.rename(main_staged, "main.py")

        # boot_state.json no formato novo - boot.py já existe a partir de
        # agora, então é ele quem lê isso no próximo boot.
        from accessng import config as _new_config
        state = _new_config.load_state()
        state["pending_update"] = True
        state["previous_version"] = FIRMWARE_VERSAO
        state["current_version"] = FIRMWARE_VERSAO
        state["boot_count"] = 0
        state["last_boot_ok"] = False
        _new_config.save_state(state)

        print("[Migração] Instalada - reiniciando para boot.py/main.py novos")
        display_message("MIGRAÇÃO", "Instalada", "reiniciando...")
        time.sleep(1)
        _soft_reset()
    except Exception as e:
        print("[Migração] Erro inesperado:", e)
        display_message("MIGRAÇÃO", "Erro", str(e)[:16])
        return False


# ─── MQTT ─────────────────────────────────────────────────────────────────────

_client           = None
_unlock_flag      = False   # set pelo callback quando command=unlock chega
_update_requested = False   # set pelo callback quando command=check_update chega
_migration_requested = False   # set pelo callback quando command=migrate chega


def _mac_safe():
    return DEVICE_MAC.replace(':', '-')


def _t():
    """Retorna os tópicos derivados do MAC e, quando já conhecido, do ambiente."""
    mac = _mac_safe()
    p   = MQTT_PREFIX
    topics = {
        'coldstart'       : f'{p}/coldstart/{mac}',
        'coldstart_result': f'{p}/coldstart/{mac}/result',
        'heartbeat'       : f'{p}/heartbeat/{mac}',
    }
    if AMBIENTE_ID is not None:
        amb = str(AMBIENTE_ID)
        topics['tag']            = f'{p}/{amb}/caronte/{mac}/tag'
        topics['result']         = f'{p}/{amb}/caronte/{mac}/result'
        topics['command']        = f'{p}/{amb}/cerberos/{mac}/command'
        topics['config_result']  = f'{p}/{amb}/cerberos/{mac}/config/result'
    return topics


_coldstart_result = None


def _publish_config():
    params = {}
    for key in _DEFAULTS:
        persistido = key in _cfg_file
        if key in _CONFIG_SENSITIVE:
            params[key] = {'persistido': persistido}
        else:
            params[key] = {'valor': globals().get(key, _DEFAULTS[key]), 'persistido': persistido}
    topic = _t().get('config_result')
    if topic:
        _client.publish(topic, json.dumps({'mac': DEVICE_MAC, 'params': params}))
        print("[Config] Configuração atual reportada")


def _apply_set_config(params):
    validos = {k: v for k, v in (params or {}).items() if k in _DEFAULTS}
    if not validos:
        print("[Config] set_config sem parametros validos, ignorando")
        return
    _cfg_file.update(validos)
    try:
        with open('config.json', 'w') as f:
            json.dump(_cfg_file, f)
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
        if key not in _CONFIG_RUNTIME_KEYS or key not in _DEFAULTS:
            continue
        try:
            globals()[key] = type(_DEFAULTS[key])(value)
            print("[Config] %s sobrescrito para %r (somente sessão)" % (key, globals()[key]))
        except Exception:
            pass


def _on_message(topic, payload):
    global _unlock_flag, _update_requested, _coldstart_result, _migration_requested
    topic_str = topic.decode('utf-8')
    topics    = _t()
    try:
        data = json.loads(payload)
    except Exception:
        return

    if topic_str == topics['coldstart_result']:
        _coldstart_result = data

    elif topic_str == topics.get('command'):
        if data.get('command') == 'unlock':
            print("[MQTT] Comando de abertura recebido!")
            display_message("COMANDO", "Abertura", "recebida")
            _unlock_flag = True
        elif data.get('command') == 'check_update':
            print("[MQTT] Solicitação de verificação de atualização recebida")
            _update_requested = True
        elif data.get('command') == 'reboot':
            print("[MQTT] Comando de reinício recebido - reiniciando...")
            display_message("COMANDO", "Reiniciando", "aguarde...")
            time.sleep_ms(300)
            _soft_reset()
        elif data.get('command') == 'get_config':
            print("[MQTT] Solicitação de configuração recebida")
            _publish_config()
        elif data.get('command') == 'set_config':
            _apply_set_config(data.get('params'))
        elif data.get('command') == 'migrate':
            print("[MQTT] Solicitação de migração para boot.py/main.py recebida")
            _migration_requested = True

    elif topic_str == topics.get('result'):
        if data.get('allow'):
            print("[MQTT] Acesso autorizado!")
            display_message("ACESSO", "Autorizado", "abrindo porta")
            unlock_door()
        else:
            print(f"[MQTT] Acesso negado: {data.get('motivo', '')}")
            display_message("ACESSO", "Negado", data.get('motivo', ''))
            led_denied()


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
    """Envia pings ICMP ao gateway padrao da rede WiFi.

    Alguns servidores bloqueiam ICMP mesmo com MQTT/TCP funcionando. Pingar o
    gateway testa o enlace local sem confundir isso com bloqueio de ping no
    broker.
    """
    import ustruct

    try:
        wlan = network.WLAN(network.STA_IF)
        host_ip = wlan.ifconfig()[2]
        if not host_ip or host_ip == "0.0.0.0":
            print("[Ping] Gateway padrao indisponivel - pulando ping")
            return
    except Exception as e:
        print(f"[Ping] Falha ao obter gateway padrao: {e}")
        return

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, 1)  # 1 = ICMP
    except Exception as e:
        print(f"[Ping] Raw socket indisponível ({e}) — pulando ping")
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
                    print(f"[Ping] {host_ip}: seq={seq} tempo={dt}ms")
                    ok += 1
                    break
        except OSError:
            print(f"[Ping] {host_ip}: seq={seq} timeout")
        time.sleep_ms(200)

    s.close()
    print(f"[Ping] gateway {host_ip}: {ok}/{count} respostas")


def _diag_broker():
    """Resolve o host e testa um socket TCP cru, para diferenciar falha de
    DNS de bloqueio/recusa de conexão pela rede ou pelo broker."""
    try:
        addr = socket.getaddrinfo(MQTT_BROKER, MQTT_PORT)[0][-1]
        print(f"[Diag] {MQTT_BROKER} -> {addr}")
    except Exception as e:
        print(f"[Diag] Falha ao resolver {MQTT_BROKER}: {e}")
        return
    try:
        s = socket.socket()
        s.connect(addr)
        s.close()
        print("[Diag] Socket TCP cru conectou OK")
    except Exception as e:
        print(f"[Diag] Socket TCP cru falhou: {e}")


def mqtt_connect():
    global _client
    try:
        from umqtt.robust import MQTTClient
    except ImportError:
        from umqtt.simple import MQTTClient

    kwargs = {'port': MQTT_PORT, 'keepalive': 90}
    if MQTT_USER:
        kwargs['user']     = MQTT_USER
        kwargs['password'] = MQTT_PASS
    if MQTT_TLS:
        kwargs['ssl'] = True

    c = MQTTClient(f'cerberos-{_mac_safe()}', MQTT_BROKER, **kwargs)
    c.set_callback(_on_message)
    c.connect()
    c.subscribe(_t()['coldstart_result'])
    _client = c
    print(f"[MQTT] Conectado ao broker {MQTT_BROKER}:{MQTT_PORT}")
    display_message("MQTT", "Conectado", MQTT_BROKER)


def do_coldstart():
    global AMBIENTE_ID, _coldstart_result
    while True:
        _coldstart_result = None
        try:
            _client.publish(_t()['coldstart'],
                            json.dumps({'mac': DEVICE_MAC, 'chave': DEVICE_KEY,
                                        'versao': FIRMWARE_VERSAO,
                                        'boot_count': BOOT_COUNT, 'hardware': HARDWARE_INFO,
                                        'mcu': _read_mcu(), 'ssid': WIFI_SSID}),
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
            print(f"[MQTT] Erro de rede no coldstart: {e} — reconectando...")
            display_message("MQTT", "Erro de rede", "reconectando")
            try:
                mqtt_connect()
            except Exception:
                pass
            time.sleep(5)
            continue

        if _coldstart_result and _coldstart_result.get('status') == 'ok':
            AMBIENTE_ID = _coldstart_result.get('ambiente_id')
            _apply_session_config(_coldstart_result.get('config'))
            topics = _t()
            _client.subscribe(topics['command'])
            _client.subscribe(topics['result'])
            print(f"[MQTT] Coldstart OK — ambiente_id={AMBIENTE_ID}")
            display_message("COLDSTART OK", f"Ambiente {AMBIENTE_ID}", "Sistema pronto")
            led_ok()
            return

        print(f"[MQTT] Coldstart negado/sem resposta ({_coldstart_result}) — tentando em 15s...")
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
        'mac': DEVICE_MAC,
        'uptime_s': uptime_s,
        'uptime': _format_uptime(uptime_s),
        'ip': network.WLAN(network.STA_IF).ifconfig()[0],
        'versao': FIRMWARE_VERSAO,
    }
    _heartbeat_count += 1
    if _heartbeat_count % HEARTBEAT_DIAG_EVERY == 1:
        payload['rssi'] = _read_rssi()
        mem_free = gc.mem_free()
        payload['mem_free'] = mem_free
        if _mem_free_min is None or mem_free < _mem_free_min:
            _mem_free_min = mem_free
        payload['mem_free_min'] = _mem_free_min
        payload['cpu_temp'] = _read_cpu_temp()
        payload['wifi_status'] = _read_wifi_status()
        payload['wifi_channel'] = _read_wifi_channel()
        payload['bssid'] = _read_ap_bssid()
        payload['wifi_reconnects'] = _wifi_reconnects
        if _wifi_last_reconnect_s is not None:
            payload['wifi_last_reconnect_s'] = time.time() - _wifi_last_reconnect_s
        if _wifi_last_disconnect_status is not None:
            payload['wifi_last_disconnect_status'] = _wifi_last_disconnect_status
        fs_free, fs_total = _read_fs_stats()
        payload['fs_free'] = fs_free
        payload['fs_total'] = fs_total
    _client.publish(_t()['heartbeat'], json.dumps(payload))


def publish_tag():
    print("[MQTT] Publicando TAG do botão...")
    display_message("BOTAO", "Autenticando", BUTTON_TAG)
    _client.publish(_t()['tag'], json.dumps({
        'tag'  : BUTTON_TAG,
        'chave': DEVICE_KEY,
        'mac'  : DEVICE_MAC,
    }))


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    global DEVICE_MAC, BOOT_COUNT, _btn_flag, _unlock_flag, _update_requested, _migration_requested

    print("\n" + "=" * 48)
    print("  CERBEROS + CARONTE — BitDogLab V6 (MQTT)")
    print("=" * 48)

    _wdt_arm()

    BOOT_COUNT = _read_boot_count()
    init_display()
    _ota_boot_guard()
    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config('mac'), ':').decode()
    print(f"[Device] MAC: {DEVICE_MAC}")
    display_message("DISPOSITIVO", "MAC", DEVICE_MAC)

    # WiFi
    while not connect_wifi():
        _wdt_feed()
        led_denied(); time.sleep(10)

    ping_gateway()

    # MQTT
    while True:
        _wdt_feed()
        _diag_broker()
        try:
            display_message("MQTT", "Conectando", MQTT_BROKER)
            mqtt_connect()
            break
        except Exception as e:
            print(f"[MQTT] Falha na conexão: {e} — tentando em 10s...")
            display_message("MQTT", "Falha conexao", "tentando em 10s")
            led_denied(); time.sleep(10)

    do_coldstart()
    last_heartbeat = time.time()
    _ota_confirmar_versao_boa()

    # Se este arquivo estiver rodando como main.bak restaurado por boot.py
    # (uma tentativa de migração anterior que não confirmou saúde a
    # tempo), accessng já existe no dispositivo - confirma aqui pra zerar
    # boot_count e o dispositivo não voltar a cair em recovery só por este
    # arquivo (que não é o main.py final) nunca "confirmar" pelo caminho
    # normal. Import tardio e guardado: em um dispositivo que ainda não
    # passou por nenhuma tentativa de migração, accessng nem existe, e
    # isso é um no-op silencioso.
    try:
        from accessng import config as _acc_config, ota as _acc_ota
        _state = _acc_config.load_state()
        _acc_ota.confirm_boot_ok(_state, FIRMWARE_VERSAO)
        _acc_config.save_state(_state)
    except ImportError:
        pass

    print("[Main] Operacional\n")
    display_message("ACCESS-NG", "Operacional", f"Ambiente {AMBIENTE_ID}")

    last_ota_check = time.time()
    ota_check_and_maybe_apply()

    while True:
        _wdt_feed()
        try:
            # Reconexão WiFi
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

            # Botão local → publica TAG para autenticação
            if _btn_flag:
                _btn_flag = False
                time.sleep_ms(BUTTON_DEBOUNCE_MS)
                publish_tag()

            # Heartbeat periódico
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = time.time()

            # Verificação periódica de atualização (OTA)
            if OTA_ENABLED and time.time() - last_ota_check >= OTA_CHECK_INTERVAL:
                ota_check_and_maybe_apply()
                last_ota_check = time.time()

            # Processa mensagens MQTT recebidas
            _client.check_msg()

            # Abre a porta se comando chegou
            if _unlock_flag:
                _unlock_flag = False
                unlock_door()

            # Verificação imediata de atualização solicitada via MQTT
            if _update_requested:
                _update_requested = False
                ota_check_and_maybe_apply()
                last_ota_check = time.time()

            if _migration_requested:
                _migration_requested = False
                _do_migration()

            time.sleep_ms(50)

        except OSError as e:
            print(f"[MQTT] Erro de rede: {e} — reconectando...")
            display_message("MQTT", "Erro de rede", "reconectando")
            try:
                mqtt_connect()
                do_coldstart()
                last_heartbeat = time.time()
            except Exception:
                time.sleep(5)
        except Exception as e:
            print(f"[Main] Erro: {e}")
            display_message("ERRO", "Loop principal", str(e))
            time.sleep(1)


main()
