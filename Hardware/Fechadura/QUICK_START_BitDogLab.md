# Guia Rápido de Setup - Cerberos BitDogLab V6

## 1️⃣ Prepare a Placa (Primeira Vez)

### Download e Instalação do Firmware

```
1. Acesse: https://github.com/BitDogLab/BitDogLab/blob/main/Firmware/BitDogLab_W.uf2
2. Clique em "Download raw file" (ícone de download)
3. Pressione BOOTSEL (botão embarcado) enquanto conecta via USB
4. Aparecerá pasta "RPI-RP2" no Windows Explorer
5. Arraste o arquivo BitDogLab_W.uf2 para essa pasta
6. Placa reinicia automaticamente
```

## 2️⃣ Configurar MicroPython (Primeira Vez)

### Via Thonny IDE (Recomendado)

```bash
# 1. Baixe Thonny em https://thonny.org/
# 2. Instale e abra

# 3. Configure interpretador:
   Tools > Options > Interpreter
   Selecione: "MicroPython (Raspberry Pi Pico)"
   Porta: Detectar automaticamente

# 4. Clique em "Install or update firmware"
   Selecione o drive RPI-RP2
   Clique "Install"
   Aguarde mensagem "DONE!"

# 5. Pronto! Fechadura IDE
```

## 3️⃣ Enviar Código para Placa

### Opção A: Thonny IDE (Mais Fácil)

```
1. Abra Thonny IDE
2. Clique em File > Open
3. Selecione Cerberos_BitDogLab.py
4. Adapte credenciais WiFi (linhas 14-15)
5. Clique em Run (Ctrl+Shift+F5)
6. Escolha salvar como "main.py" na placa
7. Pronto! Código rodará toda vez que placa ligar
```

### Opção B: PyCharm + OpenRocket (Avançado)

```bash
pip install OpenRocket

# Copiar arquivo
OpenRocket send Cerberos_BitDogLab.py /pyboard/main.py
```

## 4️⃣ Configurar Acesso-NG API

### Registrar Dispositivo

```bash
# Obtenha o MAC da placa olhando serial output
# Será exibido algo como: MAC Address: AA:BB:CC:DD:EE:FF

# Registre o dispositivo (via admin)
# 1. Acesse: http://sua-api/admin/cerberoses
# 2. Clique em "Novo Cerberos"
# 3. Preencha:
#    - MAC: AA:BB:CC:DD:EE:FF (obtido acima)
#    - Nome: "Porta BitDogLab" (nome amigável)
#    - Ambiente: (selecione ambiente)
#    - Chave: 5 (deve corresponder a DEVICE_ID)

# OU via curl:
curl -X POST http://sua-api/admin/cerberoses \
  -d '{
    "mac": "AA:BB:CC:DD:EE:FF",
    "nome": "Porta BitDogLab",
    "ambiente_id": 1,
    "chave": "5"
  }' \
  -H "Content-Type: application/json"
```

## 5️⃣ Testar

### Serial Monitor

```
1. Thonny IDE > Shell (abaixo do editor)
2. Deverá aparecer algo como:

    [GPIO] Pinos inicializados com sucesso
    [Device] MAC Address: AA:BB:CC:DD:EE:FF
    [Device] ID: 5
    [WiFi] Conectando em wIFRN-IoT...
    [WiFi] Conectado!
    [WiFi] IP: 192.168.1.100
    [Device] Enviando coldstart...
    [HTTP] Status: 200
    [Device] Coldstart bem-sucedido!
    [Main] Sistema pronto para operação
```

### Teste do Botão

```
1. Na placa, localize o Botão A (silkscreen "A")
2. Pressione o Botão A
3. Deverá aparecer no serial:
   [Button] Botão A pressionado!
   [Auth] Autenticando tag: caronte_button...
   [HTTP] Status: 200
   [Auth] Autorização concedida!
   [Lock] Acionando fechadura...

4. O LED deve piscar em azul 3 vezes (sucesso)
```

### Teste do Heartbeat

```
Aguarde 30 segundos. Deverá aparecer:
[Device] Enviando heartbeat...
[HTTP] Status: 200
[Device] Heartbeat bem-sucedido!
```

## 6️⃣ Entender os LEDs

| Situação | LED | O que significa |
|----------|-----|-----------------|
| Conectando WiFi | Nenhum | Aguardando conexão |
| Coldstart enviado | 🟢 Verde | Sistema inicializado OK |
| Autenticação autorizada | 🔵 Azul (pisca 3x) | Acesso concedido - Fechadura acionada |
| Autenticação negada | 🔴 Vermelho | Acesso negado |
| Erro na API | 🔴 Vermelho | Falha na comunicação |

## 7️⃣ Troubleshooting

### Problema: "Não conecta à WiFi"
```
Solução:
1. Verifique SSID e senha (linhas 14-15)
2. Tente com hotspot do telefone
3. Verifique firewall/roteador
4. Reinicie a placa (BOOTSEL + reset)
```

### Problema: "Device desconhecido"
```
Solução:
1. Verifique MAC na serial (deve ser mostrado no boot)
2. Registre o dispositivo na API (seção 4️⃣)
3. Confirme que DEVICE_ID = 5 (linha 17)
```

### Problema: "Botão não responde"
```
Solução:
1. Verifique se GPIO 5 está correto
2. Execute teste no Thonny:
   >>> import machine
   >>> button = machine.Pin(5, machine.Pin.IN, machine.Pin.PULL_UP)
   >>> print(button.value())  # Solto = 1, Pressionado = 0
   
3. Teste desbounce:
   >>> while True:
   ...     print(button.value())
   ...     import time
   ...     time.sleep(0.1)
```

### Problema: "Placa não aparece em COM"
```
Solução:
1. Pressione BOOTSEL enquanto conecta
2. Verifique Gerenciador de Dispositivos > Portas COM
3. Instale drivers: https://zadig.akeo.ie/
4. Tente em outro computador/porta USB
```

## 8️⃣ Adaptar para Seu Uso

### Mudar SSID/Senha WiFi
```python
WIFI_SSID = "sua_rede"
WIFI_PASS = "sua_senha"
```

### Mudar Intervalo Heartbeat
```python
HEARTBEAT_INTERVAL = 30  # segundos
# Mudar para 60 para enviar a cada minuto
```

### Usar Relé Física (Não Apenas LED)
```python
def acionamento_fechadura():
    rele = machine.Pin(14, machine.Pin.OUT)  # GPIO 14
    rele.on()      # Liga relé
    time.sleep(1)  # Aguarda 1 segundo
    rele.off()     # Desliga relé
```

## 9️⃣ Integração com Sistema Completo

```
┌─────────────┐
│  BitDogLab  │ (Cerberos - Esta placa)
│   V6 Pico W │
└──────┬──────┘
       │ WiFi
       │ HTTP POST
       ▼
┌─────────────────────┐
│  Servidor Access-NG │
│   (API Flask)       │
└──────┬──────────────┘
       │ Valida credenciais
       │
       ▼
┌─────────────┐
│   Banco de  │
│   Dados     │
│ (Usuários)  │
└─────────────┘
```

## 🔟 Próximos Passos

1. **RFID**: Adapte para ler cartões RFID em vez de botão
2. **Múltiplos Dispositivos**: Registre vários Cerberos com MACs diferentes
3. **Log**: Estenda para enviar log de tentativas de acesso
4. **Controle Remoto**: Use dashboard web para ativar/desativar

## ✅ Checklist Final

- [ ] Firmware MicroPython instalado
- [ ] Código `Cerberos_BitDogLab.py` carregado como `main.py`
- [ ] WiFi conectando
- [ ] MAC da placa anotado
- [ ] Dispositivo registrado na API
- [ ] Coldstart sendo enviado (verificar GET /api/status)
- [ ] Botão A respondendo
- [ ] LED indicando ações corretamente
- [ ] Heartbeat sendo enviado a cada 30s

---

**Dúvidas?** Consulte `README_Cerberos_BitDogLab.md` para documentação completa.
