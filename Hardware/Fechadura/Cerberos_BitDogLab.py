"""
Cerberos - MicroPython para BitDogLab V6
Sistema de controle de acesso com autenticação por botão
Implementa: Coldstart, Heartbeat, Autenticação e Acionamento de LED
"""

import machine
import network
import socket
import time
import json
import ubinascii
import os

# ─── CONFIGURAÇÃO (carregada de config.json quando disponível) ──────────────

# Valores padrão (usados se config.json não estiver presente)
_DEFAULTS = {
    "WIFI_SSID": "wIFRN-IoT",
    "WIFI_PASS": "deviceiotifrn",
    "API_HOST": "laica.ifrn.edu.br",
    "API_PORT": 80,
    "API_TIMEOUT": 10,
    "DEVICE_ID": "5",
    "HEARTBEAT_INTERVAL": 30,
    "BUTTON_DEBOUNCE": 50,
    "BUTTON_A_PIN": 5,
    "LED_RED_PIN": 13,
    "LED_GREEN_PIN": 11,
    "LED_BLUE_PIN": 12,
    "COLDSTART_ENDPOINT": "/device/coldstart",
    "HEARTBEAT_ENDPOINT": "/device/heartbeat",
    "COMMAND_ENDPOINT": "/device/command",
    "COMMAND_POLL_WAIT": 20,
    "COMMAND_POLL_TIMEOUT": 30,
    "AUTH_ENDPOINT": "/caronte/autenticarTag",
}

# Tenta carregar arquivo config.json (opcional)
config = {}
try:
    with open('config.json', 'r') as f:
        try:
            config = json.load(f)
            print("[Config] Carregado config.json")
        except Exception as e:
            print(f"[Config] Erro ao parsear config.json: {e}")
            config = {}
except Exception:
    # Arquivo ausente — usará valores padrão
    config = {}

# Função helper para obter valores com fallback
def _cfg(key):
    return config.get(key, _DEFAULTS.get(key))

# WiFi
WIFI_SSID = _cfg('WIFI_SSID')
WIFI_PASS = _cfg('WIFI_PASS')

# API
API_HOST = _cfg('API_HOST')
API_PORT = int(_cfg('API_PORT'))
API_TIMEOUT = int(_cfg('API_TIMEOUT'))

# Identidade do dispositivo
DEVICE_ID = str(_cfg('DEVICE_ID'))
DEVICE_MAC = None  # Será obtido do hardware, não do config.json

# Endpoints da API
COLDSTART_ENDPOINT = _cfg('COLDSTART_ENDPOINT')
HEARTBEAT_ENDPOINT = _cfg('HEARTBEAT_ENDPOINT')
COMMAND_ENDPOINT = _cfg('COMMAND_ENDPOINT')
AUTH_ENDPOINT = _cfg('AUTH_ENDPOINT')

# Timings
HEARTBEAT_INTERVAL = int(_cfg('HEARTBEAT_INTERVAL'))  # segundos
BUTTON_DEBOUNCE = int(_cfg('BUTTON_DEBOUNCE'))     # milissegundos
COMMAND_POLL_WAIT = int(_cfg('COMMAND_POLL_WAIT'))  # segundos
COMMAND_POLL_TIMEOUT = int(_cfg('COMMAND_POLL_TIMEOUT'))  # segundos

# ─── HARDWARE - GPIO PINS ────────────────────────────────────────────────────

# Botão A (entrada)
BUTTON_A_PIN = 5

# LED RGB (saída)
LED_RED_PIN = 13
LED_GREEN_PIN = 11
LED_BLUE_PIN = 12

# ─── INICIALIZAÇÃO ───────────────────────────────────────────────────────────

def is_valid_mac(mac):
    """Valida se o valor é um MAC address real."""
    if not isinstance(mac, str):
        return False
    parts = mac.split(':')
    if len(parts) != 6:
        return False
    if all(part == '00' for part in parts):
        return False
    for part in parts:
        if len(part) != 2:
            return False
        try:
            int(part, 16)
        except ValueError:
            return False
    return True


def get_device_mac():
    """Obtém o MAC address do dispositivo Pico W."""
    import network
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    raw_mac = wlan.config('mac')
    if not raw_mac:
        return None
    mac = ubinascii.hexlify(raw_mac, ':').decode()
    return mac


def initialize_gpio():
    """Inicializa os pinos GPIO"""
    global button_a, led_red, led_green, led_blue
    
    # Botão A como entrada com pull-up
    button_a = machine.Pin(BUTTON_A_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    
    # LEDs como PWM para controle de intensidade
    led_red = machine.PWM(machine.Pin(LED_RED_PIN))
    led_red.freq(1000)
    
    led_green = machine.PWM(machine.Pin(LED_GREEN_PIN))
    led_green.freq(1000)
    
    led_blue = machine.PWM(machine.Pin(LED_BLUE_PIN))
    led_blue.freq(1000)
    
    # Apaga todos os LEDs inicialmente
    turn_off_all_leds()
    print("[GPIO] Pinos inicializados com sucesso")

def turn_off_all_leds():
    """Apaga todos os LEDs"""
    led_red.duty_u16(0)
    led_green.duty_u16(0)
    led_blue.duty_u16(0)

def set_led_color(r, g, b):
    """
    Define a cor do LED RGB
    r, g, b: 0-255
    """
    led_red.duty_u16(r * 257)
    led_green.duty_u16(g * 257)
    led_blue.duty_u16(b * 257)

def blink_led_success(duration=500):
    """Pisca LED verde brevemente para indicar sucesso"""
    set_led_color(0, 255, 0)  # Verde
    time.sleep(duration / 1000)
    turn_off_all_leds()

def blink_led_error(duration=500):
    """Pisca LED vermelho brevemente para indicar erro"""
    set_led_color(255, 0, 0)  # Vermelho
    time.sleep(duration / 1000)
    turn_off_all_leds()

def blink_led_unlock():
    """Aciona LED para simular desbloqueio da fechadura"""
    # Pisca em azul
    for _ in range(3):
        set_led_color(0, 0, 255)  # Azul
        time.sleep(200 / 1000)
        turn_off_all_leds()
        time.sleep(100 / 1000)

# ─── REDE ─────────────────────────────────────────────────────────────────────

def connect_wifi():
    """Conecta à rede WiFi"""
    print(f"[WiFi] Conectando em {WIFI_SSID}...")
    
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    
    # Aguarda conexão (máximo 15 segundos)
    timeout = 30
    while timeout > 0:
        if wlan.isconnected():
            print(f"[WiFi] Conectado!")
            print(f"[WiFi] IP: {wlan.ifconfig()[0]}")
            return True
        time.sleep(0.5)
        timeout -= 1
        print(".", end="")
    
    print("\n[WiFi] Falha na conexão")
    return False

# ─── HTTP CLIENT ──────────────────────────────────────────────────────────────

def http_post(endpoint, data, timeout=None):
    """
    Faz uma requisição POST HTTP
    Retorna: (status_code, response_text) ou (None, None) se erro
    """
    try:
        # Cria socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout if timeout is not None else API_TIMEOUT)
        
        # Conecta ao servidor
        print(f"[HTTP] Conectando em {API_HOST}:{API_PORT}...")
        sock.connect((API_HOST, API_PORT))
        
        # Prepara dados JSON
        json_data = json.dumps(data)
        
        # Monta requisição HTTP
        request = (
            f"POST {endpoint} HTTP/1.1\r\n"
            f"Host: {API_HOST}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(json_data)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{json_data}"
        )
        
        # Envia requisição
        sock.sendall(request.encode())
        print(f"[HTTP] POST {endpoint}")
        print(f"[HTTP] Dados: {json_data}")
        
        # Recebe resposta
        response = b""
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk
        
        sock.close()
        
        # Parse resposta
        try:
            response_str = response.decode('utf-8')
        except Exception:
            response_str = response.decode('latin-1')

        # Separa header do body
        parts = response_str.split('\r\n\r\n', 1)
        status_line = parts[0].split('\r\n')[0]
        body = parts[1] if len(parts) > 1 else ""
        
        # Extrai código de status
        status_code = int(status_line.split()[1])
        
        print(f"[HTTP] Status: {status_code}")
        print(f"[HTTP] Resposta: {body}")
        
        return status_code, body
        
    except Exception as e:
        print(f"[HTTP] Erro: {e}")
        return None, None
    finally:
        try:
            sock.close()
        except:
            pass

# ─── DEVICE LIFECYCLE ──────────────────────────────────────────────────────────

def coldstart():
    """
    Envia sinal de inicialização para a API
    A API registra coldstart_at, last_seen e status=online
    """
    print("[Device] Enviando coldstart...")
    
    data = {
        "mac": DEVICE_MAC,
        "chave": DEVICE_ID
    }
    
    status_code, response = http_post(COLDSTART_ENDPOINT, data)
    
    if status_code == 200 or status_code == 201:
        print("[Device] Coldstart bem-sucedido!")
        blink_led_success(200)
        return True
    else:
        print("[Device] Coldstart falhou!")
        blink_led_error(200)
        return False

def heartbeat():
    """
    Envia heartbeat periodicamente
    A API atualiza last_seen e status=online
    """
    print("[Device] Enviando heartbeat...")
    
    data = {"mac": DEVICE_MAC}
    
    status_code, response = http_post(HEARTBEAT_ENDPOINT, data)
    
    if status_code == 200 or status_code == 201:
        print("[Device] Heartbeat bem-sucedido!")
        return True
    else:
        print("[Device] Heartbeat falhou!")
        return False

# ─── AUTENTICAÇÃO ──────────────────────────────────────────────────────────────

def poll_command():
    """
    Mantem uma conexao HTTP aguardando comando de abertura.
    O servidor responde imediatamente quando houver um unlock pendente.
    """
    print("[Command] Aguardando comando do servidor...")

    data = {
        "mac": DEVICE_MAC,
        "wait": COMMAND_POLL_WAIT
    }

    status_code, response = http_post(
        COMMAND_ENDPOINT,
        data,
        timeout=COMMAND_POLL_TIMEOUT
    )

    if status_code == 200:
        try:
            response_json = json.loads(response)
            command = response_json.get("command")
            if command in ("unlock", "open", "abrir"):
                print("[Command] Comando de abertura recebido!")
                blink_led_unlock()
                acionamento_fechadura()
                return True
            print("[Command] Nenhum comando pendente")
            return False
        except Exception as e:
            print(f"[Command] Erro ao parsear comando: {e}")
            return False

    if status_code is not None:
        print(f"[Command] Falha ao buscar comando: HTTP {status_code}")
    return False


def authenticate_tag(tag="caronte", password=DEVICE_ID):
    """
    Autentica um tag/credencial no sistema
    Se autorizado, retorna True e aciona a fechadura
    """
    print(f"[Auth] Autenticando tag: {tag}...")
    
    data = {
        "mac": DEVICE_MAC,
        "tag": tag,
        "chave": password
    }
    
    status_code, response = http_post(AUTH_ENDPOINT, data)
    
    if status_code == 200 or status_code == 201:
        try:
            response_json = json.loads(response)
            allow = response_json.get("Allow", False)
            
            # Converte string "True"/"False" para booleano se necessário
            if isinstance(allow, str):
                allow = allow.lower() in ('true', '1', 'yes')
            
            if allow:
                print("[Auth] Autorização concedida!")
                blink_led_unlock()
                acionamento_fechadura()
                return True
            else:
                print("[Auth] Autorização negada!")
                blink_led_error(1000)
                return False
        except:
            print("[Auth] Erro ao parsear resposta")
            return False
    else:
        print("[Auth] Falha na autenticação")
        blink_led_error(1000)
        return False

def acionamento_fechadura():
    """Simula o acionamento da fechadura"""
    print("[Lock] Acionando fechadura...")
    # Já foi acionado pelo LED acima
    # Aqui você poderia controlar um relé se necessário

# ─── BOTÕES ────────────────────────────────────────────────────────────────────

def check_button_a():
    """
    Verifica o estado do botão A
    Retorna True se foi pressionado
    """
    if button_a.value() == 0:  # Botão ativo em nível baixo (PULL_UP)
        time.sleep(BUTTON_DEBOUNCE / 1000)  # Debounce
        if button_a.value() == 0:
            print("[Button] Botão A pressionado!")
            return True
    return False

def button_handler():
    """Loop para monitorar o botão A"""
    while True:
        try:
            if check_button_a():
                # Aguarda soltura do botão
                while button_a.value() == 0:
                    time.sleep(0.1)
                time.sleep(BUTTON_DEBOUNCE / 1000)
                
                # Simula credenciais do "caronte"
                # Em um sistema real, isso poderia vir de um cartão RFID
                authenticate_tag(tag="caronte_button", password=DEVICE_ID)
            
            time.sleep(0.1)
        except Exception as e:
            print(f"[Button] Erro no handler: {e}")
            time.sleep(1)

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    """Função principal"""
    global DEVICE_MAC
    
    print("\n" + "="*60)
    print("CERBEROS - Sistema de Controle de Acesso")
    print("BitDogLab V6 (Raspberry Pi Pico W)")
    print("="*60 + "\n")
    
    # Inicializa GPIO
    initialize_gpio()
    
    # Obtém MAC address do hardware
    DEVICE_MAC = get_device_mac()
    if not is_valid_mac(DEVICE_MAC):
        print(f"[Device] MAC inválido: {DEVICE_MAC}")
        blink_led_error(2000)
        return
    print(f"[Device] MAC Address: {DEVICE_MAC}")
    print(f"[Device] ID: {DEVICE_ID}")
    
    # Conecta à WiFi
    if not connect_wifi():
        print("[Main] Falha fatal: não foi possível conectar à WiFi")
        blink_led_error(2000)
        return
    
    # Envia coldstart
    if not coldstart():
        print("[Main] Aviso: coldstart falhou, continuando...")
    
    # Variável para controlar heartbeat
    last_heartbeat = time.time()
    
    print("\n[Main] Sistema pronto para operação")
    print("[Main] Aguardando comandos do servidor...\n")
    
    # Loop principal
    while True:
        try:
            # Envia heartbeat periodicamente
            now = time.time()
            if now - last_heartbeat > HEARTBEAT_INTERVAL:
                heartbeat()
                last_heartbeat = now
            
            # Verifica botao local antes de aguardar o servidor
            check_button_a()
            if button_a.value() == 0:
                # Aguarda soltura
                while button_a.value() == 0:
                    time.sleep(0.1)
                time.sleep(BUTTON_DEBOUNCE / 1000)
                
                # Autenticação
                authenticate_tag(tag="caronte_button", password="")

            poll_command()
            
        except Exception as e:
            print(f"[Main] Erro no loop principal: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
