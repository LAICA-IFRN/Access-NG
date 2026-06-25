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
    "OLED_ADDR"          : 60
}

─── Tópicos MQTT ─────────────────────────────────────────────────────────────

  Publica:
    access-ng/coldstart/{mac}                → boot do dispositivo
    access-ng/heartbeat/{mac}                → presença periódica
    access-ng/{amb_id}/caronte/{mac}/tag     → TAG RFID para autenticação

  Assina:
    access-ng/coldstart/{mac}/result          → resposta do coldstart (status + ambiente_id)
    access-ng/{amb_id}/cerberos/{mac}/command → comando de abertura (servidor → dispositivo)
    access-ng/{amb_id}/caronte/{mac}/result   → resultado da autenticação

  O AMBIENTE_ID não é configurado no dispositivo: ele é obtido a partir da
  resposta do coldstart. Enquanto o coldstart não retornar status "ok"
  (MAC desconhecido ou chave inválida), o dispositivo repete a tentativa a
  cada 15s e não inicia a operação normal.

  O MAC usa '-' no lugar de ':' nos tópicos.
  HEARTBEAT_INTERVAL deve ser menor que OFFLINE_THRESHOLD do servidor (padrão 30s).
  Na BitDogLab, o OLED usa SCL=15 e SDA=14. Se RELAY_PIN também for 15 ou 14,
  o relé é desativado automaticamente para não conflitar com o display.
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

MQTT_PREFIX = 'access-ng'
DEVICE_MAC  = None
AMBIENTE_ID = None   # obtido a partir da resposta do coldstart

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
        i2c = SoftI2C(scl=Pin(OLED_SCL_PIN), sda=Pin(OLED_SDA_PIN))
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
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print(f"[WiFi] IP: {wlan.ifconfig()[0]}")
        display_message("WIFI", "Conectado", wlan.ifconfig()[0])
        return True
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


# ─── MQTT ─────────────────────────────────────────────────────────────────────

_client       = None
_unlock_flag  = False   # set pelo callback quando command=unlock chega


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
        topics['tag']     = f'{p}/{amb}/caronte/{mac}/tag'
        topics['result']  = f'{p}/{amb}/caronte/{mac}/result'
        topics['command'] = f'{p}/{amb}/cerberos/{mac}/command'
    return topics


_coldstart_result = None


def _on_message(topic, payload):
    global _unlock_flag, _coldstart_result
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
        _client.publish(_t()['coldstart'],
                        json.dumps({'mac': DEVICE_MAC, 'chave': DEVICE_KEY}),
                        qos=1)
        print("[MQTT] Coldstart publicado, aguardando confirmação...")
        display_message("COLDSTART", "Publicado", "aguardando...")

        t0 = time.time()
        while time.time() - t0 < 5:
            _client.check_msg()
            if _coldstart_result is not None:
                break
            time.sleep_ms(100)

        if _coldstart_result and _coldstart_result.get('status') == 'ok':
            AMBIENTE_ID = _coldstart_result.get('ambiente_id')
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


def publish_heartbeat():
    uptime_ms = time.ticks_ms()
    _client.publish(_t()['heartbeat'], json.dumps({
        'mac': DEVICE_MAC,
        'uptime_ms': uptime_ms,
        'uptime_s': uptime_ms // 1000,
    }))


def publish_tag():
    print("[MQTT] Publicando TAG do botão...")
    display_message("BOTAO", "Autenticando", BUTTON_TAG)
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

    init_display()
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

    ping_broker()

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
    print("[Main] Operacional\n")
    display_message("ACCESS-NG", "Operacional", f"Ambiente {AMBIENTE_ID}")

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

            # Processa mensagens MQTT recebidas
            _client.check_msg()

            # Abre a porta se comando chegou
            if _unlock_flag:
                _unlock_flag = False
                unlock_door()

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
