"""
Caronte ESP32-C3 - MicroPython MQTT + Wiegand RFID

Firmware para um Caronte com leitor Wiegand no ESP32 SSC C3.
Lê TAGs RFID, publica no broker MQTT e aguarda resultado de autorização.
Não possui Cerberos embutido — apenas leitura e publicação.

--- Arquivos no dispositivo ----------------------------------------------

  boot.py             -> supervisor mínimo: decide recovery vs main.py
  device_defaults.py  -> DEFAULTS/SENSITIVE_KEYS (dado puro, sem lógica)
  main.py             -> este arquivo, a aplicação em si
  accessng/           -> pacote compartilhado (config/wifi/recovery/
                          provisioning/ota), instalado uma vez, não
                          atualizado por OTA nesta fase
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

    "WG_D0_PIN"          : 5,
    "WG_D1_PIN"          : 7,
    "BUZZER_PIN"         : 6,
    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "WG_TIMEOUT_MS"      : 25,
    "AUTH_TIMEOUT_S"     : 5,

    "UART_ENABLED"       : false,
    "UART_ID"            : 1,
    "UART_TX_PIN"        : 21,
    "UART_RX_PIN"        : 20,
    "UART_BAUDRATE"      : 9600,
    "UART_KEEPALIVE_S"   : 5,

    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}

Se config.json estiver ausente/inválido, boot.py já intercepta antes deste
arquivo rodar e sobe o portal de provisionamento (AP AccessNG-Caronte-XXXX
em 192.168.4.1) - este arquivo sempre roda com um config.json válido.

--- Pinagem ESP32 SSC C3 -----------------------------------------------------

  GPIO 01 -> LED VM  (vermelho) — feedback de acesso negado
  GPIO 02 -> LED VD3 (verde 3)  — heartbeat visual (chase com VD2)
  GPIO 03 -> LED VD2 (verde 2)  — heartbeat visual (chase com VD3)
  GPIO 04 -> LED VD1 (verde 1)  — feedback de acesso permitido
  GPIO 05 -> Wiegand D0 (ativo baixo)
  GPIO 06 -> Buzzer (ativo alto)
  GPIO 07 -> Wiegand D1 (ativo baixo)

  GPIO 08 -> SDA display OLED  — não soldado nesta placa
  GPIO 09 -> SCL display OLED  — não soldado nesta placa
  GPIO 10 -> Enable RS485      — não soldado nesta placa
  GPIO 20 -> RX UART (link com o FECHO/Cerberos) — não soldado nesta placa
  GPIO 21 -> TX UART (link com o FECHO/Cerberos) — não soldado nesta placa

--- Heartbeat visual (LEDs VD2/VD3) --------------------------------------------

  Enquanto operacional (WiFi+MQTT conectados, aguardando leitura de TAG), um
  pulso curto alterna VD2 -> VD3 a cada HEARTBEAT_LED_INTERVAL_MS - indica
  visualmente "sistema online, esperando um comando". Implementado sem
  bloquear o loop principal (led_heartbeat(), baseado em time.ticks_ms());
  para automaticamente durante reconexão de WiFi/MQTT, já que só é chamado
  no trecho de operação normal do loop.

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

--- TAGs locais e fallback via UART com o FECHO -------------------------------

  O Caronte mantém uma cópia local das TAGs autorizadas para o ambiente em
  tags.json (lista de strings, mesmo formato hexadecimal de _decode_wiegand).
  Ela é atualizada via comando MQTT:

    {"command": "set_tags", "tags": ["0A1B2C3D", "..."]}

  no mesmo tópico access-ng/{amb_id}/caronte/{mac}/command usado para os
  demais comandos (reboot, check_update, get_config, set_config). Substitui
  a lista inteira e aplica imediatamente (sem reboot).

  Essa lista só é usada em modo de contingência: o fluxo normal continua
  sendo publicar a TAG via MQTT e aguardar o "result" do servidor
  (AUTH_TIMEOUT_S). Só quando esse fluxo não responde a tempo (broker fora
  do ar, servidor sem resposta) é que — com UART_ENABLED=true — o Caronte
  consulta a whitelist local e, se a TAG estiver autorizada, manda o pedido
  de liberação direto para o FECHO via UART (não depende do broker para
  essa decisão). Sem UART_ENABLED, uma falha de MQTT sempre nega o acesso,
  como hoje.

  UART_ENABLED (bool, default false) liga/desliga esse link serial com o
  FECHO/Cerberos (pinos UART_TX_PIN/UART_RX_PIN, ver pinagem acima). Requer
  reboot para valer (like os demais parâmetros de pino).

  Protocolo (mesmos quadros homologados com o módulo FECHO):

    7E LEN CMD [dados] CS

    CS fecha a soma de (LEN+CMD+dados+CS) em 0 mod 256 (complemento de 2).

    1. KEEP-ALIVE (Caronte -> FECHO)  : 7E 01 01 FE
       Enviado a cada UART_KEEPALIVE_S; sem resposta = FECHO offline.
    2. ACK (FECHO -> Caronte)         : 7E 01 13 EC
       Resposta do FECHO ao keep-alive (indica que está online).
    3. PERMITIDO (FECHO -> Caronte)   : 7E 01 02 FD
       FECHO liberou a fechadura para a TAG enviada.
    4. NEGADO (FECHO -> Caronte)      : 7E 01 03 FC
       FECHO recusou (ex.: proteção de solenóide em cooldown).
    5. ENVIO DE TAG (Caronte -> FECHO): 7E 06 04 [4B TAG] [0x1A ou 0x22] [CS]
       Só enviado para TAGs já autorizadas pela whitelist local (26 ou 34
       bits Wiegand — outros formatos não têm representação de 4 bytes e
       não usam esse fallback).

--- OTA (atualização remota) --------------------------------------------------

  O firmware se atualiza buscando version.json em
  http://{OTA_HOST}:{OTA_PORT}/ota/{OTA_VERSION_PATH} (HTTP puro, sem TLS —
  o handshake RSA estoura a memória disponível no ESP32-C3; os arquivos de
  OTA são públicos, sem segredo em trânsito), servido pelo proprio Access-NG.
  Se a versão remota difere de FIRMWARE_VERSAO, baixa o .py, valida, troca
  com main.py (backup em main.bak) - toda essa mecânica vive em
  accessng.ota, chamada daqui como accessng.ota.check_for_update()/
  apply_update()/confirm_boot_ok().

  A checagem ocorre: (1) após o coldstart, (2) periodicamente a cada
  OTA_CHECK_INTERVAL segundos, (3) imediatamente ao receber
  {"command":"check_update"} no tópico de comando.

  Rede de segurança: se a versão nova não completar um coldstart com sucesso
  em até 3 boots, boot.py restaura automaticamente main.bak (versão anterior
  conhecida como boa) e reinicia - evita "brick" remoto. Diferente da versão
  anterior deste firmware, esse guard agora também dispara para QUALQUER
  crash-loop de main.py (não só update pendente) - ver accessng/recovery.py.
"""

import machine
import network
import time
import json
import os
import ubinascii
import micropython
import gc

from device_defaults import DEFAULTS, SENSITIVE_KEYS
from accessng import config, wifi, ota

micropython.alloc_emergency_exception_buf(100)


# --- CONFIGURAÇÃO ------------------------------------------------------------

# Únicos que podem ser sobrescritos em memória (sem gravar em config.json) via
# um bloco "config" na resposta do coldstart — os demais dependem de pinos/
# hardware já inicializados antes do coldstart, exigindo reboot para valer.
_CONFIG_RUNTIME_KEYS = ("HEARTBEAT_INTERVAL", "OTA_CHECK_INTERVAL", "OTA_ENABLED",
                        "AUTH_TIMEOUT_S", "WG_TIMEOUT_MS")

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
WG_D0_PIN          = cfg("WG_D0_PIN")
WG_D1_PIN          = cfg("WG_D1_PIN")
BUZZER_PIN         = cfg("BUZZER_PIN")
LED_VM_PIN         = cfg("LED_VM_PIN")
LED_VD1_PIN        = cfg("LED_VD1_PIN")
LED_VD2_PIN        = cfg("LED_VD2_PIN")
LED_VD3_PIN        = cfg("LED_VD3_PIN")
WG_TIMEOUT_MS      = cfg("WG_TIMEOUT_MS")
AUTH_TIMEOUT_S     = cfg("AUTH_TIMEOUT_S")
UART_ENABLED       = cfg("UART_ENABLED")
UART_ID            = cfg("UART_ID")
UART_TX_PIN        = cfg("UART_TX_PIN")
UART_RX_PIN        = cfg("UART_RX_PIN")
UART_BAUDRATE      = cfg("UART_BAUDRATE")
UART_KEEPALIVE_S   = cfg("UART_KEEPALIVE_S")
OTA_ENABLED        = cfg("OTA_ENABLED")
OTA_CHECK_INTERVAL = cfg("OTA_CHECK_INTERVAL")

MQTT_PREFIX = "access-ng"
DEVICE_MAC  = None
AMBIENTE_ID = None
BOOT_COUNT  = None

# --- OTA -----------------------------------------------------------------------

FIRMWARE_VERSAO   = "1.4.0"   # bump manual a cada release publicada
OTA_VERSION_PATH  = "Hardware/Autenticador/version.json"
OTA_FIRMWARE_PATH = "Hardware/Autenticador/main.py"
OTA_HOST          = "laica.ifrn.edu.br"
# HTTP puro (sem TLS): o handshake TLS/RSA estoura a memoria disponivel no
# ESP32-C3 (MBEDTLS_ERR_RSA_PUBLIC_FAILED+MBEDTLS_ERR_MPI_ALLOC_FAILED). Os
# arquivos de OTA sao publicos (sem segredos), entao HTTP puro e aceitavel
# aqui — mesma logica de expor o broker MQTT em texto puro na porta 1883.
OTA_PORT          = 80

# Bibliotecas que este firmware precisa - accessng.ota.ensure_dependencies()
# busca as que ainda não existirem localmente. Mesma lista declarada em
# Hardware/Autenticador/version.json, campo "bibliotecas".
BIBLIOTECAS = ["umqtt/simple.py", "umqtt/robust.py"]

# --- DIAGNOSTICO -----------------------------------------------------------------

# O sufixo "(boot.py)" é um marcador deliberado: reportado no coldstart,
# grava direto em Caronte.hardware e aparece no painel admin - é o jeito
# mais simples de confirmar remotamente que a migração deu certo, já que
# migrador e main.py definitivo têm números de versão independentes (não
# dá pra confiar só no campo "Firmware" pra distinguir os dois).
HARDWARE_INFO         = "Caronte ESP32-C3 (boot.py)"
HEARTBEAT_DIAG_EVERY  = 10   # rssi/mem_free/cpu_temp/fs_free vao a cada N heartbeats


_SOFT_RESET_FLAG = "soft_reset.flag"


def _read_boot_count():
    """Conta reinicios "soft" (machine.reset() chamado pelo proprio firmware:
    OTA, comando de reboot). Um boot sem a flag de soft-reset e tratado como
    reinicio completo (energia caiu) e zera o contador. Diagnostico puro,
    reportado no coldstart - NAO tem relacao com boot_state.json/boot_count
    (esse e o contador de crash-loop de accessng, usado por boot.py)."""
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


def _read_fs_stats():
    """Espaço livre/total (bytes) do filesystem da placa - usado tanto para
    saber se cabe a próxima atualização OTA quanto o crescimento do
    tags.json. (None, None) se indisponível."""
    try:
        s = os.statvfs('/')
        frsize = s[1]
        return s[4] * frsize, s[2] * frsize  # (f_bavail, f_blocks) * f_frsize
    except Exception:
        return None, None


def _read_wifi_status():
    """Codigo bruto de network.WLAN.status() - o valor numerico varia por
    port/versao do MicroPython, por isso e reportado sem tentar traduzir."""
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
    """MAC do radio do Access Point atualmente associado - identifica qual AP
    fisico o dispositivo esta usando, diferente do IP do gateway (que costuma
    ser o mesmo em toda uma rede com multiplos APs sob o mesmo SSID). Tenta
    config('bssid') e status('bssid') primeiro - o parametro aceito varia por
    porta/build do MicroPython. Confirmado em campo que nenhum dos dois e
    suportado nesse build de ESP32 ("unknown config param"/"unknown status
    param"); como ultimo recurso, escaneia e casa pelo SSID atual - isso
    tira o radio do canal associado por um instante e pode interromper
    brevemente a conexao, entao so roda se os metodos diretos falharem."""
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


# Diagnostico de reconexao WiFi: contagem e ha quanto tempo desde a ultima,
# alem do codigo de status no momento em que a queda foi percebida (motivo
# aproximado da desconexao). Zerado a cada boot.
_wifi_reconnects = 0
_wifi_last_reconnect_s = None
_wifi_last_disconnect_status = None


def connect_wifi():
    """Wrapper fino sobre accessng.wifi.try_connect_once() que preserva o
    diagnostico de reconexao (contagem/timestamp/motivo) usado no
    heartbeat - accessng.wifi nao conhece esse conceito, e' especifico
    desta aplicacao."""
    global _wifi_reconnects, _wifi_last_reconnect_s, _wifi_last_disconnect_status
    if network.WLAN(network.STA_IF).isconnected():
        return True
    if _wifi_last_reconnect_s is not None:
        _wifi_last_disconnect_status = _read_wifi_status()
        _wifi_reconnects += 1
    _wifi_last_reconnect_s = time.time()
    print("[WiFi] Conectando em %s..." % WIFI_SSID)
    return wifi.try_connect_once(WIFI_SSID, WIFI_PASS)


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


# --- Heartbeat visual (VD2/VD3) -----------------------------------------------

HEARTBEAT_LED_INTERVAL_MS = 2000   # tempo entre pulsos (o "silencio" do padrao)
HEARTBEAT_LED_PULSE_MS    = 80     # duracao de cada LED aceso no chase

_heartbeat_led_step     = 0   # 0=aguardando, 1=VD2 aceso, 2=VD3 aceso
_heartbeat_led_last_ms  = 0


def led_heartbeat():
    """Pulso curto alternando VD2 -> VD3 a cada HEARTBEAT_LED_INTERVAL_MS -
    indica visualmente "sistema online, esperando comando". Não bloqueia:
    cada chamada só avança um passo do padrão se o tempo já decorreu, então
    precisa ser chamada a cada volta do loop principal (não usar sleep aqui)."""
    global _heartbeat_led_step, _heartbeat_led_last_ms
    agora = time.ticks_ms()
    decorrido = time.ticks_diff(agora, _heartbeat_led_last_ms)

    if _heartbeat_led_step == 0 and decorrido >= HEARTBEAT_LED_INTERVAL_MS:
        led_vd2.value(1)
        _heartbeat_led_step = 1
        _heartbeat_led_last_ms = agora
    elif _heartbeat_led_step == 1 and decorrido >= HEARTBEAT_LED_PULSE_MS:
        led_vd2.value(0)
        led_vd3.value(1)
        _heartbeat_led_step = 2
        _heartbeat_led_last_ms = agora
    elif _heartbeat_led_step == 2 and decorrido >= HEARTBEAT_LED_PULSE_MS:
        led_vd3.value(0)
        _heartbeat_led_step = 0
        _heartbeat_led_last_ms = agora


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


# --- TAGs locais (whitelist para fallback offline via UART) ------------------

_TAGS_FILE  = "tags.json"
_local_tags = set()


def _load_tags():
    global _local_tags
    try:
        with open(_TAGS_FILE) as f:
            _local_tags = set(json.load(f))
        print("[Tags] %d tag(s) local(is) carregada(s)" % len(_local_tags))
    except Exception:
        _local_tags = set()
        print("[Tags] Nenhuma whitelist local encontrada")


def _apply_set_tags(tags):
    """Recebido via comando MQTT set_tags: grava a lista de TAGs autorizadas
    localmente (usada só no fallback offline via UART - não substitui o fluxo
    normal de autenticação via MQTT). Substitui a lista anterior por inteiro
    e aplica de imediato, sem precisar de reboot."""
    global _local_tags
    if not isinstance(tags, list):
        print("[Tags] set_tags inválido (esperada uma lista), ignorando")
        return
    tags = [str(t).upper() for t in tags]
    try:
        with open(_TAGS_FILE, "w") as f:
            json.dump(tags, f)
    except Exception as e:
        print("[Tags] Erro ao gravar tags.json:", e)
        return
    _local_tags = set(tags)
    print("[Tags] %d tag(s) local(is) atualizada(s)" % len(_local_tags))


# --- UART / Protocolo FECHO ---------------------------------------------------
#
# Quadro: 7E LEN CMD [dados] CS — CS fecha a soma (LEN+CMD+dados+CS) em 0 mod
# 256 (complemento de 2). Ver docstring do módulo para a tabela de comandos.

_UART_STX            = 0x7E
_UART_CMD_KEEPALIVE  = 0x01
_UART_CMD_PERMITIDO  = 0x02
_UART_CMD_NEGADO     = 0x03
_UART_CMD_TAG        = 0x04
_UART_CMD_ACK        = 0x13

uart               = None
_uart_rx_buf       = bytearray()
_fecho_online      = False
_fecho_last_ack_s  = None


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
    """Consome o RX pendente e devolve o primeiro quadro completo e válido
    do buffer, ou None se ainda não há um quadro inteiro disponível. Lixo
    antes do STX e quadros com checksum inválido são descartados byte a
    byte (não trava o link em caso de ruído/dessincronia)."""
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


def uart_keepalive():
    _uart_send(_UART_CMD_KEEPALIVE)


def uart_poll():
    """Consome quadros do FECHO fora do fluxo de autenticação (ex.: ACK do
    keep-alive). Atualiza _fecho_online. Chamado a cada volta do loop
    principal quando UART_ENABLED."""
    global _fecho_online, _fecho_last_ack_s
    frame = _uart_read_frame()
    while frame is not None:
        cmd, _data = frame
        if cmd == _UART_CMD_ACK:
            _fecho_online = True
            _fecho_last_ack_s = time.time()
        frame = _uart_read_frame()


def _fecho_is_online():
    """True só se o último ACK de keep-alive chegou há menos de 3 intervalos
    de UART_KEEPALIVE_S - evita tentar o fallback (e pagar o timeout de
    fecho_send_tag) quando o FECHO está claramente desconectado."""
    return (_fecho_online and _fecho_last_ack_s is not None and
            time.time() - _fecho_last_ack_s < UART_KEEPALIVE_S * 3)


def fecho_send_tag(tag, wg_count, timeout_ms=1500):
    """Envia ao FECHO uma TAG já autorizada pela whitelist local e aguarda
    PERMITIDO/NEGADO. Só deve ser chamada para wg_count em (26, 34) - os
    únicos formatos com representação de 4 bytes no protocolo. Retorna True
    (fechadura liberada) ou False (negado ou sem resposta a tempo)."""
    tipo = wg_count if wg_count in (26, 34) else 26
    data = ubinascii.unhexlify(tag) + bytes([tipo])
    _uart_send(_UART_CMD_TAG, data)

    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        frame = _uart_read_frame()
        if frame is not None:
            cmd, _data = frame
            if cmd == _UART_CMD_PERMITIDO:
                return True
            if cmd == _UART_CMD_NEGADO:
                return False
        time.sleep_ms(10)
    print("[UART] FECHO não respondeu à TAG")
    return False


# --- OTA -----------------------------------------------------------------------

def ota_check_and_maybe_apply(state):
    """Verifica e, se houver versão nova, aplica (reinicia em caso de
    sucesso - o estado já fica persistido antes do reset)."""
    remote = ota.check_for_update(OTA_HOST, OTA_PORT, OTA_VERSION_PATH,
                                   FIRMWARE_VERSAO, OTA_ENABLED)
    if not remote:
        return
    beep(60)
    if ota.apply_update(state, OTA_HOST, OTA_PORT, OTA_FIRMWARE_PATH, remote,
                         target_file="main.py", backup_file="main.bak"):
        config.save_state(state)
        beep(60); time.sleep_ms(80); beep(60)
        time.sleep(1)
        _soft_reset()


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
        topics["config_result"] = "%s/%s/caronte/%s/config/result" % (MQTT_PREFIX, str(AMBIENTE_ID), mac)
    return topics


def _publish_config():
    """Reporta o config efetivo atual: para cada chave de DEFAULTS, o valor
    em uso agora (globals(), reflete tanto config.json quanto uma eventual
    sobrescrita de sessão via coldstart) e se ela está persistida no
    config.json (True) ou vem só do default/sessão (False). Campos sensíveis
    nunca têm o valor reportado, só a flag de persistência."""
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
    """Grava os parâmetros válidos em config.json e reinicia para aplicar
    de forma limpa (vários parâmetros só têm efeito na inicialização do
    hardware, ex. pinos)."""
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
    """Aplica em memória (sem tocar config.json) as chaves permitidas vindas
    no coldstart_result — vale só até o próximo reboot."""
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
        elif data.get("command") == "get_config":
            print("[MQTT] Solicitação de configuração recebida")
            _publish_config()
        elif data.get("command") == "set_config":
            _apply_set_config(data.get("params"))
        elif data.get("command") == "set_tags":
            _apply_set_tags(data.get("tags"))


def mqtt_connect():
    global _client
    # umqtt.simple e preferida de proposito: publish()/check_msg() propagam
    # OSError de verdade, o que aciona o except OSError do main() - que ja
    # faz a recuperacao completa e correta (reconecta + do_coldstart() +
    # reinscreve nos topicos). A umqtt.robust captura OSError sozinha e fica
    # tentando reconectar em loop silencioso (sem log, DEBUG=False por
    # padrao) dentro da propria chamada de publish()/check_msg(), travando
    # o loop principal por tempo indeterminado sem que nada apareca na
    # serial - e o reconnect() dela usa connect(False), que nao reinscreve
    # em nenhum topico, deixando o dispositivo surdo a comandos ate um
    # reboot completo. So cai para robust se simple nao estiver instalada.
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
                    "mcu": _read_mcu(), "ssid": WIFI_SSID, "rssi": _read_rssi(),
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
            _apply_session_config(_coldstart_result.get("config"))
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


def _format_uptime(uptime_s):
    days, rem = divmod(uptime_s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return "%dT%02d:%02d:%02d" % (days, hours, minutes, seconds)


# time.time() em vez de time.ticks_ms(): ticks_ms() estoura (volta a zero)
# depois de alguns dias de uptime continuo, o que faria o campo "uptime" do
# heartbeat saltar/zerar sozinho, parecendo um reboot que nao aconteceu.
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

    state = config.load_state()
    BOOT_COUNT = _read_boot_count()

    init_gpio()
    init_uart()
    _load_tags()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config("mac"), ":").decode()
    print("[Device] MAC: %s" % DEVICE_MAC)

    # Na prática, boot.py já deixou o rádio conectado - este laço serve de
    # rede de segurança caso algo tenha mudado entre um passo e outro.
    while not connect_wifi():
        beep(120)
        time.sleep(10)

    ota.ensure_dependencies(OTA_HOST, OTA_PORT, BIBLIOTECAS)

    tentativas_mqtt = 0
    while True:
        try:
            mqtt_connect()
            do_coldstart()
            break
        except Exception as e:
            tentativas_mqtt += 1
            print("[MQTT] Falha na conexão: %s (%d/5) - tentando em 10s..." %
                  (e, tentativas_mqtt))
            beep(120)
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

    last_uart_keepalive = time.time()

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

            led_heartbeat()

            # Leitura Wiegand completa: silêncio > WG_TIMEOUT_MS
            if _wg_count > 0 and time.ticks_diff(time.ticks_ms(), _wg_last_ms) > WG_TIMEOUT_MS:
                irq_state = machine.disable_irq()
                count = _wg_count
                _wg_count = 0
                machine.enable_irq(irq_state)

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
                    elif (_auth_result is None and UART_ENABLED and _fecho_is_online() and
                          count in (26, 34) and tag in _local_tags):
                        # MQTT nao respondeu (broker/servidor fora do ar) -
                        # decide pela whitelist local e pede a liberacao
                        # direto ao FECHO via UART.
                        print("[UART] MQTT sem resposta - usando whitelist local")
                        if fecho_send_tag(tag, count):
                            feedback_allow()
                        else:
                            feedback_deny()
                    else:
                        feedback_deny()
                    _auth_result = None

            if UART_ENABLED:
                uart_poll()
                if time.time() - last_uart_keepalive >= UART_KEEPALIVE_S:
                    uart_keepalive()
                    last_uart_keepalive = time.time()

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
