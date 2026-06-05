"""
Teste de Componentes - BitDogLab V6
Valida cada periférico da placa antes de usar Cerberos completo
"""

import machine
import time

def print_header(text):
    """Imprime header formatado"""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")

def test_button_a():
    """Testa Botão A (GPIO 5)"""
    print_header("TESTE: BOTÃO A")
    
    button = machine.Pin(5, machine.Pin.IN, machine.Pin.PULL_UP)
    print("[Test] Botão A configurado em GPIO 5")
    print("[Test] Pressione o Botão A 3 vezes (30 segundos de timeout)...\n")
    
    presses = 0
    timeout = 30
    start = time.time()
    
    while presses < 3 and (time.time() - start) < timeout:
        if button.value() == 0:  # Ativo em nível baixo
            presses += 1
            print(f"[Test] ✓ Botão pressionado! ({presses}/3)")
            # Aguarda soltura
            while button.value() == 0:
                time.sleep(0.1)
            time.sleep(0.5)
        else:
            time.sleep(0.1)
    
    if presses == 3:
        print("[Test] ✓ Botão A funcionando corretamente!")
        return True
    else:
        print("[Test] ✗ Botão A não respondeu")
        return False

def test_led_rgb():
    """Testa LED RGB (GPIOs 13, 11, 12)"""
    print_header("TESTE: LED RGB")
    
    try:
        r = machine.PWM(machine.Pin(13))
        g = machine.PWM(machine.Pin(11))
        b = machine.PWM(machine.Pin(12))
        
        r.freq(1000)
        g.freq(1000)
        b.freq(1000)
        
        def set_color(rv, gv, bv):
            r.duty_u16(rv * 257)
            g.duty_u16(gv * 257)
            b.duty_u16(bv * 257)
        
        print("[Test] LED RGB configurado")
        print("[Test] Testando cores...\n")
        
        # Vermelho
        print("[Test] Acendendo LED Vermelho...")
        set_color(255, 0, 0)
        time.sleep(1)
        print("[Test] ✓ Vermelho OK")
        
        # Verde
        print("[Test] Acendendo LED Verde...")
        set_color(0, 255, 0)
        time.sleep(1)
        print("[Test] ✓ Verde OK")
        
        # Azul
        print("[Test] Acendendo LED Azul...")
        set_color(0, 0, 255)
        time.sleep(1)
        print("[Test] ✓ Azul OK")
        
        # Branco
        print("[Test] Acendendo LED Branco (todas as cores)...")
        set_color(255, 255, 255)
        time.sleep(1)
        print("[Test] ✓ Branco OK")
        
        # Apaga
        set_color(0, 0, 0)
        print("\n[Test] ✓ LED RGB funcionando corretamente!")
        return True
        
    except Exception as e:
        print(f"[Test] ✗ Erro ao testar LED RGB: {e}")
        return False

def test_wifi():
    """Testa Conexão WiFi"""
    print_header("TESTE: WIFI")
    
    import network
    
    WIFI_SSID = "wIFRN-IoT"
    WIFI_PASS = "deviceiotifrn"
    
    print(f"[Test] Conectando em {WIFI_SSID}...")
    
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    
    timeout = 30
    while timeout > 0:
        if wlan.isconnected():
            print(f"[Test] ✓ WiFi conectado!")
            ifconfig = wlan.ifconfig()
            print(f"[Test] IP: {ifconfig[0]}")
            print(f"[Test] Máscara: {ifconfig[1]}")
            print(f"[Test] Gateway: {ifconfig[2]}")
            print(f"[Test] DNS: {ifconfig[3]}")
            
            # Teste de ping (HTTP simples)
            print(f"\n[Test] Testando conectividade com API...")
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex(("laica.ifrn.edu.br", 80))
                sock.close()
                if result == 0:
                    print("[Test] ✓ API acessível (porta 80 OK)")
                    return True
                else:
                    print("[Test] ✗ Não conseguiu conectar à API (porta 80 bloqueada?)")
                    return False
            except Exception as e:
                print(f"[Test] ✗ Erro ao conectar à API: {e}")
                return False
        
        time.sleep(0.5)
        timeout -= 1
        print(".", end="")
    
    print("\n[Test] ✗ WiFi não conectou")
    return False

def test_mac_address():
    """Obtém MAC Address"""
    print_header("TESTE: MAC ADDRESS")
    
    import network
    import ubinascii
    
    try:
        wlan = network.WLAN(network.STA_IF)
        mac = ubinascii.hexlify(wlan.config('mac'), ':').decode()
        print(f"[Test] MAC Address: {mac}")
        print("[Test] ✓ MAC obtido com sucesso!")
        return True
    except Exception as e:
        print(f"[Test] ✗ Erro ao obter MAC: {e}")
        return False

def test_http_post():
    """Testa requisição HTTP POST"""
    print_header("TESTE: HTTP POST")
    
    try:
        import socket
        import json
        
        print("[Test] Preparando requisição HTTP...")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        
        print("[Test] Conectando em laica.ifrn.edu.br:80...")
        sock.connect(("laica.ifrn.edu.br", 80))
        
        data = {"mac": "AA:BB:CC:DD:EE:FF"}
        json_data = json.dumps(data)
        
        request = (
            f"POST /device/heartbeat HTTP/1.1\r\n"
            f"Host: laica.ifrn.edu.br\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(json_data)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{json_data}"
        )
        
        print("[Test] Enviando POST /device/heartbeat...")
        sock.sendall(request.encode())
        
        response = b""
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk
        
        sock.close()
        
        response_str = response.decode('utf-8', errors='ignore')
        status_line = response_str.split('\r\n')[0]
        
        print(f"[Test] Resposta HTTP: {status_line}")
        
        if "200" in status_line or "201" in status_line:
            print("[Test] ✓ HTTP POST funcionando!")
            return True
        else:
            print("[Test] ✗ Resposta inesperada")
            return False
            
    except Exception as e:
        print(f"[Test] ✗ Erro ao testar HTTP: {e}")
        return False

def main():
    """Executa todos os testes"""
    print("\n" + "█"*60)
    print("█" + " "*58 + "█")
    print("█" + "  TESTE DE COMPONENTES - BitDogLab V6".center(58) + "█")
    print("█" + " "*58 + "█")
    print("█"*60)
    
    results = {}
    
    # Teste 1: MAC
    results["MAC Address"] = test_mac_address()
    
    # Teste 2: LED
    results["LED RGB"] = test_led_rgb()
    
    # Teste 3: WiFi
    results["WiFi"] = test_wifi()
    
    # Teste 4: HTTP
    results["HTTP POST"] = test_http_post()
    
    # Teste 5: Botão (por último pois requer interação)
    results["Botão A"] = test_button_a()
    
    # Resumo
    print_header("RESUMO DOS TESTES")
    
    for name, passed in results.items():
        status = "✓ PASSOU" if passed else "✗ FALHOU"
        print(f"{name:.<40} {status}")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    
    print(f"\n{passed}/{total} testes passaram")
    
    if passed == total:
        print("\n🎉 TODOS OS TESTES PASSARAM! Placa pronta para usar Cerberos.")
    else:
        print("\n⚠️  Alguns testes falharam. Verifique os erros acima.")
    
    print("\n" + "█"*60 + "\n")

if __name__ == "__main__":
    main()
