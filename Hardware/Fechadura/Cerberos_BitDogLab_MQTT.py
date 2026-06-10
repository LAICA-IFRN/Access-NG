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
    "AMBIENTE_ID"        : 1,

    "HEARTBEAT_INTERVAL" : 25,

    "BUTTON_PIN"         : 5,
    "BUTTON_DEBOUNCE_MS" : 50,
    "BUTTON_TAG"         : "btn_local",

    "LED_RED_PIN"        : 13,
    "LED_GREEN_PIN"      : 11,
    "LED_BLUE_PIN"       : 12,
    "RELAY_PIN"          : 15,
    "RELAY_ACTIVE_MS"    : 2000
}

─── Tópicos MQTT ─────────────────────────────────────────────────────────────

  Publica:
    access-ng/coldstart/{mac}                → boot do dispositivo
    access-ng/heartbeat/{mac}                → presença periódica
    access-ng/{amb_id}/caronte/{mac}/tag     → TAG RFID para autenticação

  Assina:
    access-ng/{amb_id}/cerberos/{mac}/command → comando de abertura (servidor → dispositivo)
    access-ng/{amb_id}/caronte/{mac}/result   → resultado da autenticação

  O MAC usa '-' no lugar de ':' nos tópicos.
  HEARTBEAT_INTERVAL deve ser menor que OFFLINE_THRESHOLD do servidor (padrão 30s).
──────────────────────────────────────────────────────────────────────────────
"""

import machine
import network
import socket
import time
import json
import ubinascii

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
    "AMBIENTE_ID"         : 1,
    "HEARTBEAT_INTERVAL"  : 25,
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
AMBIENTE_ID        = cfg('AMBIENTE_ID')
HEARTBEAT_INTERVAL = cfg('HEARTBEAT_INTERVAL')
BUTTON_PIN         = cfg('BUTTON_PIN')
BUTTON_DEBOUNCE_MS = cfg('BUTTON_DEBOUNCE_MS')
BUTTON_TAG         = cfg('BUTTON_TAG')
LED_RED_PIN        = cfg('LED_RED_PIN')
LED_GREEN_PIN      = cfg('LED_GREEN_PIN')
LED_BLUE_PIN       = cfg('LED_BLUE_PIN')
RELAY_PIN          = cfg('RELAY_PIN')
RELAY_ACTIVE_MS    = cfg('RELAY_ACTIVE_MS')

MQTT_PREFIX = 'access-ng'
DEVICE_MAC  = None

# ─── HARDWARE ─────────────────────────────────────────────────────────────────

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


def _pulse(r, g, b, ms):
    _led(r, g, b); time.sleep_ms(ms); _led(0, 0, 0)


def led_ok():     _pulse(0, 255, 0, 400)
def led_denied(): _pulse(255, 0, 0, 1000)


def unlock_door():
    print("[Lock] Abrindo porta...")
    for _ in range(3):
        _led(0, 0, 255); time.sleep_ms(200)
        _led(0, 0, 0);   time.sleep_ms(100)
    relay.value(1)
    time.sleep_ms(RELAY_ACTIVE_MS)
    relay.value(0)
    print("[Lock] Porta fechada")


# ─── WiFi ──────────────────────────────────────────────────────────────────────

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print(f"[WiFi] IP: {wlan.ifconfig()[0]}")
        return True
    print(f"[WiFi] Conectando em {WIFI_SSID}...")
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
        if wlan.isconnected():
            print(f"[WiFi] IP: {wlan.ifconfig()[0]}")
            return True
        time.sleep(0.5)
    print("[WiFi] Falha")
    return False


# ─── MQTT ─────────────────────────────────────────────────────────────────────

_client       = None
_unlock_flag  = False   # set pelo callback quando command=unlock chega


def _mac_safe():
    return DEVICE_MAC.replace(':', '-')


def _t():
    """Retorna os tópicos derivados do MAC e ambiente."""
    mac = _mac_safe()
    amb = str(AMBIENTE_ID)
    p   = MQTT_PREFIX
    return {
        'coldstart': f'{p}/coldstart/{mac}',
        'heartbeat': f'{p}/heartbeat/{mac}',
        'tag'      : f'{p}/{amb}/caronte/{mac}/tag',
        'result'   : f'{p}/{amb}/caronte/{mac}/result',
        'command'  : f'{p}/{amb}/cerberos/{mac}/command',
    }


def _on_message(topic, payload):
    global _unlock_flag
    topic_str = topic.decode('utf-8')
    topics    = _t()
    try:
        data = json.loads(payload)
    except Exception:
        return

    if topic_str == topics['command']:
        if data.get('command') == 'unlock':
            print("[MQTT] Comando de abertura recebido!")
            _unlock_flag = True

    elif topic_str == topics['result']:
        if data.get('allow'):
            print("[MQTT] Acesso autorizado!")
            unlock_door()
        else:
            print(f"[MQTT] Acesso negado: {data.get('motivo', '')}")
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


def ping_broker(count=4, timeout_ms=1000):
    """Envia pings ICMP ao broker para checar conectividade de rede."""
    import ustruct

    try:
        host_ip = socket.getaddrinfo(MQTT_BROKER, 1)[0][-1][0]
    except Exception as e:
        print(f"[Ping] Falha ao resolver {MQTT_BROKER}: {e}")
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
    print(f"[Ping] {host_ip}: {ok}/{count} respostas")


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

    kwargs = {'port': MQTT_PORT, 'keepalive': 30}
    if MQTT_USER:
        kwargs['user']     = MQTT_USER
        kwargs['password'] = MQTT_PASS
    if MQTT_TLS:
        kwargs['ssl'] = True

    c = MQTTClient(f'cerberos-{_mac_safe()}', MQTT_BROKER, **kwargs)
    c.set_callback(_on_message)
    c.connect()
    topics = _t()
    c.subscribe(topics['command'])
    c.subscribe(topics['result'])
    _client = c
    print(f"[MQTT] Conectado ao broker {MQTT_BROKER}:{MQTT_PORT}")


def publish_coldstart():
    _client.publish(_t()['coldstart'],
                    json.dumps({'mac': DEVICE_MAC, 'chave': DEVICE_KEY}),
                    qos=1)
    print("[MQTT] Coldstart publicado")
    led_ok()


def publish_heartbeat():
    _client.publish(_t()['heartbeat'], json.dumps({'mac': DEVICE_MAC}))


def publish_tag():
    print("[MQTT] Publicando TAG do botão...")
    _client.publish(_t()['tag'], json.dumps({
        'tag'  : BUTTON_TAG,
        'chave': DEVICE_KEY,
        'mac'  : DEVICE_MAC,
    }), qos=1)


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    global DEVICE_MAC, _btn_flag, _unlock_flag

    print("\n" + "=" * 48)
    print("  CERBEROS + CARONTE — BitDogLab V6 (MQTT)")
    print("=" * 48)

    init_gpio()

    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    DEVICE_MAC = ubinascii.hexlify(wlan.config('mac'), ':').decode()
    print(f"[Device] MAC: {DEVICE_MAC}")

    # WiFi
    while not connect_wifi():
        led_denied(); time.sleep(10)

    ping_broker()

    # MQTT
    while True:
        _diag_broker()
        try:
            mqtt_connect()
            break
        except Exception as e:
            print(f"[MQTT] Falha na conexão: {e} — tentando em 10s...")
            led_denied(); time.sleep(10)

    publish_coldstart()
    last_heartbeat = time.time()
    print("[Main] Operacional\n")

    while True:
        try:
            # Reconexão WiFi
            if not network.WLAN(network.STA_IF).isconnected():
                print("[WiFi] Reconectando...")
                if connect_wifi():
                    mqtt_connect()
                    publish_coldstart()
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

            # Processa mensagens MQTT recebidas
            _client.check_msg()

            # Abre a porta se comando chegou
            if _unlock_flag:
                _unlock_flag = False
                unlock_door()

            time.sleep_ms(50)

        except OSError as e:
            print(f"[MQTT] Erro de rede: {e} — reconectando...")
            try:
                mqtt_connect()
                publish_coldstart()
                last_heartbeat = time.time()
            except Exception:
                time.sleep(5)
        except Exception as e:
            print(f"[Main] Erro: {e}")
            time.sleep(1)


main()
