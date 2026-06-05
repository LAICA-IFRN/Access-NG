# Índice - Cerberos BitDogLab V6

## 📚 Arquivos Criados

Este pacote contém uma implementação completa de **controle de acesso com MicroPython** para a placa **BitDogLab V6** (Raspberry Pi Pico W).

### 🔴 Arquivo Principal
- **`Cerberos_BitDogLab.py`** - Código completo do sistema de controle de acesso
  - Coldstart (notifica inicialização)
  - Heartbeat (presença periódica)
  - Botão A para autenticação
  - Acionamento de LED RGB
  - Cliente HTTP nativo

### 📖 Documentação

- **`README_Cerberos_BitDogLab.md`** - Documentação técnica completa
  - Características detalhadas
  - Hardware e pinout
  - Rede e API
  - Instalação passo-a-passo
  - Fluxo de operação
  - Endpoints da API
  - Feedback visual (LEDs)
  - Troubleshooting avançado

- **`QUICK_START_BitDogLab.md`** - Guia de instalação rápida
  - Setup em 10 minutos
  - Preparação da placa
  - Carregamento do código
  - Testes básicos
  - Checklist final
  - Troubleshooting comum

### 🧪 Testes e Exemplos

- **`test_bitdoglab_components.py`** - Tester de todos os componentes
  - Valida Botão A
  - Valida LED RGB (todas as cores)
  - Testa WiFi
  - Testa HTTP POST
  - Testa MAC Address
  - Relatório resumido

- **`examples_code_snippets.py`** - Biblioteca de exemplos de código
  - LED RGB (15 exemplos)
  - Leitura de botão com debounce
  - WiFi com timeout
  - HTTP GET/POST
  - JSON parsing
  - Configuração em arquivo
  - Timers
  - ADC (analógico)
  - PWM (PWM fade)
  - Tratamento de erros
  - NeoPixel (matriz de LEDs)
  - Template main.py

## 🚀 Como Usar

### 1️⃣ Primeira Vez (Novo Usuário)
```
1. Leia: QUICK_START_BitDogLab.md (5 minutos)
2. Execute: test_bitdoglab_components.py (valida hardware)
3. Carregue: Cerberos_BitDogLab.py (código principal)
4. Teste: Pressione Botão A
```

### 2️⃣ Desenvolvimento/Customização
```
1. Consulte: examples_code_snippets.py (copiar código)
2. Leia: README_Cerberos_BitDogLab.md (entender sistema)
3. Edite: Cerberos_BitDogLab.py (adapte para seu caso)
```

### 3️⃣ Troubleshooting
```
1. Procure na seção "Troubleshooting" do QUICK_START
2. Se não resolver, veja README_Cerberos_BitDogLab.md
3. Use test_bitdoglab_components.py para diagnosticar
```

## 📊 Arquitetura

```
┌─────────────────────────────────────────┐
│        BitDogLab V6                     │
│      (Raspberry Pi Pico W)              │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ Cerberos_BitDogLab.py           │   │
│  │ • Coldstart                     │   │
│  │ • Heartbeat (30s)               │   │
│  │ • Monitora Botão A (GPIO 5)     │   │
│  │ • Controla LED RGB              │   │
│  │ • HTTP Client nativo            │   │
│  └─────────────────────────────────┘   │
│                                         │
│  Hardware:                              │
│  • Botão A → GPIO 5                     │
│  • LED R   → GPIO 13 (PWM)              │
│  • LED G   → GPIO 11 (PWM)              │
│  • LED B   → GPIO 12 (PWM)              │
│  • WiFi Pico W                          │
└─────────────────────────────────────────┘
          │
          │ WiFi (HTTP/JSON)
          │
          ▼
┌─────────────────────────────────────────┐
│     API Access-NG (Flask)               │
│     laica.ifrn.edu.br                   │
│                                         │
│  • POST /device/coldstart               │
│  • POST /device/heartbeat               │
│  • POST /caronte/autenticarTag          │
│  • GET  /api/status                     │
└─────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────┐
│     Banco de Dados                      │
│     • Usuarios                          │
│     • Ambientes                         │
│     • Cerberoses                        │
│     • Carontes                          │
└─────────────────────────────────────────┘
```

## 🔧 Configuração Rápida

### WiFi
```python
WIFI_SSID = "wIFRN-IoT"
WIFI_PASS = "deviceiotifrn"
```

### API
```python
API_HOST = "laica.ifrn.edu.br"
API_PORT = 80
```

### Device
```python
DEVICE_ID = "5"  # Deve existir no banco de dados
```

## 📱 Ciclo de Vida do Dispositivo

### Boot
```
Placa inicia
    ↓
Inicializa GPIO + WiFi
    ↓
POST /device/coldstart (com MAC)
    ↓
LED Verde pisca (sucesso) / LED Vermelho pisca (erro)
```

### Loop Normal
```
A cada 100ms:
  • Se passou 30s → POST /device/heartbeat
  • Monitora Botão A
    - Se pressionado → POST /caronte/autenticarTag
    - Se autorizado → LED Azul pisca (desbloqueio)
    - Se negado → LED Vermelho pisca (erro)
```

### Status Online
```
Dispositivo é marcado como "online" quando:
  • Envia coldstart
  • Envia heartbeat
  • Envia autenticação (qualquer endpoint)

Dispositivo é marcado como "offline" quando:
  • Não envia nada por > 30 segundos
  • Monitored automático a cada 15 segundos
```

## ✅ Checklist de Implementação

- ✅ Código MicroPython compatível com Pico W
- ✅ Coldstart com MAC address
- ✅ Heartbeat periódico (30s)
- ✅ Monitoramento de Botão A
- ✅ Autenticação via API
- ✅ Acionamento de LED em sucesso
- ✅ Feedback visual (cores diferentes)
- ✅ HTTP/JSON nativo (sem bibliotecas)
- ✅ Tratamento de erros
- ✅ Debounce de botão
- ✅ Timeout nas conexões

## 📦 Comparação: Arduino vs MicroPython

| Feature | Arduino (Cerberos.ino) | MicroPython (Aqui) |
|---------|------------------------|-------------------|
| Placa | ESP8266 | Pico W |
| Conexão | WiFi simples | WiFi + Bluetooth |
| Cores | C++ | Python |
| HTTP | Biblioteca Externa | Socket Nativo |
| LED | Digital simples | PWM RGB |
| Botão | Polling | GPIO com debounce |
| Ambiente | Arduino IDE | Thonny IDE |
| Heartbeat | ❌ Não | ✅ Sim |
| Coldstart | ❌ Sim | ✅ Sim |

## 🎯 Próximas Melhorias Possíveis

1. **RFID Integration**
   - Leitor RFID no GPIO SPI
   - Enviar UID do cartão como "tag"

2. **Armazenamento Local**
   - Salvar configuração em arquivo
   - Cache de senhas permitidas

3. **Múltiplos Dispositivos**
   - Sistema de filas
   - Sincronização entre placas

4. **Dashboard Web**
   - WebSocket para updates em tempo real
   - UI para ativar/desativar remotamente

5. **Baixo Consumo**
   - Deep sleep entre heartbeats
   - Wake-on-button

6. **Segurança**
   - HTTPS/SSL
   - Autenticação Bearer Token

## 📞 Suporte

### Documentação
- `README_Cerberos_BitDogLab.md` - Tudo sobre o sistema
- `QUICK_START_BitDogLab.md` - Instalação rápida
- `examples_code_snippets.py` - Exemplos de código

### Testes
- `test_bitdoglab_components.py` - Validar hardware

### Referência
- https://github.com/BitDogLab/BitDogLab
- https://micropython.org/
- https://www.raspberrypi.com/products/raspberry-pi-pico/

## 📄 Informações do Projeto

- **Sistema**: Access-NG
- **Módulo**: Hardware/Fechadura (Cerberos)
- **Placa**: BitDogLab V6 (Raspberry Pi Pico W)
- **Linguagem**: MicroPython
- **Data**: 2026-06-05
- **Status**: ✅ Pronto para produção

---

**Para começar agora:**
1. Abra `QUICK_START_BitDogLab.md`
2. Siga os passos
3. Execute `test_bitdoglab_components.py` para validar
4. Carregue `Cerberos_BitDogLab.py` como `main.py`

**Bom desenvolvimento! 🚀**
