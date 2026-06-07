"""
Cerberos + Caronte — MicroPython para BitDogLab V6 (Raspberry Pi Pico W)

Papéis do dispositivo:
  Caronte : lê o botão local → autentica na API → abre a porta
  Cerberos: faz long-poll na API por comandos remotos (portal web) → abre a porta
"""

import machine
import network
import socket
import time
import json
import ubinascii

# ─── CONFIGURAÇÃO ────────────────────────────────────────────────────────────────

_DEFAULTS = {
    # Rede
    "WIFI_SSID"         : "wIFRN-IoT",
    "WIFI_PASS"         : "deviceiotifrn",
    # API
    "API_HOST"          : "laica.ifrn.edu.br",
    "API_PORT"          : 80,
    "API_TIMEOUT"       : 10,
    "DEVICE_KEY"        : "chave-do-dispositivo",  # campo 'chave' no banco
    # Comportamento
    "HEARTBEAT_INTERVAL": 25,    # segundos — deve ser < OFFLINE_THRESHOLD do servidor (30s)
    "COMMAND_WAIT"      : 8,     # segundos que o servidor aguarda por comando (long-poll)
    "COMMAND_TIMEOUT"   : 12,    # timeout do socket do command poll
    # Hardware
    "BUTTON_PIN"        : 5,
    "BUTTON_DEBOUNCE_MS": 50,
    "BUTTON_TAG"        : "btn_local",  # TAG virtual cadastrada no sistema para este botão
    "LED_RED_PIN"       : 13,
    "LED_GREEN_PIN"     : 11,
    "LED_BLUE_PIN"      : 12,
    "RELAY_PIN"         : 15,    # pino do relé que controla a fechadura
    "RELAY_ACTIVE_MS"   : 2000,  # tempo em que o relé fica ativo (porta aberta)
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
    return type(_DEFAULTS[key])(v)   # garante o tipo correto (int/str)

WIFI_SSID          = _cfg('WIFI_SSID')
WIFI_PASS          = _cfg('WIFI_PASS')
API_HOST           = _cfg('API_HOST')
API_PORT           = _cfg('API_PORT')
API_TIMEOUT        = _cfg('API_TIMEOUT')
DEVICE_KEY         = _cfg('DEVICE_KEY')
HEARTBEAT_INTERVAL = _cfg('HEARTBEAT_INTERVAL')
COMMAND_WAIT       = _cfg('COMMAND_WAIT')
COMMAND_TIMEOUT    = _cfg('COMMAND_TIMEOUT')
BUTTON_PIN         = _cfg('BUTTON_PIN')
BUTTON_DEBOUNCE_MS = _cfg('BUTTON_DEBOUNCE_MS')
BUTTON_TAG         = _cfg('BUTTON_TAG')
LED_RED_PIN        = _cfg('LED_RED_PIN')
LED_GREEN_PIN      = _cfg('LED_GREEN_PIN')
LED_BLUE_PIN       = _cfg('LED_BLUE_PIN')
RELAY_PIN          = _cfg('RELAY_PIN')
RELAY_ACTIVE_MS    = _cfg('RELAY_ACTIVE_MS')

DEVICE_MAC = None

# ─── HARDWARE ────────────────────────────────────────────────────────────────────

button    = None
led_r     = None
led_g     = None
led_b     = None
relay     = None
_btn_flag = False   # setado pelo IRQ; lido no loop principal


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

    # Relé ativo em nível alto; inicia desligado
    relay = machine.Pin(RELAY_PIN, machine.Pin.OUT, value=0)

    _led(0, 0, 0)
    print("[GPIO] Inicializado")


def _led(r, g, b):
    led_r.duty_u16(r * 257)
    led_g.duty_u16(g * 257)
    led_b.duty_u16(b * 257)


def _led_pulse(r, g, b, ms):
    _led(r, g, b); time.sleep_ms(ms); _led(0, 0, 0)


def led_ok():
    _led_pulse(0, 255, 0, 400)


def led_denied():
    _led_pulse(255, 0, 0, 1000)


def unlock_door():
    """Aciona o relé por RELAY_ACTIVE_MS ms e sinaliza com LED azul."""
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
    for _ in range(30):          # aguarda até 15s
        if wlan.isconnected():
            print(f"[WiFi] IP: {wlan.ifconfig()[0]}")
            return True
        time.sleep(0.5)
    print("[WiFi] Falha na conexão")
    return False


def http_post(endpoint, data, timeout=None):
    """POST JSON para a API. Retorna (status_code, body) ou (None, None)."""
    sock = None
    try:
        sock = socket.socket()
        sock.settimeout(timeout or API_TIMEOUT)
        sock.connect((API_HOST, API_PORT))

        body = json.dumps(data)
        req = (
            f"POST {endpoint} HTTP/1.1\r\n"
            f"Host: {API_HOST}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
            f"{body}"
        )
        sock.sendall(req.encode())

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
        print(f"[HTTP] Erro: {e}")
        return None, None
    finally:
        if sock:
            try: sock.close()
            except: pass


# ─── DEVICE LIFECYCLE ─────────────────────────────────────────────────────────────

def coldstart():
    status, _ = http_post('/device/coldstart', {'mac': DEVICE_MAC, 'chave': DEVICE_KEY})
    ok = status in (200, 201)
    print(f"[Device] Coldstart {'OK' if ok else 'FALHOU'}")
    led_ok() if ok else led_denied()
    return ok


def heartbeat():
    status, _ = http_post('/device/heartbeat', {'mac': DEVICE_MAC})
    print(f"[Device] Heartbeat {'OK' if status in (200, 201) else 'FALHOU'}")


# ─── CARONTE — Botão local ─────────────────────────────────────────────────────────

def caronte_button():
    """Autentica o botão local na API e abre a porta se autorizado."""
    print("[Caronte] Autenticando botão...")
    status, resp = http_post('/caronte/autenticarTag', {
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
                print("[Caronte] Acesso autorizado!")
                unlock_door()
                return
        except Exception:
            pass
    print("[Caronte] Acesso negado")
    led_denied()


# ─── CERBEROS — Comando remoto ─────────────────────────────────────────────────────

def cerberos_poll():
    """Long-poll no servidor por comando de abertura remoto (portal web)."""
    print("[Cerberos] Aguardando comando remoto...")
    status, resp = http_post(
        '/device/command',
        {'mac': DEVICE_MAC, 'wait': COMMAND_WAIT},
        timeout=COMMAND_TIMEOUT,
    )
    if status == 200:
        try:
            if json.loads(resp).get('command') == 'unlock':
                print("[Cerberos] Comando remoto recebido!")
                unlock_door()
                return True
        except Exception:
            pass
    return False


# ─── MAIN ─────────────────────────────────────────────────────────────────────────

def main():
    global DEVICE_MAC, _btn_flag

    print("\n" + "=" * 48)
    print("  CERBEROS + CARONTE — BitDogLab V6")
    print("=" * 48)

    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config('mac'), ':').decode()
    print(f"[Device] MAC: {DEVICE_MAC}")

    while not connect_wifi():
        print("[WiFi] Aguardando rede — nova tentativa em 10s...")
        led_denied()
        time.sleep(10)

    coldstart()
    last_heartbeat = time.time()
    print("[Main] Operacional\n")

    while True:
        try:
            # Reconexão automática se WiFi cair
            if not network.WLAN(network.STA_IF).isconnected():
                print("[WiFi] Conexão perdida — reconectando...")
                if connect_wifi():
                    coldstart()
                    last_heartbeat = time.time()
                else:
                    time.sleep(5)
                    continue

            # Caronte: botão local
            # A confirmação por button.value() foi removida intencionalmente:
            # o poll bloqueia até COMMAND_TIMEOUT segundos, então quando a flag
            # é checada o botão já foi solto — o IRQ em si é suficiente como fonte.
            if _btn_flag:
                _btn_flag = False
                time.sleep_ms(BUTTON_DEBOUNCE_MS)  # filtra bouncing mecânico
                caronte_button()

            # Heartbeat periódico
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                heartbeat()
                last_heartbeat = time.time()

            # Cerberos: long-poll por comando remoto
            # Pulado se o botão já foi pressionado neste ciclo para responder
            # imediatamente; caso contrário bloqueia até COMMAND_TIMEOUT.
            if not _btn_flag:
                cerberos_poll()

        except Exception as e:
            print(f"[Main] Erro: {e}")
            time.sleep(1)


main()
