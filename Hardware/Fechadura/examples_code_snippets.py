"""
Exemplos de Código Úteis - BitDogLab V6 + MicroPython
Snippets para usar em projetos
"""

# =============================================================================
# 1. CONTROLE BÁSICO DE LED RGB
# =============================================================================

import machine
import time

def example_led_rgb():
    """Exemplo: Piscar LED em diferentes cores"""
    
    # Inicializa LEDs com PWM
    led_r = machine.PWM(machine.Pin(13))
    led_g = machine.PWM(machine.Pin(11))
    led_b = machine.PWM(machine.Pin(12))
    
    # Frequência de 1kHz
    for led in [led_r, led_g, led_b]:
        led.freq(1000)
    
    def set_color(r, g, b):
        """r, g, b: 0-255"""
        led_r.duty_u16(r * 257)
        led_g.duty_u16(g * 257)
        led_b.duty_u16(b * 257)
    
    # Ciclo de cores
    colors = [
        (255, 0, 0),    # Vermelho
        (0, 255, 0),    # Verde
        (0, 0, 255),    # Azul
        (255, 255, 0),  # Amarelo
        (255, 0, 255),  # Magenta
        (0, 255, 255),  # Ciano
    ]
    
    for color in colors:
        set_color(*color)
        time.sleep(1)
    
    set_color(0, 0, 0)  # Apaga

# =============================================================================
# 2. LEITURA DO BOTÃO A COM DEBOUNCE
# =============================================================================

def example_button_debounce():
    """Exemplo: Ler botão com debounce"""
    
    button_a = machine.Pin(5, machine.Pin.IN, machine.Pin.PULL_UP)
    
    def button_pressed():
        """Retorna True se botão foi pressionado (com debounce)"""
        if button_a.value() == 0:
            time.sleep(0.05)  # 50ms debounce
            if button_a.value() == 0:
                return True
        return False
    
    # Uso
    while True:
        if button_pressed():
            print("Botão pressionado!")
            # Aguarda soltura
            while button_a.value() == 0:
                time.sleep(0.1)
            time.sleep(0.05)  # Debounce na soltura também
        
        time.sleep(0.1)

# =============================================================================
# 3. CONEXÃO WiFi COM TIMEOUT
# =============================================================================

def example_wifi_connect():
    """Exemplo: Conectar à WiFi com tratamento de erro"""
    
    import network
    
    WIFI_SSID = "wIFRN-IoT"
    WIFI_PASS = "deviceiotifrn"
    TIMEOUT = 15  # segundos
    
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    
    elapsed = 0
    while not wlan.isconnected() and elapsed < TIMEOUT:
        time.sleep(0.5)
        elapsed += 0.5
        print(".", end="")
    
    if wlan.isconnected():
        print(f"\nConectado! IP: {wlan.ifconfig()[0]}")
        return True
    else:
        print(f"\nFalha! (timeout após {TIMEOUT}s)")
        return False

# =============================================================================
# 4. HTTP GET SIMPLES
# =============================================================================

def example_http_get(host, path, timeout=10):
    """Exemplo: Fazer requisição HTTP GET"""
    
    import socket
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        sock.connect((host, 80))
        
        request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
        sock.sendall(request.encode())
        
        response = b""
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk
        
        sock.close()
        return response.decode('utf-8', errors='ignore')
        
    except Exception as e:
        print(f"Erro: {e}")
        return None

# Uso:
# response = example_http_get("laica.ifrn.edu.br", "/api/status")
# print(response)

# =============================================================================
# 5. HTTP POST COM JSON
# =============================================================================

def example_http_post_json(host, path, data, timeout=10):
    """Exemplo: POST com JSON"""
    
    import socket
    import json
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        sock.connect((host, 80))
        
        json_data = json.dumps(data)
        
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(json_data)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{json_data}"
        )
        
        sock.sendall(request.encode())
        
        response = b""
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk
        
        sock.close()
        
        response_str = response.decode('utf-8', errors='ignore')
        # Separa header do body
        parts = response_str.split('\r\n\r\n', 1)
        body = parts[1] if len(parts) > 1 else ""
        
        return body
        
    except Exception as e:
        print(f"Erro: {e}")
        return None

# Uso:
# response = example_http_post_json(
#     "laica.ifrn.edu.br",
#     "/device/heartbeat",
#     {"mac": "AA:BB:CC:DD:EE:FF"},
#     timeout=10
# )
# print(response)

# =============================================================================
# 6. OBTER MAC ADDRESS
# =============================================================================

def example_get_mac():
    """Exemplo: Obter MAC address do Pico W"""
    
    import network
    import ubinascii
    
    wlan = network.WLAN(network.STA_IF)
    mac = ubinascii.hexlify(wlan.config('mac'), ':').decode()
    return mac

# Uso:
# mac = example_get_mac()
# print(f"MAC: {mac}")

# =============================================================================
# 7. THREAD/TAREFA PERIÓDICA
# =============================================================================

def example_periodic_task():
    """Exemplo: Executar tarefa periodicamente"""
    
    import threading
    
    def heartbeat_task():
        """Tarefa que roda a cada 30 segundos"""
        while True:
            print("Enviando heartbeat...")
            time.sleep(30)
    
    # Inicia thread em background
    thread = threading.Thread(target=heartbeat_task)
    thread.daemon = True
    thread.start()
    
    # Resto do código continua executando
    while True:
        print("Main loop")
        time.sleep(5)

# =============================================================================
# 8. PARSE JSON
# =============================================================================

def example_json_parse():
    """Exemplo: Parse de JSON"""
    
    import json
    
    # JSON simples
    json_str = '{"Allow": true, "message": "OK"}'
    data = json.loads(json_str)
    
    print(f"Allow: {data['Allow']}")
    print(f"Message: {data['message']}")
    
    # JSON complexo
    json_str = '''
    {
        "status": "ok",
        "device": "cerberos",
        "mac": "AA:BB:CC:DD:EE:FF",
        "cerberoses": [
            {"id": 1, "nome": "Porta1"},
            {"id": 2, "nome": "Porta2"}
        ]
    }
    '''
    
    data = json.loads(json_str)
    print(f"Status: {data['status']}")
    print(f"Cerberoses: {len(data['cerberoses'])}")
    for c in data['cerberoses']:
        print(f"  - {c['nome']} (id={c['id']})")

# =============================================================================
# 9. SALVAR/CARREGAR CONFIGURAÇÃO DE ARQUIVO
# =============================================================================

def example_config_file():
    """Exemplo: Salvar e carregar configuração"""
    
    import json
    
    # Salvar
    config = {
        "ssid": "wIFRN-IoT",
        "password": "deviceiotifrn",
        "api_host": "laica.ifrn.edu.br",
        "device_id": "5"
    }
    
    with open("config.json", "w") as f:
        json.dump(config, f)
    
    print("Configuração salva!")
    
    # Carregar
    with open("config.json", "r") as f:
        config = json.load(f)
    
    print(f"SSID: {config['ssid']}")
    print(f"Device ID: {config['device_id']}")

# =============================================================================
# 10. TIMER COM CALLBACK
# =============================================================================

def example_timer():
    """Exemplo: Timer com função de callback"""
    
    def callback(timer):
        print("Timer disparou!")
    
    # Timer que dispara a cada 1 segundo
    timer = machine.Timer()
    timer.init(period=1000, mode=machine.Timer.PERIODIC, callback=callback)
    
    # Aguarda 5 segundos
    time.sleep(5)
    
    # Para o timer
    timer.deinit()

# =============================================================================
# 11. SENSOR ADC (ANALÓGICO)
# =============================================================================

def example_adc():
    """Exemplo: Ler valor analógico (LDR, joystick, etc)"""
    
    # GPIO 26, 27, 28 são analógicos
    adc = machine.ADC(machine.Pin(28))
    
    while True:
        # Valor de 0 a 65535
        value = adc.read_u16()
        
        # Converte para 0-3.3V
        voltage = value * 3.3 / 65535
        
        print(f"ADC: {value} ({voltage:.2f}V)")
        time.sleep(0.5)

# =============================================================================
# 12. PWM - CONTROLE DE VELOCIDADE
# =============================================================================

def example_pwm_fade():
    """Exemplo: PWM com fade (aumenta/diminui brilho)"""
    
    led = machine.PWM(machine.Pin(13))
    led.freq(1000)
    
    # Fade in
    for i in range(0, 255, 5):
        led.duty_u16(i * 257)
        time.sleep(0.05)
    
    # Fade out
    for i in range(255, 0, -5):
        led.duty_u16(i * 257)
        time.sleep(0.05)
    
    led.duty_u16(0)

# =============================================================================
# 13. TRATAMENTO DE EXCEÇÃO E LOG
# =============================================================================

def example_error_handling():
    """Exemplo: Tratamento de erro com log"""
    
    def safe_http_request(host, path):
        """Faz requisição com retry"""
        import socket
        
        max_retries = 3
        retry = 0
        
        while retry < max_retries:
            try:
                print(f"[HTTP] Tentativa {retry + 1}/{max_retries}")
                
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((host, 80))
                
                request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
                sock.sendall(request.encode())
                
                response = sock.recv(1024)
                sock.close()
                
                print(f"[HTTP] Sucesso!")
                return response
                
            except socket.timeout:
                print(f"[HTTP] Timeout - tentando novamente...")
                retry += 1
                time.sleep(2)
                
            except Exception as e:
                print(f"[HTTP] Erro: {e}")
                retry += 1
                time.sleep(2)
        
        print(f"[HTTP] Falha após {max_retries} tentativas")
        return None
    
    # Uso
    response = safe_http_request("laica.ifrn.edu.br", "/api/status")

# =============================================================================
# 14. MATRIZ DE LEDS (NeoPixel)
# =============================================================================

def example_neopixel():
    """Exemplo: Controlar matriz de LEDs WS2812B (GPIO 7)"""
    
    # Requer biblioteca: micropython-neopixel
    # pip install micropython-neopixel
    
    try:
        from neopixel import NeoPixel
        
        np = NeoPixel(machine.Pin(7), 25)  # 25 LEDs (5x5)
        
        # Liga todos em vermelho
        for i in range(25):
            np[i] = (255, 0, 0)  # (R, G, B)
        np.write()
        
        time.sleep(1)
        
        # Apaga todos
        for i in range(25):
            np[i] = (0, 0, 0)
        np.write()
        
    except ImportError:
        print("NeoPixel library não instalada")

# =============================================================================
# 15. BOOT RÁPIDO - ARQUIVO main.py
# =============================================================================

def example_main_py():
    """
    Cole isto em main.py na placa para executar na inicialização
    """
    
    code = '''
import machine
import network
import time
import json
import socket

# Configuração
WIFI_SSID = "wIFRN-IoT"
WIFI_PASS = "deviceiotifrn"
API_HOST = "laica.ifrn.edu.br"
DEVICE_MAC = "AA:BB:CC:DD:EE:FF"

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    
    timeout = 15
    while not wlan.isconnected() and timeout > 0:
        time.sleep(0.5)
        timeout -= 1
        print(".", end="")
    
    return wlan.isconnected()

def send_heartbeat():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((API_HOST, 80))
    
    data = json.dumps({"mac": DEVICE_MAC})
    request = (
        f"POST /device/heartbeat HTTP/1.1\\r\\n"
        f"Host: {API_HOST}\\r\\n"
        f"Content-Type: application/json\\r\\n"
        f"Content-Length: {len(data)}\\r\\n"
        f"Connection: close\\r\\n"
        f"\\r\\n"
        f"{data}"
    )
    
    sock.sendall(request.encode())
    sock.close()

# Principal
print("Inicializando...")

if connect_wifi():
    print("\\nWiFi OK!")
    send_heartbeat()
    print("Heartbeat enviado!")
else:
    print("\\nWiFi FALHOU!")

# Seu código aqui...
while True:
    time.sleep(30)
    send_heartbeat()
'''
    
    return code

# =============================================================================

if __name__ == "__main__":
    print("Exemplos de Código - BitDogLab V6")
    print("Descomente a função que deseja testar e execute")
    print("\nFunções disponíveis:")
    print("  - example_led_rgb()")
    print("  - example_button_debounce()")
    print("  - example_wifi_connect()")
    print("  - example_http_get()")
    print("  - example_http_post_json()")
    print("  - example_get_mac()")
    print("  - example_json_parse()")
    print("  - example_config_file()")
    print("  - example_adc()")
    print("  - example_pwm_fade()")
    print("  - example_error_handling()")
    print("\n# Exemplo:")
    print("# example_led_rgb()")
