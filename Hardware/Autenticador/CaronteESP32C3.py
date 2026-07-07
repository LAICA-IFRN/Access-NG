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
    "AUTH_TIMEOUT_S"     : 5,

    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
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
    access-ng/{amb_id}/caronte/{mac}/command     -> comando check_update (servidor -> dispositivo)

  O MAC usa '-' no lugar de ':' nos tópicos.

--- OTA (atualização remota) --------------------------------------------------

  O firmware se atualiza buscando version.json em
  https://raw.githubusercontent.com/{OTA_REPO}/{ref}/{OTA_VERSION_PATH} (sem
  autenticação - repositório público). Se a versão remota difere de
  FIRMWARE_VERSAO, baixa o .py do ref indicado em OTA_FIRMWARE_PATH, valida,
  grava em main.new, troca com main.py (backup em main.bak) e reinicia.

  A checagem ocorre: (1) após o coldstart, (2) periodicamente a cada
  OTA_CHECK_INTERVAL segundos, (3) imediatamente ao receber
  {"command":"check_update"} no tópico de comando.

  Rede de segurança: se a versão nova não completar um coldstart com sucesso
  em até 3 boots, o dispositivo restaura automaticamente main.bak (versão
  anterior conhecida como boa) e reinicia - evita "brick" remoto.
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

try:
    import ssl
    _SSL_AVAILABLE = True
except ImportError:
    _SSL_AVAILABLE = False

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
    "OTA_ENABLED"        : True,
    "OTA_CHECK_INTERVAL" : 3600,
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
OTA_ENABLED        = cfg("OTA_ENABLED")
OTA_CHECK_INTERVAL = cfg("OTA_CHECK_INTERVAL")

MQTT_PREFIX = "access-ng"
DEVICE_MAC  = None
AMBIENTE_ID = None
BOOT_COUNT  = None

# --- OTA -----------------------------------------------------------------------

FIRMWARE_VERSAO   = "1.2.4"   # bump manual a cada release publicada
OTA_REPO          = "LAICA-IFRN/Access-NG"
OTA_VERSION_PATH  = "Hardware/Autenticador/version.json"
OTA_FIRMWARE_PATH = "Hardware/Autenticador/CaronteESP32C3.py"
OTA_HOST          = "raw.githubusercontent.com"

# --- DIAGNOSTICO -----------------------------------------------------------------

HARDWARE_INFO         = "Caronte ESP32-C3"
HEARTBEAT_DIAG_EVERY  = 10   # rssi/mem_free/cpu_temp vao a cada N heartbeats


_SOFT_RESET_FLAG = "soft_reset.flag"


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
    """Marca o proximo boot como soft-reset (mantem o contador) e reinicia."""
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
    """Sensor interno de temperatura - nao suportado em todos os builds do
    ESP32-C3; retorna None quando indisponivel."""
    try:
        import esp32
        return round((esp32.raw_temperature() - 32) * 5 / 9, 1)
    except Exception:
        return None


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


# --- OTA -----------------------------------------------------------------------

def _ota_boot_guard():
    """Roda antes de tudo no boot. Se há um update pendente que falhou em
    completar um coldstart por 3 boots seguidos, restaura main.bak (versão
    anterior conhecida como boa) e reinicia. Nunca levanta exceção - esse
    código não pode travar o boot normal (sem update pendente, é um no-op)."""
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
    """Chamado após o primeiro coldstart+heartbeat bem-sucedidos na versão
    atual: remove os marcadores de update pendente (a versão é considerada
    estável). main.bak permanece como rede de segurança até a próxima
    atualização."""
    for fname in ("ota_pending.txt", "ota_boot_attempts.txt"):
        try:
            os.remove(fname)
        except OSError:
            pass


def _https_get(path, host=None, timeout=10):
    """GET HTTPS simples. Retorna (status_code, body_str) ou (None, None)."""
    return _https_request(host or OTA_HOST, path, timeout=timeout)


def _https_request(host, path, dest_file=None, timeout=10):
    """GET HTTPS em host+path. Se dest_file for informado, grava o corpo da
    resposta direto nesse arquivo (streaming) e retorna (status, None);
    senão acumula o corpo em memória e retorna (status, body_str).
    Retorna (None, None) em qualquer falha de rede."""
    if not _SSL_AVAILABLE:
        print("[OTA] ssl indisponível neste build")
        return None, None
    sock = None
    t0 = time.time()
    try:
        ai   = socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM)
        addr = ai[0][-1]
        print("[OTA] %s -> %s" % (host, addr))
        sock = socket.socket()
        sock.settimeout(timeout)
        sock.connect(addr)
        if dest_file:
            print("[OTA] TCP conectado, iniciando TLS...")
        try:
            sock = ssl.wrap_socket(sock, server_hostname=host)
        except TypeError:
            sock = ssl.wrap_socket(sock)
        if dest_file:
            print("[OTA] TLS OK, enviando requisição...")

        req = (
            "GET " + path + " HTTP/1.1\r\n"
            "Host: " + host + "\r\n"
            "User-Agent: access-ng-caronte\r\n"
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
                    else:
                        buf += chunk
        finally:
            if out:
                out.close()

        if status is None:
            return None, None
        return status, (None if out else buf.decode("utf-8", "ignore"))
    except Exception as e:
        print("[OTA] Erro HTTPS (%.1fs):" % (time.time() - t0), e)
        return None, None
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def check_for_update():
    """Busca version.json no repo. Retorna o dict remoto se houver uma
    versão diferente da atual, ou None (sem update / qualquer falha)."""
    if not OTA_ENABLED:
        return None
    status, body = _https_get("/" + OTA_REPO + "/main/" + OTA_VERSION_PATH)
    if status != 200 or not body:
        return None
    try:
        remote = json.loads(body)
    except Exception:
        return None
    if remote.get("versao") == FIRMWARE_VERSAO:
        return None
    print("[OTA] Nova versão disponível:", remote.get("versao"))
    return remote


def _valida_payload(path, versao):
    """Checagem barata de sanidade do .py baixado antes de instalar."""
    try:
        if os.stat(path)[6] < 500:
            return False
        with open(path) as f:
            conteudo = f.read()
        return ("FIRMWARE_VERSAO" in conteudo) and (versao in conteudo)
    except Exception:
        return False


def apply_update(remote):
    """Baixa o firmware do ref indicado, valida, troca main.py e reinicia.
    Nunca propaga exceção - qualquer falha apenas aborta a atualização."""
    try:
        ref = remote.get("ref", "main")
        versao = remote.get("versao", "")
        path = "/" + OTA_REPO + "/" + ref + "/" + OTA_FIRMWARE_PATH
        print("[OTA] Baixando", "https://" + OTA_HOST + path)
        beep(60)
        status, _ = _https_request(OTA_HOST, path, dest_file="main.new", timeout=30)
        if status != 200 or not _valida_payload("main.new", versao):
            print("[OTA] Download inválido (status=%s) - abortando" % status)
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
        beep(60); time.sleep_ms(80); beep(60)
        time.sleep(1)
        _soft_reset()
    except Exception as e:
        print("[OTA] Erro ao aplicar atualização:", e)
        return False


def ota_check_and_maybe_apply():
    """Verifica e, se houver versão nova, aplica (reinicia em caso de sucesso)."""
    remote = check_for_update()
    if remote:
        apply_update(remote)


# --- MQTT --------------------------------------------------------------------

_client           = None
_coldstart_result = None
_auth_result      = None
_update_requested = False   # set pelo callback quando command=check_update chega


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
        topics["tag"]     = "%s/%s/caronte/%s/tag"     % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
        topics["result"]  = "%s/%s/caronte/%s/result"  % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
        topics["command"] = "%s/%s/caronte/%s/command" % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
    return topics


def _on_message(topic, payload):
    global _coldstart_result, _auth_result, _update_requested
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
    elif topic_str == topics.get("command"):
        if data.get("command") == "check_update":
            print("[MQTT] Solicitação de verificação de atualização recebida")
            _update_requested = True
        elif data.get("command") == "reboot":
            print("[MQTT] Comando de reinício recebido - reiniciando...")
            time.sleep_ms(300)
            _soft_reset()


def mqtt_connect():
    global _client
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
        try:
            _client.publish(
                _topics()["coldstart"],
                json.dumps({
                    "mac": DEVICE_MAC, "chave": DEVICE_KEY, "versao": FIRMWARE_VERSAO,
                    "boot_count": BOOT_COUNT, "hardware": HARDWARE_INFO,
                    "mcu": _read_mcu(), "ssid": WIFI_SSID,
                }),
                qos=0,
            )
            print("[MQTT] Coldstart publicado, aguardando confirmação...")

            t0 = time.time()
            while time.time() - t0 < 5:
                _client.check_msg()
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
            topics = _topics()
            _client.subscribe(topics["result"])
            _client.subscribe(topics["command"])
            print("[MQTT] Coldstart OK - ambiente_id=%s" % AMBIENTE_ID)
            return

        print("[MQTT] Coldstart negado/sem resposta (%s) - tentando em 15s..." %
              _coldstart_result)
        for _ in range(15):
            beep(40)
            time.sleep(1)


def _format_uptime(uptime_ms):
    total_s = uptime_ms // 1000
    days, rem = divmod(total_s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return "%dT%02d:%02d:%02d" % (days, hours, minutes, seconds)


_heartbeat_count = 0


def publish_heartbeat():
    global _heartbeat_count
    uptime_ms = time.ticks_ms()
    payload = {
        "mac": DEVICE_MAC,
        "uptime_ms": uptime_ms,
        "uptime_s": uptime_ms // 1000,
        "uptime": _format_uptime(uptime_ms),
        "ip": network.WLAN(network.STA_IF).ifconfig()[0],
        "versao": FIRMWARE_VERSAO,
    }
    _heartbeat_count += 1
    if _heartbeat_count % HEARTBEAT_DIAG_EVERY == 1:
        payload["rssi"] = _read_rssi()
        payload["mem_free"] = gc.mem_free()
        payload["cpu_temp"] = _read_cpu_temp()
    _client.publish(_topics()["heartbeat"], json.dumps(payload))


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
    global DEVICE_MAC, BOOT_COUNT, _auth_result, _wg_count, _update_requested

    print("\n" + "=" * 48)
    print("  CARONTE ESP32-C3 - MQTT + WIEGAND")
    print("=" * 48)

    BOOT_COUNT = _read_boot_count()
    _ota_boot_guard()

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
    _ota_confirmar_versao_boa()
    print("[Main] Operacional\n")

    last_ota_check = time.time()
    ota_check_and_maybe_apply()

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

            if OTA_ENABLED and time.time() - last_ota_check >= OTA_CHECK_INTERVAL:
                ota_check_and_maybe_apply()
                last_ota_check = time.time()

            _client.check_msg()

            if _update_requested:
                _update_requested = False
                ota_check_and_maybe_apply()
                last_ota_check = time.time()

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
