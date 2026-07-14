"""
Cerberos + Caronte — MicroPython para BitDogLab V6 (Raspberry Pi Pico W)
Modo MQTT exclusivo

Este firmware comunica-se com o servidor Access-NG via MQTT.
Para o modo REST (HTTP/HTTPS) use Cerberos_BitDogLab.py.

─── config.json ──────────────────────────────────────────────────────────────

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

─── Tópicos MQTT ─────────────────────────────────────────────────────────────

  Publica:
    access-ng/coldstart/{mac}                → boot do dispositivo
    access-ng/heartbeat/{mac}                → presença periódica
    access-ng/{amb_id}/caronte/{mac}/tag     → TAG RFID para autenticação

  Assina:
    access-ng/coldstart/{mac}/result          → resposta do coldstart (status + ambiente_id)
    access-ng/{amb_id}/cerberos/{mac}/command → comando de abertura/check_update (servidor → dispositivo)
    access-ng/{amb_id}/caronte/{mac}/result   → resultado da autenticação

  O AMBIENTE_ID não é configurado no dispositivo: ele é obtido a partir da
  resposta do coldstart. Enquanto o coldstart não retornar status "ok"
  (MAC desconhecido ou chave inválida), o dispositivo repete a tentativa a
  cada 15s e não inicia a operação normal.

  O MAC usa '-' no lugar de ':' nos tópicos.
  HEARTBEAT_INTERVAL deve ser menor que OFFLINE_THRESHOLD do servidor (padrão 30s).
  Na BitDogLab, o OLED usa SCL=15 e SDA=14. Se RELAY_PIN também for 15 ou 14,
  o relé é desativado automaticamente para não conflitar com o display.

─── OTA (atualização remota) ──────────────────────────────────────────────────

  O firmware se atualiza buscando version.json em
  http://{OTA_HOST}:{OTA_PORT}/ota/{OTA_VERSION_PATH} (HTTP puro, sem TLS —
  os arquivos de OTA são públicos, sem segredo em trânsito), servido pelo
  proprio Access-NG (nao pelo raw.githubusercontent.com — a rede da IFRN
  nao entrega de forma confiavel arquivos maiores vindos do CDN do GitHub).
  Se a versão remota difere de FIRMWARE_VERSAO, baixa o .py em
  OTA_FIRMWARE_PATH, valida, grava em main.new, troca com main.py (backup
  em main.bak) e reinicia.

  A checagem ocorre: (1) após o coldstart, (2) periodicamente a cada
  OTA_CHECK_INTERVAL segundos, (3) imediatamente ao receber
  {"command":"check_update"} no tópico de comando.

  Rede de segurança: se a versão nova não completar um coldstart com sucesso
  em até 3 boots, o dispositivo restaura automaticamente main.bak (versão
  anterior conhecida como boa) e reinicia — evita "brick" remoto.

  Todo o ciclo (verificando, baixando, erro de rede/download, atualizado,
  rollback, confirmação) também aparece no OLED via display_message(), além
  do log na serial.
──────────────────────────────────────────────────────────────────────────────
"""

import machine
import network
import socket
import time
import json
import os
import ubinascii
import gc

# ─── CONFIGURAÇÃO ─────────────────────────────────────────────────────────────

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

# ─── OTA ────────────────────────────────────────────────────────────────────────

FIRMWARE_VERSAO   = "1.3.23"   # bump manual a cada release publicada
# Servido pelo proprio Access-NG (nao pelo raw.githubusercontent.com): a rede
# da IFRN nao entrega de forma confiavel arquivos maiores vindos do CDN do
# GitHub, mas o dispositivo ja tem conectividade comprovada com este host
# (mesmo dominio do broker MQTT).
OTA_VERSION_PATH  = "Hardware/Fechadura/version.json"
OTA_FIRMWARE_PATH = "Hardware/Fechadura/Cerberos_BitDogLab_MQTT.py"
OTA_HOST          = "laica.ifrn.edu.br"
# HTTP puro (sem TLS): mesma infraestrutura usada pelo CerberosESP32.py e
# CaronteESP32C3.py (o handshake TLS/RSA estourava a memoria desses ESP32).
# Os arquivos de OTA sao publicos (sem segredos), entao HTTP puro e
# aceitavel aqui — mesma logica de expor o broker MQTT em texto puro na
# porta 1883.
OTA_PORT          = 80

# ─── DIAGNÓSTICO ────────────────────────────────────────────────────────────────

HARDWARE_INFO         = "BitDogLab V6 (Pico W)"
HEARTBEAT_DIAG_EVERY  = 10   # rssi/mem_free/cpu_temp vao a cada N heartbeats


_SOFT_RESET_FLAG = 'soft_reset.flag'


def _read_boot_count():
    """Conta reinicios "soft" (machine.reset() chamado pelo proprio firmware:
    OTA, comando de reboot, rollback). Um boot sem a flag de soft-reset e
    tratado como reinicio completo (energia caiu) e zera o contador."""
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
    """Marca o proximo boot como soft-reset (mantem o contador) e reinicia."""
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


def _read_wifi_status():
    """Codigo bruto de network.WLAN.status() - o valor numerico varia por
    port/versao do MicroPython, por isso e reportado sem tentar traduzir."""
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
    """MAC do rádio do Access Point atualmente associado — identifica qual AP
    físico o dispositivo está usando, diferente do IP do gateway (que costuma
    ser o mesmo em toda uma rede com múltiplos APs sob o mesmo SSID). Nem
    toda combinação de porta/build do MicroPython expõe isso; retorna None
    quando indisponível."""
    try:
        bssid = network.WLAN(network.STA_IF).config('bssid')
        return ':'.join('%02X' % b for b in bssid)
    except Exception:
        return None


# Diagnostico de reconexao WiFi: contagem e ha quanto tempo desde a ultima,
# alem do codigo de status no momento em que a queda foi percebida (motivo
# aproximado da desconexao). Zerado a cada boot.
_wifi_reconnects = 0
_wifi_last_reconnect_s = None
_wifi_last_disconnect_status = None

# ─── HARDWARE ─────────────────────────────────────────────────────────────────

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
        print(f"[GPIO] RELAY_PIN {RELAY_PIN} conflita com OLED; relé desativado")
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
        # timeout (us) limita quanto o bit-bang espera por clock-stretch/ACK
        # antes de desistir com OSError - sem isso, um soluco no barramento
        # I2C (ruido de RF da propria antena WiFi, sensor travado, etc.) trava
        # o driver para sempre dentro de display_message(), o que congela o
        # firmware inteiro sem excecao nenhuma pra pegar (o try/except de
        # display_message() so ajuda se a chamada realmente retornar).
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
        # ja tinha conectado antes nesse boot - isso e uma reconexao, nao a
        # conexao inicial. Guarda o status no momento da queda como motivo.
        _wifi_last_disconnect_status = _read_wifi_status()
        _wifi_reconnects += 1
    _wifi_last_reconnect_s = time.time()

    print(f"[WiFi] Conectando em {WIFI_SSID}...")
    display_message("WIFI", "Conectando", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
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
    """Roda antes de tudo no boot. Se há um update pendente que falhou em
    completar um coldstart por 3 boots seguidos, restaura main.bak (versão
    anterior conhecida como boa) e reinicia. Nunca levanta exceção — esse
    código não pode travar o boot normal (sem update pendente, é um no-op)."""
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
    """Chamado após o primeiro coldstart+heartbeat bem-sucedidos na versão
    atual: remove os marcadores de update pendente (a versão é considerada
    estável). main.bak permanece como rede de segurança até a próxima
    atualização."""
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
    """GET HTTP simples (sem TLS). Retorna (status_code, body_str) ou (None, None)."""
    return _http_request(host or OTA_HOST, path, timeout=timeout)


def _http_request(host, path, dest_file=None, timeout=10):
    """GET HTTP em host+path (sem TLS — ver OTA_PORT). Se dest_file for
    informado, grava o corpo da resposta direto nesse arquivo (streaming) e
    retorna (status, None); senão acumula o corpo em memória e retorna
    (status, body_str). Retorna (None, None) em qualquer falha de rede."""
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
    """Converte "1.3.10" em (1, 3, 10) para comparação numérica.
    Comparar como string quebra em versões de dois dígitos (ex.:
    "1.3.10" < "1.3.7" lexicograficamente)."""
    try:
        return tuple(int(p) for p in str(v).split('.'))
    except (ValueError, AttributeError):
        return None


def check_for_update():
    """Busca version.json no repo. Retorna o dict remoto se a versão remota
    for numericamente MAIOR que a atual, ou None (sem update / qualquer
    falha). Nunca reinstala uma versão igual ou mais antiga — evita
    "downgrade" se o version.json servido estiver desatualizado em relação
    ao firmware já instalado."""
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
    """Checagem barata de sanidade do .py baixado antes de instalar.

    Lê em blocos para não carregar o firmware inteiro na RAM do Pico W.
    """
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
    """Baixa o firmware, valida, troca main.py e reinicia.
    Nunca propaga exceção — qualquer falha apenas aborta a atualização."""
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
    """Verifica e, se houver versão nova, aplica (reinicia em caso de sucesso)."""
    remote = check_for_update()
    if remote:
        apply_update(remote)


# ─── MQTT ─────────────────────────────────────────────────────────────────────

_client           = None
_unlock_flag      = False   # set pelo callback quando command=unlock chega
_update_requested = False   # set pelo callback quando command=check_update chega


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
    """Reporta o config efetivo atual: para cada chave de _DEFAULTS, o valor
    em uso agora (globals(), reflete tanto config.json quanto uma eventual
    sobrescrita de sessão via coldstart) e se ela está persistida no
    config.json (True) ou vem só do default/sessão (False). Campos sensíveis
    nunca têm o valor reportado, só a flag de persistência."""
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
    """Grava os parâmetros válidos em config.json e reinicia para aplicar
    de forma limpa (varios parametros so tem efeito na inicializacao do
    hardware, ex. pinos)."""
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
    """Aplica em memória (sem tocar config.json) as chaves permitidas vindas
    no coldstart_result — vale só até o próximo reboot."""
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
    global _unlock_flag, _update_requested, _coldstart_result
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
    """Publica coldstart e aguarda confirmação do servidor com o ambiente_id.

    Não retorna enquanto o servidor não responder status "ok" — repete a
    cada 15s em caso de "unknown"/"denied" ou ausência de resposta.
    """
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


# time.time() em vez de time.ticks_ms(): ticks_ms() no RP2040 estoura
# (volta a zero) a cada ~12,4 dias (periodo de 2**30 ms) — um dispositivo de
# controle de acesso deve ficar ligado por muito mais tempo que isso sem
# reiniciar, e usar ticks_ms() direto faria o campo "uptime" do heartbeat
# saltar/zerar sozinho nesse ponto, parecendo um reboot que nao aconteceu.
# time.time() nao tem esse problema (contador de segundos, sem wraparound
# nessa escala).
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
    global DEVICE_MAC, BOOT_COUNT, _btn_flag, _unlock_flag, _update_requested

    print("\n" + "=" * 48)
    print("  CERBEROS + CARONTE — BitDogLab V6 (MQTT)")
    print("=" * 48)

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
        led_denied(); time.sleep(10)

    ping_gateway()

    # MQTT
    while True:
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
    print("[Main] Operacional\n")
    display_message("ACCESS-NG", "Operacional", f"Ambiente {AMBIENTE_ID}")

    last_ota_check = time.time()
    ota_check_and_maybe_apply()

    while True:
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
            display_message("ERRO", "Loop principal", e)
            time.sleep(1)


main()
