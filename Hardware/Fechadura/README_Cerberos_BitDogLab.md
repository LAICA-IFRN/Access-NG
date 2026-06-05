# Cerberos - BitDogLab V6 (MicroPython)

Sistema de controle de acesso com MicroPython para placa **BitDogLab V6** (Raspberry Pi Pico W).

## 📋 Características

- ✅ **Coldstart**: Notifica a API quando o dispositivo inicia
- ✅ **Heartbeat**: Envia presença periódica à API (a cada 30s)
- ✅ **Autenticação**: Botão A funciona como "caronte" para autenticação
- ✅ **Feedback Visual**: LEDs RGB indicam status (verde=sucesso, vermelho=erro, azul=desbloqueio)
- ✅ **Acionamento**: Simula desbloqueio com LED (pode ser estendido para relé)
- ✅ **HTTP Client**: Implementação nativa com socket (sem bibliotecas externas)

## 🔧 Hardware

### BitDogLab V6 - Pinout Utilizado

| Componente | GPIO | Descrição |
|------------|------|-----------|
| Botão A | 5 | Entrada (PULL_UP) |
| LED Vermelho | 13 | PWM - Erro |
| LED Verde | 11 | PWM - Sucesso |
| LED Azul | 12 | PWM - Desbloqueio |

### Conexão

A BitDogLab V6 já vem com esses componentes integrados. Nenhuma conexão externa necessária.

## 📡 Rede

### Configuração WiFi

```python
WIFI_SSID = "wIFRN-IoT"
WIFI_PASS = "deviceiotifrn"
```

Modifique no início do arquivo `Cerberos_BitDogLab.py` conforme sua rede.

### API

```python
API_HOST = "laica.ifrn.edu.br"
API_PORT = 80
```

## 🚀 Instalação e Execução

### Pré-requisitos

1. **BitDogLab V6** com Pico W
2. **MicroPython** instalado na placa
3. **Thonny IDE** ou similar para carregar o código

### Passo 1: Preparar Firmware

A BitDogLab V6 vem com Firmware MicroPython pré-instalado. Se precisar reinstalar:

1. Pressione **BOOTSEL** enquanto conecta a placa (entra em modo de gravação)
2. Baixe firmware em: https://github.com/BitDogLab/BitDogLab/blob/main/Firmware/BitDogLab_W.uf2
3. Arraste o arquivo `.uf2` para a pasta `RPI-RP2` que aparecer no Windows

### Passo 2: Carregar Código

#### Opção A: Thonny IDE

1. Abra Thonny IDE
2. Configure interpretador: `Tools > Options > Interpreter > MicroPython (Raspberry Pi Pico)`
3. Abra `Cerberos_BitDogLab.py`
4. Clique em "Run" ou `Ctrl+Shift+F5`
5. Escolha salvar como `main.py` na placa

#### Opção B: Via Terminal (rshell)

```bash
# Instale rshell
pip install rshell

# Conecte
rshell

# Copie arquivo
cp Cerberos_BitDogLab.py /pyboard/main.py

# Reinicie
repl
>>> import machine; machine.soft_reset()
```

### Passo 3: Configuração do Dispositivo na API

Antes de rodar o código, registre o dispositivo na API:

```bash
# Obtenha o MAC da placa (será exibido no boot)
# Exemplo: AA:BB:CC:DD:EE:FF

# Registre um novo Cerberos no banco de dados
curl -X POST http://laica.ifrn.edu.br/admin/cerberoses \
  -H "Content-Type: application/json" \
  -d '{
    "mac": "AA:BB:CC:DD:EE:FF",
    "nome": "Porta BitDogLab",
    "ambiente_id": 1,
    "chave": "5"
  }'
```

### Passo 4: Teste

1. Placa inicia e se conecta à WiFi
2. Enviam coldstart para `/device/coldstart` - **LED verde pisca**
3. A cada 30s envia heartbeat para `/device/heartbeat`
4. Pressione **Botão A** para autenticar
5. Se autorizado: **LED azul pisca 3 vezes** (desbloqueio simulado)
6. Se não autorizado: **LED vermelho pisca 1 segundo** (erro)

## 📊 Fluxo de Operação

```
┌─────────────────────────────────────────────────────────────────┐
│ BOOT                                                            │
└─────────────────────────────────────────────────────────────────┘
                            ↓
              ┌──────────────────────────┐
              │ Inicializa GPIO + WiFi   │
              └──────────────────────────┘
                            ↓
              ┌──────────────────────────┐
              │ POST /device/coldstart   │  ← Informa inicialização
              │ (LED verde pisca)        │
              └──────────────────────────┘
                            ↓
       ┌────────────────────────────────────────┐
       │ LOOP PRINCIPAL (a cada 100ms)          │
       ├────────────────────────────────────────┤
       │ 1. Verifica tempo de heartbeat         │
       │    - Se passou 30s                     │
       │      POST /device/heartbeat            │
       │                                        │
       │ 2. Monitora Botão A                    │
       │    - Se pressionado:                   │
       │      POST /caronte/autenticarTag       │
       │      Se resposta = "true"              │
       │        - Aciona LED (azul)             │
       │        - Fecha por 500ms               │
       │      Senão                             │
       │        - LED erro (vermelho)           │
       └────────────────────────────────────────┘
```

## 🔐 API Endpoints Utilizados

### Coldstart (Inicialização)

```http
POST /device/coldstart
Content-Type: application/json

{
  "mac": "AA:BB:CC:DD:EE:FF",
  "chave": "5"
}
```

**Resposta:**
```json
{"status":"ok","device":"cerberos","mac":"AA:BB:CC:DD:EE:FF"}
```

### Heartbeat (Presença)

```http
POST /device/heartbeat
Content-Type: application/json

{
  "mac": "AA:BB:CC:DD:EE:FF"
}
```

**Resposta:**
```json
{"received":"AA:BB:CC:DD:EE:FF"}
```

### Autenticação

```http
POST /caronte/autenticarTag
Content-Type: application/json

{
  "mac": "AA:BB:CC:DD:EE:FF",
  "tag": "caronte_button",
  "chave": ""
}
```

**Resposta:**
```json
{"Allow": true}
```

## 🎨 Feedback Visual - LEDs

| LED | Cor | Significado |
|-----|-----|------------|
| Verde | Sucesso | Coldstart/Heartbeat OK |
| Vermelho | Erro | Autenticação negada ou erro na API |
| Azul | Desbloqueio | Autorização concedida - Fechadura acionada |

## 🔧 Customização

### Mudar Intervalo de Heartbeat

```python
HEARTBEAT_INTERVAL = 30  # segundos
```

### Mudar Rede WiFi

```python
WIFI_SSID = "sua_rede"
WIFI_PASS = "sua_senha"
```

### Mudar ID do Dispositivo

```python
DEVICE_ID = "5"
```

### Estender para Usar Relé Real

Substitua `blink_led_unlock()` para controlar um relé:

```python
def acionamento_fechadura():
    """Aciona relé real em GP14"""
    rele = machine.Pin(14, machine.Pin.OUT)
    rele.on()
    time.sleep(0.5)
    rele.off()
```

## 📝 Debug

### Monitorar Serial

Use Thonny IDE ou terminal:

```bash
rshell
repl
```

### Output Esperado no Boot

```
============================================================
CERBEROS - Sistema de Controle de Acesso
BitDogLab V6 (Raspberry Pi Pico W)
============================================================

[GPIO] Pinos inicializados com sucesso
[Device] MAC Address: AA:BB:CC:DD:EE:FF
[Device] ID: 5
[WiFi] Conectando em wIFRN-IoT...
[WiFi] Conectado!
[WiFi] IP: 192.168.1.100
[Device] Enviando coldstart...
[HTTP] Conectando em laica.ifrn.edu.br:80...
[HTTP] POST /device/coldstart
[HTTP] Dados: {"mac": "AA:BB:CC:DD:EE:FF", "chave": "5"}
[HTTP] Status: 200
[HTTP] Resposta: {"status":"ok","device":"cerberos","mac":"AA:BB:CC:DD:EE:FF"}
[Device] Coldstart bem-sucedido!

[Main] Sistema pronto para operação
[Main] Aguardando entrada do botão A...
```

## ⚠️ Troubleshooting

### "WiFi: Falha na conexão"
- Verifique SSID e senha
- Verifique se a placa está na mesma rede
- Teste com hotspot do celular para descartar firewall

### "HTTP: Erro"
- Verifique se `API_HOST` é acessível: `ping laica.ifrn.edu.br`
- Confirme que a porta 80 não está bloqueada
- Aumentar timeout em `API_TIMEOUT = 15`

### "Device desconhecido"
- Registre o MAC na API antes de rodar
- Confirme que o `DEVICE_ID` está correto

### Botão não funciona
- Verifique GPIO 5 está correto
- Teste leitura direta:
```python
button_a = machine.Pin(5, machine.Pin.IN, machine.Pin.PULL_UP)
print(button_a.value())  # Deve ser 1 (solto) e 0 (pressionado)
```

## 📞 Comparação Arduino vs MicroPython

| Aspecto | Arduino (Cerberos.ino) | MicroPython |
|--------|------------------------|------------|
| Microcontrolador | ESP8266 | Pico W |
| Rede | WiFi simples | WiFi + Bluetooth |
| Código | C++ | Python |
| Desenvolvimento | Arduino IDE | Thonny IDE |
| HTTP | Biblioteca HTTPClient | Socket nativo |
| Memória | 4MB | 2MB |
| Velocidade | 80-160 MHz | 133 MHz |

## 📚 Recursos

- **BitDogLab Docs**: https://github.com/BitDogLab/BitDogLab
- **MicroPython Docs**: https://micropython.org/
- **Raspberry Pi Pico W**: https://www.raspberrypi.com/products/raspberry-pi-pico/

## 📄 Licença

Código fornecido como parte do projeto Access-NG.

---

**Última atualização**: 2026-06-05  
**Compatibilidade**: BitDogLab V6 com Raspberry Pi Pico W
