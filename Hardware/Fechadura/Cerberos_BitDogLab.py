"""
Cerberos + Caronte — MicroPython para BitDogLab V6 (Raspberry Pi Pico W)

Papéis do dispositivo:
  Caronte : lê o botão local → autentica na API → abre a porta
  Cerberos: aguarda comandos remotos (portal web) → abre a porta

Suporta dois protocolos: REST (padrão) e MQTT.

─── config.json (copie para a raiz do dispositivo e ajuste os valores) ──────────

{
    "WIFI_SSID"          : "nome-da-rede",
    "WIFI_PASS"          : "senha-da-rede",

    "PROTOCOLO"          : "rest",

    "API_HOST"           : "seu-servidor.exemplo.com",
    "API_PORT"           : 443,
    "API_TIMEOUT"        : 10,

    "DEVICE_KEY"         : "chave-cadastrada-no-banco",

    "COLDSTART_ENDPOINT" : "/access-ng/device/coldstart",
    "HEARTBEAT_ENDPOINT" : "/access-ng/device/heartbeat",
    "AUTH_ENDPOINT"      : "/access-ng/caronte/autenticarTag",
    "COMMAND_ENDPOINT"   : "/access-ng/device/command",

    "HEARTBEAT_INTERVAL" : 25,
    "COMMAND_WAIT"       : 8,
    "COMMAND_TIMEOUT"    : 12,

    "MQTT_BROKER"        : "broker.exemplo.com",
    "MQTT_PORT"          : 1883,
    "MQTT_USER"          : "",
    "MQTT_PASS"          : "",
    "MQTT_TLS"           : false,

    "BUTTON_PIN"         : 5,
    "BUTTON_DEBOUNCE_MS" : 50,
    "BUTTON_TAG"         : "btn_local",

    "LED_RED_PIN"        : 13,
    "LED_GREEN_PIN"      : 11,
    "LED_BLUE_PIN"       : 12,
    "RELAY_PIN"          : 15,
    "RELAY_ACTIVE_MS"    : 2000
}

Notas:
  - PROTOCOLO "rest" → HTTP/HTTPS (padrão); "mqtt" → MQTT.
  - API_PORT 443  → HTTPS (ssl); 80 → HTTP puro (só no modo REST).
  - DEVICE_KEY    deve bater com o campo 'chave' do Cerberos cadastrado no servidor.
  - BUTTON_TAG    deve ser uma TAG virtual cadastrada para o usuário que o botão representa.
  - HEARTBEAT_INTERVAL deve ser menor que OFFLINE_THRESHOLD do servidor (padrão 30 s).
  - Em modo MQTT, os tópicos usam '-' no lugar de ':' no MAC address.
  - O AMBIENTE_ID não é configurado no dispositivo: ele é obtido a partir da
    resposta do coldstart (REST ou MQTT). Se o coldstart for negado ou o
    dispositivo não estiver cadastrado, ele tenta novamente a cada 15s e não
    prossegue para a operação normal até receber "ok".
─────────────────────────────────────────────────────────────────────────────────
"""

import machine
import network
import socket
import time
import json
import ubinascii
try:
    import ssl
    _SSL_AVAILABLE = True
except ImportError:
    _SSL_AVAILABLE = False

# ─── CONFIGURAÇÃO ────────────────────────────────────────────────────────────────

_DEFAULTS = {
    # Rede
    "WIFI_SSID"           : "wIFRN-IoT",
    "WIFI_PASS"           : "deviceiotifrn",
    # Protocolo: "rest" ou "mqtt"
    "PROTOCOLO"           : "rest",
    # API REST
    "API_HOST"            : "laica.ifrn.edu.br",
    "API_PORT"            : 443,
    "API_TIMEOUT"         : 10,
    "DEVICE_KEY"          : "chave-do-dispositivo",
    # Endpoints REST (com prefixo do subpath do servidor)
    "COLDSTART_ENDPOINT"  : "/access-ng/device/coldstart",
    "HEARTBEAT_ENDPOINT"  : "/access-ng/device/heartbeat",
    "AUTH_ENDPOINT"       : "/access-ng/caronte/autenticarTag",
    "COMMAND_ENDPOINT"    : "/access-ng/device/command",
    # Comportamento REST
    "HEARTBEAT_INTERVAL"  : 25,
    "COMMAND_WAIT"        : 8,
    "COMMAND_TIMEOUT"     : 12,
    # MQTT
    "MQTT_BROKER"         : "broker.exemplo.com",
    "MQTT_PORT"           : 1883,
    "MQTT_USER"           : "",
    "MQTT_PASS"           : "",
    "MQTT_TLS"            : False,
    # Hardware
    "BUTTON_PIN"          : 5,
    "BUTTON_DEBOUNCE_MS"  : 50,
    "BUTTON_TAG"          : "btn_local",
    "LED_RED_PIN"         : 13,
    "LED_GREEN_PIN"       : 11,
    "LED_BLUE_PIN"        : 12,
    "RELAY_PIN"           : 15,
    "RELAY_ACTIVE_MS"     : 2000,
}

try:
    with open('config.json') as f:
        _file_cfg = json.load(f)
    print("[Config] config.json carregado")
except Exception:
    _file_cfg = {}
    print("[Config] Usando valores padrão")

def _cfg(key):
    v = _file_cfg.get(key, _DEFAULTS[key])
    return type(_DEFAULTS[key])(v)

WIFI_SSID          = _cfg('WIFI_SSID')
WIFI_PASS          = _cfg('WIFI_PASS')
PROTOCOLO          = _cfg('PROTOCOLO')
API_HOST           = _cfg('API_HOST')
API_PORT           = _cfg('API_PORT')
API_TIMEOUT        = _cfg('API_TIMEOUT')
DEVICE_KEY         = _cfg('DEVICE_KEY')
COLDSTART_ENDPOINT = _cfg('COLDSTART_ENDPOINT')
HEARTBEAT_ENDPOINT = _cfg('HEARTBEAT_ENDPOINT')
AUTH_ENDPOINT      = _cfg('AUTH_ENDPOINT')
COMMAND_ENDPOINT   = _cfg('COMMAND_ENDPOINT')
HEARTBEAT_INTERVAL = _cfg('HEARTBEAT_INTERVAL')
COMMAND_WAIT       = _cfg('COMMAND_WAIT')
COMMAND_TIMEOUT    = _cfg('COMMAND_TIMEOUT')
MQTT_BROKER        = _cfg('MQTT_BROKER')
MQTT_PORT          = _cfg('MQTT_PORT')
MQTT_USER          = _cfg('MQTT_USER')
MQTT_PASS          = _cfg('MQTT_PASS')
MQTT_TLS           = _cfg('MQTT_TLS')
BUTTON_PIN         = _cfg('BUTTON_PIN')
BUTTON_DEBOUNCE_MS = _cfg('BUTTON_DEBOUNCE_MS')
BUTTON_TAG         = _cfg('BUTTON_TAG')
LED_RED_PIN        = _cfg('LED_RED_PIN')
LED_GREEN_PIN      = _cfg('LED_GREEN_PIN')
LED_BLUE_PIN       = _cfg('LED_BLUE_PIN')
RELAY_PIN          = _cfg('RELAY_PIN')
RELAY_ACTIVE_MS    = _cfg('RELAY_ACTIVE_MS')

MQTT_PREFIX = 'access-ng'
DEVICE_MAC  = None
AMBIENTE_ID = None   # obtido a partir da resposta do coldstart

# ─── HARDWARE ────────────────────────────────────────────────────────────────────

button    = None
led_r     = None
led_g     = None
led_b     = None
relay     = None
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

    relay = machine.Pin(RELAY_PIN, machine.Pin.OUT, value=0)
    _led(0, 0, 0)
    print("[GPIO] Inicializado")


def _led(r, g, b):
    led_r.duty_u16(r * 257)
    led_g.duty_u16(g * 257)
    led_b.duty_u16(b * 257)


def _led_pulse(r, g, b, ms):
    _led(r, g, b); time.sleep_ms(ms); _led(0, 0, 0)


def led_ok():     _led_pulse(0, 255, 0, 400)
def led_denied(): _led_pulse(255, 0, 0, 1000)


def unlock_door():
    print("[Lock] Abrindo porta...")
    for _ in range(3):
        _led(0, 0, 255); time.sleep_ms(200)
        _led(0, 0, 0);   time.sleep_ms(100)
    relay.value(1)
    time.sleep_ms(RELAY_ACTIVE_MS)
    relay.value(0)
    print("[Lock] Porta fechada")


# ─── REDE ─────────────────────────────────────────────────────────────────────────

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    print(f"[WiFi] Conectando em {WIFI_SSID}...")
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
        if wlan.isconnected():
            print(f"[WiFi] IP: {wlan.ifconfig()[0]}")
            return True
        time.sleep(0.5)
    print("[WiFi] Falha na conexão")
    return False


# ─── REST — HTTP/HTTPS ────────────────────────────────────────────────────────────

def http_post(endpoint, data, timeout=None):
    """POST JSON para a API. Retorna (status_code, body) ou (None, None).

    Usa getaddrinfo() para resolver o hostname (obrigatório no MicroPython/lwIP)
    e envolve o socket em ssl quando API_PORT == 443.
    """
    sock = None
    t    = timeout or API_TIMEOUT
    use_ssl = (API_PORT == 443)
    try:
        ai   = socket.getaddrinfo(API_HOST, API_PORT, 0, socket.SOCK_STREAM)
        addr = ai[0][-1]

        sock = socket.socket()
        sock.settimeout(t)
        sock.connect(addr)

        if use_ssl:
            if not _SSL_AVAILABLE:
                raise Exception("ssl indisponível neste build")
            try:
                sock = ssl.wrap_socket(sock, server_hostname=API_HOST)
            except TypeError:
                sock = ssl.wrap_socket(sock)

        body_bytes = json.dumps(data).encode('utf-8')
        headers = (
            "POST " + endpoint + " HTTP/1.1\r\n"
            "Host: " + API_HOST + "\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: " + str(len(body_bytes)) + "\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.sendall(headers.encode('utf-8'))
        sock.sendall(body_bytes)

        resp = b""
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            resp += chunk

        resp_str = resp.decode('utf-8', 'ignore')
        _, _, resp_body = resp_str.partition('\r\n\r\n')
        status = int(resp_str.split('\r\n', 1)[0].split()[1])
        return status, resp_body

    except Exception as e:
        print("[HTTP] Erro:", e)
        return None, None
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


# ─── REST — Device lifecycle ──────────────────────────────────────────────────────

def rest_coldstart():
    """Faz coldstart e aguarda confirmação do servidor com o ambiente_id.

    Não retorna enquanto o servidor não responder status "ok" — repete a
    cada 15s em caso de "unknown"/"denied" ou falha de rede.
    """
    global AMBIENTE_ID
    while True:
        status, body = http_post(COLDSTART_ENDPOINT, {'mac': DEVICE_MAC, 'chave': DEVICE_KEY})
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if status in (200, 201) and data.get('status') == 'ok':
            AMBIENTE_ID = data.get('ambiente_id')
            print(f"[REST] Coldstart OK — ambiente_id={AMBIENTE_ID}")
            led_ok()
            return

        print(f"[REST] Coldstart negado/sem resposta (status={status}, {data}) — tentando em 15s...")
        led_denied()
        time.sleep(15)


def rest_heartbeat():
    status, _ = http_post(HEARTBEAT_ENDPOINT, {'mac': DEVICE_MAC})
    print("[REST] Heartbeat", "OK" if status in (200, 201) else "FALHOU")


def rest_caronte_button():
    print("[REST] Autenticando botão...")
    status, resp = http_post(AUTH_ENDPOINT, {
        'mac'  : DEVICE_MAC,
        'tag'  : BUTTON_TAG,
        'chave': DEVICE_KEY,
    })
    if status in (200, 201):
        try:
            allow = json.loads(resp).get('Allow', False)
            if isinstance(allow, str):
                allow = allow.lower() == 'true'
            if allow:
                print("[REST] Acesso autorizado!")
                unlock_door()
                # Drena o comando enfileirado no servidor para evitar abertura dupla
                http_post(COMMAND_ENDPOINT, {'mac': DEVICE_MAC, 'wait': 0}, timeout=3)
                return
        except Exception:
            pass
    print("[REST] Acesso negado")
    led_denied()


def rest_cerberos_poll():
    """Long-poll no servidor por comando de abertura remoto."""
    print("[REST] Aguardando comando remoto...")
    status, resp = http_post(
        COMMAND_ENDPOINT,
        {'mac': DEVICE_MAC, 'wait': COMMAND_WAIT},
        timeout=COMMAND_TIMEOUT,
    )
    if status == 200:
        try:
            if json.loads(resp).get('command') == 'unlock':
                print("[REST] Comando remoto recebido!")
                unlock_door()
                return True
        except Exception:
            pass
    return False


# ─── MQTT ─────────────────────────────────────────────────────────────────────────

_mqtt_client  = None
_mqtt_pending = False   # True quando comando de abertura chegou por MQTT


def _mac_safe():
    return DEVICE_MAC.replace(':', '-')


def _topics():
    """Retorna os tópicos derivados do MAC e, quando já conhecido, do ambiente."""
    mac    = _mac_safe()
    prefix = MQTT_PREFIX
    topics = {
        'coldstart'       : f'{prefix}/coldstart/{mac}',
        'coldstart_result': f'{prefix}/coldstart/{mac}/result',
        'heartbeat'       : f'{prefix}/heartbeat/{mac}',
    }
    if AMBIENTE_ID is not None:
        amb = str(AMBIENTE_ID)
        topics['tag']     = f'{prefix}/{amb}/caronte/{mac}/tag'
        topics['result']  = f'{prefix}/{amb}/caronte/{mac}/result'
        topics['command'] = f'{prefix}/{amb}/cerberos/{mac}/command'
    return topics


_mqtt_coldstart_result = None


def _mqtt_on_message(topic, payload):
    global _mqtt_pending, _mqtt_coldstart_result
    topic_str = topic.decode('utf-8')
    t = _topics()
    try:
        data = json.loads(payload)
    except Exception:
        return

    if topic_str == t['coldstart_result']:
        _mqtt_coldstart_result = data
    elif topic_str == t.get('command'):
        if data.get('command') == 'unlock':
            print("[MQTT] Comando de abertura recebido!")
            _mqtt_pending = True
    elif topic_str == t.get('result'):
        if data.get('allow'):
            print("[MQTT] Acesso autorizado pelo servidor!")
            unlock_door()
        else:
            print(f"[MQTT] Acesso negado: {data.get('motivo', '')}")
            led_denied()


def mqtt_connect():
    global _mqtt_client
    try:
        from umqtt.robust import MQTTClient
    except ImportError:
        try:
            from umqtt.simple import MQTTClient
        except ImportError:
            print("[MQTT] umqtt não disponível — instale micropython-umqtt.robust")
            return False

    cid = f'cerberos-{_mac_safe()}'
    try:
        kwargs = {
            'port'      : MQTT_PORT,
            'keepalive' : 30,
        }
        if MQTT_USER:
            kwargs['user']     = MQTT_USER
            kwargs['password'] = MQTT_PASS
        if MQTT_TLS:
            kwargs['ssl'] = True
        client = MQTTClient(cid, MQTT_BROKER, **kwargs)
        client.set_callback(_mqtt_on_message)
        client.connect()
        client.subscribe(_topics()['coldstart_result'])
        _mqtt_client = client
        print(f"[MQTT] Conectado ao broker {MQTT_BROKER}:{MQTT_PORT}")
        return True
    except Exception as e:
        print(f"[MQTT] Falha na conexão: {e}")
        return False


def mqtt_coldstart():
    """Publica coldstart e aguarda confirmação do servidor com o ambiente_id.

    Não retorna enquanto o servidor não responder status "ok" — repete a
    cada 15s em caso de "unknown"/"denied" ou ausência de resposta.
    """
    global AMBIENTE_ID, _mqtt_coldstart_result
    while True:
        _mqtt_coldstart_result = None
        try:
            _mqtt_client.publish(
                _topics()['coldstart'],
                json.dumps({'mac': DEVICE_MAC, 'chave': DEVICE_KEY}),
                qos=1
            )
            print("[MQTT] Coldstart publicado, aguardando confirmação...")

            t0 = time.time()
            while time.time() - t0 < 5:
                _mqtt_client.check_msg()
                if _mqtt_coldstart_result is not None:
                    break
                time.sleep_ms(100)
        except Exception as e:
            print(f"[MQTT] Erro coldstart: {e}")

        if _mqtt_coldstart_result and _mqtt_coldstart_result.get('status') == 'ok':
            AMBIENTE_ID = _mqtt_coldstart_result.get('ambiente_id')
            t = _topics()
            _mqtt_client.subscribe(t['command'])
            _mqtt_client.subscribe(t['result'])
            print(f"[MQTT] Coldstart OK — ambiente_id={AMBIENTE_ID}")
            led_ok()
            return

        print(f"[MQTT] Coldstart negado/sem resposta ({_mqtt_coldstart_result}) — tentando em 15s...")
        led_denied()
        time.sleep(15)


def _format_uptime(uptime_ms):
    total_s = uptime_ms // 1000
    days, rem = divmod(total_s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return "%dT%02d:%02d:%02d" % (days, hours, minutes, seconds)


def mqtt_heartbeat():
    if not _mqtt_client:
        return
    try:
        uptime_ms = time.ticks_ms()
        _mqtt_client.publish(_topics()['heartbeat'], json.dumps({
            'mac': DEVICE_MAC,
            'uptime_ms': uptime_ms,
            'uptime_s': uptime_ms // 1000,
            'uptime': _format_uptime(uptime_ms),
            'ip': network.WLAN(network.STA_IF).ifconfig()[0],
        }))
    except Exception as e:
        print(f"[MQTT] Erro heartbeat: {e}")


def mqtt_caronte_button():
    if not _mqtt_client:
        print("[MQTT] Sem conexão ao broker")
        led_denied()
        return
    print("[MQTT] Publicando TAG do botão...")
    try:
        _mqtt_client.publish(_topics()['tag'], json.dumps({
            'tag'  : BUTTON_TAG,
            'chave': DEVICE_KEY,
            'mac'  : DEVICE_MAC,
        }), qos=1)
    except Exception as e:
        print(f"[MQTT] Erro ao publicar TAG: {e}")
        led_denied()


# ─── MAIN ─────────────────────────────────────────────────────────────────────────

def main():
    global DEVICE_MAC, _btn_flag, _mqtt_pending

    print("\n" + "=" * 48)
    print("  CERBEROS + CARONTE — BitDogLab V6")
    print(f"  Protocolo: {PROTOCOLO.upper()}")
    print("=" * 48)

    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config('mac'), ':').decode()
    print(f"[Device] MAC: {DEVICE_MAC}")

    while not connect_wifi():
        print("[WiFi] Nova tentativa em 10s...")
        led_denied()
        time.sleep(10)

    # ── Modo MQTT ─────────────────────────────────────────────────────────
    if PROTOCOLO == 'mqtt':
        while not mqtt_connect():
            print("[MQTT] Tentando reconectar em 10s...")
            led_denied()
            time.sleep(10)

        mqtt_coldstart()
        last_heartbeat = time.time()
        print("[Main] Operacional (MQTT)\n")

        while True:
            try:
                if not network.WLAN(network.STA_IF).isconnected():
                    print("[WiFi] Reconectando...")
                    if connect_wifi():
                        mqtt_connect()
                        mqtt_coldstart()
                        last_heartbeat = time.time()
                    else:
                        time.sleep(5)
                        continue

                if _btn_flag:
                    _btn_flag = False
                    time.sleep_ms(BUTTON_DEBOUNCE_MS)
                    mqtt_caronte_button()

                if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                    mqtt_heartbeat()
                    last_heartbeat = time.time()

                # Processa mensagens MQTT pendentes (command / result)
                try:
                    _mqtt_client.check_msg()
                except Exception as e:
                    print(f"[MQTT] Erro check_msg: {e} — reconectando...")
                    if mqtt_connect():
                        mqtt_coldstart()

                if _mqtt_pending:
                    _mqtt_pending = False
                    unlock_door()

                time.sleep_ms(50)

            except Exception as e:
                print(f"[Main] Erro: {e}")
                time.sleep(1)

    # ── Modo REST ─────────────────────────────────────────────────────────
    else:
        rest_coldstart()
        last_heartbeat = time.time()
        print("[Main] Operacional (REST)\n")

        while True:
            try:
                if not network.WLAN(network.STA_IF).isconnected():
                    print("[WiFi] Conexão perdida — reconectando...")
                    if connect_wifi():
                        rest_coldstart()
                        last_heartbeat = time.time()
                    else:
                        time.sleep(5)
                        continue

                if _btn_flag:
                    _btn_flag = False
                    time.sleep_ms(BUTTON_DEBOUNCE_MS)
                    rest_caronte_button()

                if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                    rest_heartbeat()
                    last_heartbeat = time.time()

                if not _btn_flag:
                    rest_cerberos_poll()

            except Exception as e:
                print(f"[Main] Erro: {e}")
                time.sleep(1)


main()
