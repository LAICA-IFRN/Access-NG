"""
Cerberos ESP32 (enxuto) - MIGRADOR para o esquema boot.py/main.py + accessng/

*** ESTE ARQUIVO É TRANSITÓRIO ***

Existe só para levar, 100% por OTA (sem religar fisicamente o
dispositivo), um Cerberos enxuto já em campo rodando a versão antiga
(arquivo único, sem boot.py) até o novo esquema descrito em
Hardware/Fechadura/main_esp32.py + Hardware/Fechadura/boot_esp32.py +
Hardware/accessng/. Ele É a aplicação antiga (mesmo código de sempre -
relé/LEDs/entrada física, MQTT, heartbeat, OTA) rodando normalmente, mais
uma rotina de migração (_do_migration(), acionada por
{"command":"migrate"} no tópico de comando) que baixa os arquivos novos,
valida cada um (compile() - checagem de sintaxe, não só tamanho/
substring) e só troca main.py/instala boot.py depois que TUDO validar.
Depois de trocado, o boot.py novo (com seu próprio guard de crash-loop)
passa a proteger o dispositivo: se o main.py novo não confirmar saúde em
até 3 boots, o rollback automático restaura ESTE arquivo (migrador) como
main.bak - não a aplicação truly-original - porque este arquivo já é
auto-suficiente e sabe se confirmar saudável sozinho (ver o guarded
confirm_boot_ok() perto do fim de main()).

Mesmo padrão já usado para o Caronte (Hardware/Autenticador/
CaronteESP32C3.py) e o FECHO (Hardware/Fechadura/CerberosESP32C3.py) - ver
a docstring de lá para o desenho completo do mecanismo de migração; este
arquivo só adapta os detalhes específicos do Cerberos enxuto (sem OLED/
UART, com entradas físicas de acionamento no lugar).

Depois que todos os dispositivos confirmarem a migração, este arquivo
deixa de ser necessário e pode ser removido do repositório (a versão
publicada em Hardware/Fechadura/version_esp32.json volta a apontar só
para main_esp32.py).

--- Como confirmar que deu certo (remotamente) ---------------------------

  O sinal confiável: main.py definitivo reporta
  HARDWARE_INFO = "Cerberos ESP32 DevKit (boot.py)" (este arquivo continua
  reportando só "Cerberos ESP32 DevKit", sem o sufixo) - esse valor vai
  parar direto em Cerberos.hardware no próximo coldstart. "Contador de
  Boots" também deve incrementar (soft-reset ao trocar de arquivo).

--- Docstring original (Cerberos enxuto antigo) ----------------------------

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
    "INPUT_ENABLED"      : true,
    "INPUT_PINS"         : [26, 34],
    "INPUT_DEBOUNCE_MS"  : 200,
    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}

--- Pinagem ESP32 ------------------------------------------------------------

  GPIO 12 -> LED link vermelho. Aceso quando WiFi + broker MQTT estao OK.
  GPIO 13 -> LED status verde. Pisca quando ha trafego MQTT ou acionamento.
  GPIO 15 -> Rele da fechadura. Ativo alto, tempo maximo 2s.
  GPIO 26 -> Entrada logica para liberar o rele. Ativo baixo.
  GPIO 34 -> Entrada logica para liberar o rele. Ativo baixo.

Observacao: no ESP32, GPIO34 e somente entrada e nao possui pull-up interno.
Use resistor pull-up externo nessa entrada quando o sinal for ativo baixo.

--- OTA (atualizacao remota) --------------------------------------------------

  Mesmo esquema dos demais firmwares deste projeto, com arquivo de versao
  proprio (version_esp32.json). Rede de segurança: se a versão nova não
  completar um coldstart em até 3 boots, o dispositivo restaura main.bak
  (versão anterior conhecida como boa) e reinicia.
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
# versão autocontida.

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
    "INPUT_ENABLED"      : False,
    "INPUT_PINS"         : [26, 34],
    "INPUT_DEBOUNCE_MS"  : 200,
    "OTA_ENABLED"        : True,
    "OTA_CHECK_INTERVAL" : 3600,
}

# Nunca reportados por valor via MQTT (so e possivel sobrescrever, nao ler).
_CONFIG_SENSITIVE = ("WIFI_PASS", "DEVICE_KEY", "MQTT_PASS")
# Unicos que podem ser sobrescritos em memoria (sem gravar em config.json) via
# um bloco "config" na resposta do coldstart - os demais dependem de pinos/
# hardware ja inicializados antes do coldstart, exigindo reboot para valer.
_CONFIG_RUNTIME_KEYS = ("HEARTBEAT_INTERVAL", "OTA_CHECK_INTERVAL", "OTA_ENABLED")

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
INPUT_ENABLED      = cfg("INPUT_ENABLED")
INPUT_PINS         = cfg("INPUT_PINS")
INPUT_DEBOUNCE_MS  = cfg("INPUT_DEBOUNCE_MS")
OTA_ENABLED        = cfg("OTA_ENABLED")
OTA_CHECK_INTERVAL = cfg("OTA_CHECK_INTERVAL")

MQTT_PREFIX = "access-ng"
DEVICE_MAC  = None
AMBIENTE_ID = None
BOOT_COUNT  = None

# --- OTA -----------------------------------------------------------------------

# IMPORTANTE: precisa ser EXATAMENTE igual ao "versao" em
# Hardware/Fechadura/version_esp32.json - _valida_payload() confere que
# esse número aparece como substring no arquivo baixado (auto-atualização
# padrão do próprio migrador, igual qualquer OTA de sempre). NÃO precisa
# bater com o FIRMWARE_VERSAO do main.py definitivo (main_esp32.py) - os
# dois evoluem de forma independente, ver _migration_validate_main().
FIRMWARE_VERSAO   = "1.3.9"   # bump manual a cada release publicada
OTA_VERSION_PATH  = "Hardware/Fechadura/version_esp32.json"
OTA_FIRMWARE_PATH = "Hardware/Fechadura/CerberosESP32.py"
OTA_HOST          = "laica.ifrn.edu.br"
OTA_PORT          = 80

# --- DIAGNOSTICO ---------------------------------------------------------

HARDWARE_INFO         = "Cerberos ESP32 DevKit"
HEARTBEAT_DIAG_EVERY  = 10   # rssi/mem_free/cpu_temp/fs_free vao a cada N heartbeats


_SOFT_RESET_FLAG = "soft_reset.flag"


def _read_boot_count():
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


# --- WIFI --------------------------------------------------------------------

def connect_wifi():
    global _wifi_reconnects, _wifi_last_reconnect_s, _wifi_last_disconnect_status
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print("[WiFi] IP: %s" % wlan.ifconfig()[0])
        return True

    if _wifi_last_reconnect_s is not None:
        _wifi_last_disconnect_status = _read_wifi_status()
        _wifi_reconnects += 1
    _wifi_last_reconnect_s = time.time()

    print("[WiFi] Conectando em %s..." % WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
        _wdt_feed()
        if wlan.isconnected():
            print("[WiFi] IP: %s" % wlan.ifconfig()[0])
            return True
        led_link.value(1 - led_link.value())
        time.sleep(0.5)

    led_link.value(0)
    print("[WiFi] Falha")
    return False


# --- OTA -----------------------------------------------------------------------

def _ota_boot_guard():
    try:
        with open("ota_pending.txt"):
            pass
    except OSError:
        return
    try:
        try:
            with open("ota_boot_attempts.txt") as f:
                tentativas = int(f.read().strip())
        except (OSError, ValueError):
            tentativas = 0
        tentativas += 1
        if tentativas >= 3:
            print("[OTA] Update pendente falhou %d vezes - restaurando main.bak" % tentativas)
            try:
                os.remove("main.py")
                os.rename("main.bak", "main.py")
            except OSError:
                pass
            for fname in ("ota_pending.txt", "ota_boot_attempts.txt"):
                try:
                    os.remove(fname)
                except OSError:
                    pass
            _soft_reset()
        else:
            with open("ota_boot_attempts.txt", "w") as f:
                f.write(str(tentativas))
    except Exception as e:
        print("[OTA] Erro no boot guard:", e)


def _ota_confirmar_versao_boa():
    for fname in ("ota_pending.txt", "ota_boot_attempts.txt"):
        try:
            os.remove(fname)
        except OSError:
            pass


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
            print("[OTA] TCP conectado, enviando requisicao...")

        req = (
            "GET " + path + " HTTP/1.1\r\n"
            "Host: " + host + "\r\n"
            "User-Agent: access-ng-cerberos\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.write(req.encode("utf-8"))

        buf = b""
        status = None
        out = None
        total_bytes = None
        received = 0
        if dest_file:
            out = open(dest_file, "wb")
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
                    header_str = buf[:sep].decode("utf-8", "ignore")
                    status = int(header_str.split("\r\n", 1)[0].split()[1])
                    for line in header_str.split("\r\n")[1:]:
                        if line.lower().startswith("content-length:"):
                            try:
                                total_bytes = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                pass
                    if dest_file:
                        print("[OTA] Resposta recebida (status=%s, tamanho=%s)" %
                              (status, total_bytes if total_bytes is not None else "?"))
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
            print("[OTA] Download concluido: %d bytes" % received)
        return status, (None if out else buf.decode("utf-8", "ignore"))
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
        return tuple(int(p) for p in str(v).split("."))
    except (ValueError, AttributeError):
        return None


def check_for_update():
    if not OTA_ENABLED:
        return None
    status, body = _http_get("/access-ng/ota/" + OTA_VERSION_PATH)
    if status != 200 or not body:
        print("[OTA] Falha ao verificar version.json (status=%s)" % status)
        return None
    try:
        remote = json.loads(body)
    except Exception:
        print("[OTA] version.json invalido")
        return None

    remota_versao = remote.get("versao")
    remota_t = _parse_versao(remota_versao)
    atual_t = _parse_versao(FIRMWARE_VERSAO)
    if remota_t is None or atual_t is None:
        if remota_versao == FIRMWARE_VERSAO:
            return None
    elif remota_t <= atual_t:
        return None

    print("[OTA] Nova versao disponivel:", remota_versao)
    return remote


def _valida_payload(path, versao):
    try:
        if os.stat(path)[6] < 500:
            return False
        needle_fw = b"FIRMWARE_VERSAO"
        needle_ver = str(versao).encode("utf-8")
        found_fw = False
        found_ver = False
        tail = b""
        with open(path, "rb") as f:
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
        versao = remote.get("versao", "")
        path = "/access-ng/ota/" + OTA_FIRMWARE_PATH
        print("[OTA] Baixando", "http://" + OTA_HOST + path)
        status, _ = _http_request(OTA_HOST, path, dest_file="main.new", timeout=30)
        if status != 200 or not _valida_payload("main.new", versao):
            print("[OTA] Download invalido (status=%s) - abortando" % status)
            try:
                os.remove("main.new")
            except OSError:
                pass
            return False

        try:
            os.remove("main.bak")
        except OSError:
            pass
        os.rename("main.py", "main.bak")
        os.rename("main.new", "main.py")
        with open("ota_pending.txt", "w") as f:
            f.write(versao)
        try:
            os.remove("ota_boot_attempts.txt")
        except OSError:
            pass

        print("[OTA] Atualizado para", versao, "- reiniciando")
        time.sleep(1)
        _soft_reset()
    except Exception as e:
        print("[OTA] Erro ao aplicar atualizacao:", e)
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
# Mesmo mecanismo dos migradores do Caronte e do FECHO - ver a docstring de
# Hardware/Autenticador/CaronteESP32C3.py para o desenho completo.

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
    ("Hardware/Fechadura/device_defaults_esp32.py", "/device_defaults.py"),
]
# boot.py baixado com nome provisório - só vira boot.py de verdade depois de
# TODO o resto (inclusive o main.py novo) já ter validado.
_MIGRATION_BOOT_FILE = ("Hardware/Fechadura/boot_esp32.py", "/boot.py.new", "/boot.py")
_MIGRATION_MAIN_FILE = ("Hardware/Fechadura/main_esp32.py", "/main_target.new")


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
    raciocínio de _migration_validate_main nos outros dois migradores."""
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
        for repo_path, dest_path in _MIGRATION_SUPPORT_FILES:
            if not _migration_download(repo_path, dest_path):
                print("[Migração] Abortada")
                return False

        boot_repo, boot_staged, boot_final = _MIGRATION_BOOT_FILE
        if not _migration_download(boot_repo, boot_staged):
            print("[Migração] Abortada")
            return False

        main_repo, main_staged = _MIGRATION_MAIN_FILE
        # main.py definitivo é grande o bastante pra estourar memória no
        # compile() (mesmo problema visto em campo no Caronte) - validação
        # por streaming (não amarrada a nenhum número de versão específico).
        if not _migration_download(main_repo, main_staged, validate=_migration_validate_main):
            print("[Migração] Abortada")
            return False

        print("[Migração] Todos os arquivos validados - instalando...")

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
        time.sleep(1)
        _soft_reset()
    except Exception as e:
        print("[Migração] Erro inesperado:", e)
        return False


# --- MQTT --------------------------------------------------------------------

_client = None
_coldstart_result = None
_update_requested = False   # set pelo callback quando command=check_update chega
_migration_requested = False   # set pelo callback quando command=migrate chega


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
        topics["config_result"] = "%s/%s/cerberos/%s/config/result" % (
            MQTT_PREFIX,
            str(AMBIENTE_ID),
            mac,
        )
    return topics


def _publish_config():
    params = {}
    for key in _DEFAULTS:
        persistido = key in _cfg_file
        if key in _CONFIG_SENSITIVE:
            params[key] = {"persistido": persistido}
        else:
            params[key] = {"valor": globals().get(key, _DEFAULTS[key]), "persistido": persistido}
    topic = _topics().get("config_result")
    if topic:
        _client.publish(topic, json.dumps({"mac": DEVICE_MAC, "params": params}))
        print("[Config] Configuracao atual reportada")


def _apply_set_config(params):
    validos = {k: v for k, v in (params or {}).items() if k in _DEFAULTS}
    if not validos:
        print("[Config] set_config sem parametros validos, ignorando")
        return
    _cfg_file.update(validos)
    try:
        with open("config.json", "w") as f:
            json.dump(_cfg_file, f)
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
        if key not in _CONFIG_RUNTIME_KEYS or key not in _DEFAULTS:
            continue
        try:
            globals()[key] = type(_DEFAULTS[key])(value)
            print("[Config] %s sobrescrito para %r (somente sessao)" % (key, globals()[key]))
        except Exception:
            pass


def _on_message(topic, payload):
    global _unlock_flag, _coldstart_result, _update_requested, _migration_requested
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
        elif data.get("command") == "check_update":
            print("[MQTT] Solicitacao de verificacao de atualizacao recebida")
            _update_requested = True
        elif data.get("command") == "reboot":
            print("[MQTT] Comando de reinicio recebido - reiniciando...")
            status_pulse(200)
            time.sleep_ms(300)
            _soft_reset()
        elif data.get("command") == "get_config":
            print("[MQTT] Solicitacao de configuracao recebida")
            _publish_config()
        elif data.get("command") == "set_config":
            _apply_set_config(data.get("params"))
        elif data.get("command") == "migrate":
            print("[MQTT] Solicitação de migração para boot.py/main.py recebida")
            _migration_requested = True


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


def publish_entrada(pin_no):
    topic = _topics().get("entrada")
    if topic is None:
        return
    _client.publish(topic, json.dumps({
        "mac": DEVICE_MAC,
        "pin": pin_no,
    }))
    print("[Lock] Entrada fisica publicada (pin=%d)" % pin_no)


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
    global DEVICE_MAC, BOOT_COUNT, _input_pin, _unlock_flag, _update_requested, _migration_requested

    print("\n" + "=" * 48)
    print("  CERBEROS ESP32 - MQTT")
    print("=" * 48)

    _wdt_arm()

    BOOT_COUNT = _read_boot_count()
    _ota_boot_guard()
    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config("mac"), ":").decode()
    print("[Device] MAC: %s" % DEVICE_MAC)

    while not connect_wifi():
        _wdt_feed()
        _set_link(False)
        status_pulse(120)
        time.sleep(10)

    while True:
        _wdt_feed()
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

    last_ota_check = time.time()
    ota_check_and_maybe_apply()

    while True:
        _wdt_feed()
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
                ota_check_and_maybe_apply()
                last_ota_check = time.time()

            _client.check_msg()

            if _unlock_flag:
                _unlock_flag = False
                unlock_door("mqtt")

            if _update_requested:
                _update_requested = False
                ota_check_and_maybe_apply()
                last_ota_check = time.time()

            if _migration_requested:
                _migration_requested = False
                _do_migration()

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
