"""
Cerberos ESP32-C3 (FECHO) - MicroPython MQTT + UART

Firmware para o Cerberos ESP32-C3 dedicado a abrir a fechadura, apelidado de
"FECHO" pela equipe de hardware. Roda na mesma placa ESP32-C3 do Caronte
(Hardware/Autenticador/CaronteESP32C3.py), só que cumprindo o papel de
fechadura: LEDs de feedback, relé da tranca e, futuramente, display OLED.

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

    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}

--- Pinagem ESP32-C3 (FECHO) --------------------------------------------------

  Conforme pinagem definida pela equipe de hardware para o módulo FECHO:

  GPIO 01 -> LED VM  (vermelho)  - feedback de acesso negado
  GPIO 02 -> LED VD3 (verde 3)   - pulso de atividade (heartbeat/tráfego)
  GPIO 03 -> LED VD2 (verde 2)   - link WiFi+MQTT ok (aceso fixo)
  GPIO 04 -> LED VD1 (verde 1)   - feedback de acesso permitido
  GPIO 05 -> Botão PROG (ativo baixo) - reservado para modo AP/provisionamento;
             não há lógica de AP implementada nesta versão do firmware.
  GPIO 06 -> Relé da tranca (ativo alto)
  GPIO 07 -> SCL display OLED - reservado; sem driver de display nesta versão.
  GPIO 08 -> SDA display OLED - reservado; sem driver de display nesta versão.
  GPIO 20 -> RX UART (link com o Caronte) - mesma pinagem RS485/UART do
             Caronte ESP32-C3; conectar TX<->RX cruzado entre os dois módulos.
  GPIO 21 -> TX UART (link com o Caronte)

  Atenção (equipe de hardware): não acionar o relé por muito tempo, risco de
  queima da solenóide. RELAY_ACTIVE_MS é sempre limitado a 2000ms no código
  independente do que vier em config.json, e RELAY_COOLDOWN_MS impõe um
  intervalo mínimo entre acionamentos (protege contra picos de comando
  repetidos via MQTT/UART estressando a bobina em duty cycle).

--- UART (comunicação com o Caronte) -------------------------------------------

  UART_ENABLED (bool, default false) liga o link serial com o Caronte. Requer
  reboot para valer (como os demais parâmetros de pino).

  Protocolo homologado com a equipe de hardware:

    7E LEN CMD [dados] CS

    CS fecha a soma de (LEN+CMD+dados+CS) em 0 mod 256 (complemento de 2).

    1. KEEP-ALIVE (Caronte -> FECHO)  : 7E 01 01 FE
       FECHO responde com ACK se online; fica em silêncio caso contrário.
    2. ACK (FECHO -> Caronte)         : 7E 01 13 EC
    3. PERMITIDO (FECHO -> Caronte)   : 7E 01 02 FD
       FECHO liberou a fechadura para a TAG recebida.
    4. NEGADO (FECHO -> Caronte)      : 7E 01 03 FC
       FECHO recusou (ex.: RELAY_COOLDOWN_MS ainda não decorrido).
    5. ENVIO DE TAG (Caronte -> FECHO): 7E 06 04 [4B TAG] [0x1A ou 0x22] [CS]
       O FECHO não valida a TAG contra lista alguma - quem decide se a TAG
       pode acessar o ambiente é o Caronte (whitelist local, ver
       CaronteESP32C3.py); o FECHO só tenta liberar a tranca e informa se
       conseguiu (respeitando o cooldown de proteção da solenóide).

  Cada TAG recebida via UART também é publicada em
  access-ng/{amb_id}/cerberos/{mac}/uart_tag para auditoria no servidor.

--- Tópicos MQTT -------------------------------------------------------------

  Publica:
    access-ng/coldstart/{mac}                     -> boot do dispositivo
    access-ng/heartbeat/{mac}                     -> presença periódica
    access-ng/{amb_id}/cerberos/{mac}/uart_tag    -> TAG liberada via UART (auditoria)

  Assina:
    access-ng/coldstart/{mac}/result          -> resposta do coldstart
    access-ng/{amb_id}/cerberos/{mac}/command -> comando de abertura/check_update

  O MAC usa '-' no lugar de ':' nos tópicos.

--- OTA (atualização remota) --------------------------------------------------

  Mesmo esquema dos demais firmwares deste projeto, com arquivo de versão
  próprio (version_esp32c3.json, ao lado de version.json e version_esp32.json
  dos outros firmwares deste diretório) para que os três tenham ciclos de
  release independentes. Busca version_esp32c3.json em
  http://{OTA_HOST}:{OTA_PORT}/ota/{OTA_VERSION_PATH} (HTTP puro, sem TLS - o
  handshake RSA estoura a memória disponível no ESP32-C3; os arquivos de OTA
  são públicos, sem segredo em trânsito), servido pelo próprio Access-NG. Se a
  "versao" remota difere de FIRMWARE_VERSAO, baixa o .py em
  OTA_FIRMWARE_PATH, valida, grava em main.new, troca com main.py (backup em
  main.bak) e reinicia.

  Checagem em três momentos: após o coldstart, a cada OTA_CHECK_INTERVAL
  segundos, e imediatamente ao receber {"command":"check_update"} no tópico
  de comando (mesmo tópico usado para "unlock").

  Rede de segurança: se a versão nova não completar um coldstart em até 3
  boots, o dispositivo restaura main.bak (versão anterior conhecida como boa)
  e reinicia.
"""

import machine
import network
import socket
import time
import json
import os
import ubinascii
import gc


# --- CONFIGURAÇÃO --------------------------------------------------------------

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
    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "RELAY_PIN"          : 6,
    "RELAY_ACTIVE_MS"    : 2000,
    "RELAY_COOLDOWN_MS"  : 3000,
    "UART_ENABLED"       : False,
    "UART_ID"            : 1,
    "UART_TX_PIN"        : 21,
    "UART_RX_PIN"        : 20,
    "UART_BAUDRATE"      : 9600,
    "OTA_ENABLED"        : True,
    "OTA_CHECK_INTERVAL" : 3600,
}

# Nunca reportados por valor via MQTT (só é possível sobrescrever, não ler).
_CONFIG_SENSITIVE = ("WIFI_PASS", "DEVICE_KEY", "MQTT_PASS")
# Únicos que podem ser sobrescritos em memória (sem gravar em config.json) via
# um bloco "config" na resposta do coldstart - os demais dependem de pinos/
# hardware já inicializados antes do coldstart, exigindo reboot para valer.
_CONFIG_RUNTIME_KEYS = ("HEARTBEAT_INTERVAL", "OTA_CHECK_INTERVAL", "OTA_ENABLED")

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
OTA_ENABLED        = cfg("OTA_ENABLED")
OTA_CHECK_INTERVAL = cfg("OTA_CHECK_INTERVAL")

MQTT_PREFIX = "access-ng"
DEVICE_MAC  = None
AMBIENTE_ID = None
BOOT_COUNT  = None

# --- OTA -----------------------------------------------------------------

FIRMWARE_VERSAO   = "1.0.0"   # bump manual a cada release publicada
# Arquivo próprio (nem version.json nem version_esp32.json dos outros
# firmwares deste diretório) para que os três tenham ciclos de release
# independentes.
OTA_VERSION_PATH  = "Hardware/Fechadura/version_esp32c3.json"
OTA_FIRMWARE_PATH = "Hardware/Fechadura/CerberosESP32C3.py"
OTA_HOST          = "laica.ifrn.edu.br"
# HTTP puro (sem TLS): o handshake TLS/RSA estoura a memória disponível no
# ESP32-C3. Os arquivos de OTA são públicos (sem segredos), então HTTP puro é
# aceitável aqui - mesma lógica de expor o broker MQTT em texto puro na
# porta 1883.
OTA_PORT          = 80

# --- DIAGNÓSTICO -----------------------------------------------------------

HARDWARE_INFO         = "Cerberos ESP32-C3 (FECHO)"
HEARTBEAT_DIAG_EVERY  = 10   # rssi/mem_free/cpu_temp vão a cada N heartbeats


_SOFT_RESET_FLAG = "soft_reset.flag"


def _read_boot_count():
    """Conta reinícios "soft" (machine.reset() chamado pelo próprio firmware:
    OTA, comando de reboot, rollback). Um boot sem a flag de soft-reset é
    tratado como reinício completo (energia caiu) e zera o contador."""
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
    """Marca o próximo boot como soft-reset (mantém o contador) e reinicia."""
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
    """Sensor interno de temperatura - não suportado em todos os builds do
    ESP32-C3; retorna None quando indisponível."""
    try:
        import esp32
        return round((esp32.raw_temperature() - 32) * 5 / 9, 1)
    except Exception:
        return None


def _read_wifi_status():
    """Código bruto de network.WLAN.status() - o valor numérico varia por
    port/versão do MicroPython, por isso é reportado sem tentar traduzir."""
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
    """MAC do rádio do Access Point atualmente associado. Tenta config('bssid')
    e status('bssid') primeiro; como último recurso, escaneia e casa pelo
    SSID atual (tira o rádio do canal associado por um instante)."""
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


# Diagnóstico de reconexão WiFi: contagem e há quanto tempo desde a última,
# além do código de status no momento em que a queda foi percebida. Zerado a
# cada boot.
_wifi_reconnects = 0
_wifi_last_reconnect_s = None
_wifi_last_disconnect_status = None


# --- HARDWARE ----------------------------------------------------------------

led_vm  = None
led_vd1 = None
led_vd2 = None
led_vd3 = None
relay   = None

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
    """Aciona o relé por RELAY_ACTIVE_MS (sempre limitado a 2000ms). Recusa o
    acionamento se RELAY_COOLDOWN_MS ainda não decorreu desde o último
    (proteção da solenóide contra acionamentos repetidos em sequência).
    Retorna True se abriu, False se recusado por cooldown."""
    global _last_unlock_ms
    now = time.ticks_ms()
    if _last_unlock_ms is not None and time.ticks_diff(now, _last_unlock_ms) < RELAY_COOLDOWN_MS:
        print("[Lock] Acionamento recusado (%s) - cooldown da solenóide" % source)
        return False

    print("[Lock] Abrindo porta (%s)..." % source)
    _last_unlock_ms = now
    relay.value(1)
    time.sleep_ms(RELAY_ACTIVE_MS)
    relay.value(0)
    print("[Lock] Porta fechada")
    return True


# --- UART / Protocolo FECHO ---------------------------------------------------
#
# Quadro: 7E LEN CMD [dados] CS - CS fecha a soma (LEN+CMD+dados+CS) em 0 mod
# 256 (complemento de 2). Ver docstring do módulo para a tabela de comandos.

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
    """body = LEN+CMD+dados. CS = complemento de 2 da soma de body, de forma
    que sum(body) + CS feche em 0 mod 256."""
    return (-sum(body)) & 0xFF


def _uart_build_frame(cmd, data=b""):
    body = bytes([len(data) + 1, cmd]) + data
    return bytes([_UART_STX]) + body + bytes([_uart_checksum(body)])


def _uart_send(cmd, data=b""):
    if uart is None:
        return
    uart.write(_uart_build_frame(cmd, data))


def _uart_read_frame():
    """Consome o RX pendente e devolve o primeiro quadro completo e válido do
    buffer, ou None se ainda não há um quadro inteiro disponível. Lixo antes
    do STX e quadros com checksum inválido são descartados byte a byte."""
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
    """Processa quadros pendentes do Caronte: responde KEEP-ALIVE com ACK e
    TAG com PERMITIDO/NEGADO (após tentar abrir a porta). Chamado a cada
    volta do loop principal quando UART_ENABLED. Publica a TAG recebida em
    /uart_tag para auditoria (melhor esforço, não bloqueia a resposta)."""
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
            allowed = unlock_door("uart")
            _uart_send(_UART_CMD_PERMITIDO if allowed else _UART_CMD_NEGADO)
            if allowed:
                feedback_permitido()
            else:
                feedback_negado()
            publish_uart_tag(tag, tipo, allowed)
        frame = _uart_read_frame()


# --- WIFI --------------------------------------------------------------------

def connect_wifi():
    global _wifi_reconnects, _wifi_last_reconnect_s, _wifi_last_disconnect_status
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print("[WiFi] IP: %s" % wlan.ifconfig()[0])
        return True

    if _wifi_last_reconnect_s is not None:
        # já tinha conectado antes nesse boot - isso é uma reconexão, não a
        # conexão inicial. Guarda o status no momento da queda como motivo.
        _wifi_last_disconnect_status = _read_wifi_status()
        _wifi_reconnects += 1
    _wifi_last_reconnect_s = time.time()

    print("[WiFi] Conectando em %s..." % WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
        if wlan.isconnected():
            print("[WiFi] IP: %s" % wlan.ifconfig()[0])
            return True
        led_vd2.value(1 - led_vd2.value())
        time.sleep(0.5)

    led_vd2.value(0)
    print("[WiFi] Falha")
    return False


# --- OTA -----------------------------------------------------------------

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
            time.sleep(2)
            _soft_reset()
        else:
            print("[OTA] Boot %d/3 com update pendente" % tentativas)
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
            print("[OTA] Versão", FIRMWARE_VERSAO, "confirmada como estável")
        except OSError:
            pass


def _http_get(path, host=None, timeout=10):
    """GET HTTP simples (sem TLS). Retorna (status_code, body_str) ou (None, None)."""
    return _http_request(host or OTA_HOST, path, timeout=timeout)


def _http_request(host, path, dest_file=None, timeout=10):
    """GET HTTP em host+path (sem TLS). Se dest_file for informado, grava o
    corpo da resposta direto nesse arquivo (streaming) e retorna
    (status, None); senão acumula o corpo em memória e retorna
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
            "User-Agent: access-ng-cerberos-c3\r\n"
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
    """Converte "1.3.10" em (1, 3, 10) para comparação numérica. Comparar
    como string quebra em versões de dois dígitos."""
    try:
        return tuple(int(p) for p in str(v).split("."))
    except (ValueError, AttributeError):
        return None


def check_for_update():
    """Busca version_esp32c3.json no repo. Retorna o dict remoto se a versão
    remota for numericamente MAIOR que a atual, ou None (sem update / qualquer
    falha). Nunca reinstala uma versão igual ou mais antiga."""
    if not OTA_ENABLED:
        return None
    status, body = _http_get("/access-ng/ota/" + OTA_VERSION_PATH)
    if status != 200 or not body:
        print("[OTA] Falha ao verificar version_esp32c3.json (status=%s)" % status)
        return None
    try:
        remote = json.loads(body)
    except Exception:
        print("[OTA] version_esp32c3.json inválido")
        return None

    remota_versao = remote.get("versao")
    remota_t = _parse_versao(remota_versao)
    atual_t = _parse_versao(FIRMWARE_VERSAO)
    if remota_t is None or atual_t is None:
        if remota_versao == FIRMWARE_VERSAO:
            return None
    elif remota_t <= atual_t:
        return None

    print("[OTA] Nova versão disponível:", remota_versao)
    return remote


def _valida_payload(path, versao):
    """Checagem barata de sanidade do .py baixado antes de instalar. Lê em
    blocos para não carregar o firmware inteiro na RAM."""
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
    """Baixa o firmware, valida, troca main.py e reinicia. Nunca propaga
    exceção - qualquer falha apenas aborta a atualização."""
    try:
        versao = remote.get("versao", "")
        path = "/access-ng/ota/" + OTA_FIRMWARE_PATH
        print("[OTA] Baixando", "http://" + OTA_HOST + path)
        status, _ = _http_request(OTA_HOST, path, dest_file="main.new", timeout=30)
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

_client = None
_coldstart_result = None
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
        topics["command"] = "%s/%s/cerberos/%s/command" % (
            MQTT_PREFIX,
            str(AMBIENTE_ID),
            mac,
        )
        topics["uart_tag"] = "%s/%s/cerberos/%s/uart_tag" % (
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


def publish_uart_tag(tag, tipo, allowed):
    """Publica (melhor esforço) a TAG liberada via UART, para auditoria no
    servidor - a decisão de acesso em si já foi tomada pelo Caronte."""
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
    """Reporta o config efetivo atual: para cada chave de _DEFAULTS, o valor
    em uso agora (globals(), reflete tanto config.json quanto uma eventual
    sobrescrita de sessão via coldstart) e se ela está persistida no
    config.json (True) ou vem só do default/sessão (False). Campos sensíveis
    nunca têm o valor reportado, só a flag de persistência."""
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
        print("[Config] Configuração atual reportada")


def _apply_set_config(params):
    """Grava os parâmetros válidos em config.json e reinicia para aplicar de
    forma limpa (vários parâmetros só têm efeito na inicialização do
    hardware, ex. pinos)."""
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
    """Aplica em memória (sem tocar config.json) as chaves permitidas vindas
    no coldstart_result - vale só até o próximo reboot."""
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
    # umqtt.simple é preferida de propósito: publish()/check_msg() propagam
    # OSError de verdade, o que aciona o except OSError do main() - que já
    # faz a recuperação completa e correta (reconecta + do_coldstart() +
    # reinscreve nos tópicos). A umqtt.robust captura OSError sozinha e fica
    # tentando reconectar em loop silencioso dentro da própria chamada de
    # publish()/check_msg(), travando o loop principal por tempo
    # indeterminado. Só cai para robust se simple não estiver instalada.
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
            return

        print("[MQTT] Coldstart negado/sem resposta (%s) - tentando em 15s..." %
              _coldstart_result)
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


# time.time() em vez de time.ticks_ms(): ticks_ms() estoura (volta a zero)
# depois de alguns dias de uptime contínuo, o que faria o campo "uptime" do
# heartbeat saltar/zerar sozinho, parecendo um reboot que não aconteceu.
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
    _client.publish(_topics()["heartbeat"], json.dumps(payload))
    status_pulse()


# --- MAIN --------------------------------------------------------------------

def main():
    global DEVICE_MAC, BOOT_COUNT, _update_requested

    print("\n" + "=" * 48)
    print("  CERBEROS ESP32-C3 (FECHO) - MQTT + UART")
    print("=" * 48)

    BOOT_COUNT = _read_boot_count()
    _ota_boot_guard()
    init_gpio()
    init_uart()

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
            print("[MQTT] Falha na conexão: %s - tentando em 10s..." % e)
            _set_link(False)
            status_pulse(120)
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
