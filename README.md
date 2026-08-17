# Access-NG

Sistema de controle de acesso para ambientes físicos usando ESP32/ESP8266,
RFID, fechaduras acionadas por relé, API Flask, painel administrativo com
dashboard de estatísticas e uma versão web/mobile do Caronte com geolocalização.

Cerberoses e Carontes podem se comunicar com o Sistema por **REST** (HTTP/HTTPS,
modo padrão) ou por **MQTT**, configurável por dispositivo no painel admin.

O projeto usa a seguinte nomenclatura:

- **Tartaro**: ambiente físico controlado, modelado como `Ambiente`.
- **Cerberos**: dispositivo/fechadura que consulta a API para saber se deve abrir.
- **Caronte fixo**: leitor RFID físico que autentica tags e solicita abertura.
- **Caronte web**: portal mobile em navegador, com login por matrícula/PIN e validação por geolocalização.

## Estrutura do repositório

```text
Access-NG/
├── Sistema/
│   ├── api.py                         # API principal, admin, Caronte web e endpoints IoT
│   ├── Model.py                       # Modelos SQLAlchemy e migrações SQLite automáticas
│   ├── Tartaro.py                     # Regras de autenticação, filas de abertura e geofence
│   ├── mqtt_service.py                # Serviço MQTT de background (brokers, tópicos, handlers)
│   ├── requirements.txt               # Dependências do Sistema
│   └── templates/
│       ├── admin/                     # Painel administrativo (Visão Geral com dashboard de estatísticas, CRUD de Brokers MQTT etc.)
│       └── caronte/                   # Portal mobile do Caronte web
└── Hardware/
    ├── accessng/                      # Pacote MicroPython compartilhado pelos 4 firmwares MQTT — ver
    │   │                              # [Arquitetura boot.py/main.py/accessng/](#arquitetura-bootpymainpyaccessng-dos-firmwares-mqtt)
    │   ├── config.py                  # config.json / boot_state.json (leitura/escrita atômica)
    │   ├── wifi.py                    # Conexão Wi-Fi (try_connect_once/connect_bounded)
    │   ├── recovery.py                # Detecção de crash-loop + entrada em modo recovery
    │   ├── provisioning.py            # AP + portal HTTP de configuração (modo recovery)
    │   ├── ota.py                     # Download/validação/troca/rollback de firmware + ensure_dependencies()
    │   └── watchdog.py                # Watchdog de hardware (machine.WDT), rede de segurança contra travamentos
    ├── bibliotecas/                   # Bibliotecas MicroPython vendorizadas (servidas via /ota/ como qualquer firmware)
    │   ├── umqtt/simple.py, robust.py # Cliente MQTT (de micropython-lib)
    │   ├── sh1106.py                  # Driver do display OLED SH1106 (FECHO)
    │   └── ssd1306.py                 # Driver do display OLED SSD1306 (BitDogLab)
    ├── Fechadura/
    │   ├── Cerberos_UART.ino          # ESP com Wi-Fi/API/relé e UART para leitor RFID (legado, sem OTA)
    │   ├── Cerberos.ino               # Sketch alternativo/legado (sem OTA)
    │   ├── Cerberos_BitDogLab.py      # Firmware MicroPython (Pico W) — modo REST (sem OTA)
    │   │
    │   ├── boot_esp32.py, device_defaults_esp32.py, main_esp32.py
    │   │                              # Cerberos ESP32 enxuto — esquema boot.py/main.py atual
    │   ├── CerberosESP32.py           # MIGRADOR do Cerberos enxuto (dispositivos antigos em campo) + version_esp32.json
    │   ├── installer_esp32.py         # Bootstrap para um ESP32 com MicroPython zerado
    │   │
    │   ├── boot_esp32c3.py, device_defaults_esp32c3.py, main_esp32c3.py
    │   │                              # "FECHO" (ESP32-C3): LEDs, relé, OLED SH1106, UART com o Caronte
    │   ├── CerberosESP32C3.py         # MIGRADOR do FECHO + version_esp32c3.json
    │   ├── installer_esp32c3.py       # Bootstrap para um ESP32-C3 (FECHO) com MicroPython zerado
    │   │
    │   ├── boot_bitdoglab.py, device_defaults_bitdoglab.py, main_bitdoglab.py
    │   │                              # BitDogLab V6/Pico W: Cerberos + Caronte combinados, OLED SSD1306
    │   ├── Cerberos_BitDogLab_MQTT.py # MIGRADOR da BitDogLab + version.json
    │   └── installer_bitdoglab.py     # Bootstrap para uma BitDogLab com MicroPython zerado
    ├── Autenticador/
    │   ├── Caronte_RFID.ino           # ESP leitor RFID via MFRC522, envia tag por UART ao Cerberos (legado, sem OTA)
    │   ├── boot.py, device_defaults.py, main.py
    │   │                              # Caronte com leitor Wiegand (ESP32-C3) — esquema boot.py/main.py atual
    │   ├── CaronteESP32C3.py          # MIGRADOR do Caronte + version.json
    │   └── installer.py               # Bootstrap para um ESP32-C3 (Caronte) com MicroPython zerado
    ├── Ambiente/
    │   └── TempHumi.ino               # Sensor de temperatura/umidade
    └── ModPotencia/
        └── Servo.ino                  # Módulo de potência/servo
```

Os 4 firmwares MQTT (Caronte, FECHO, Cerberos enxuto, BitDogLab) passaram por um
redesenho de boot/OTA: cada um agora é um trio `boot_*.py`/`device_defaults_*.py`/
`main_*.py` (instalados no dispositivo sem sufixo, como `boot.py`/
`device_defaults.py`/`main.py` — o sufixo no repositório só existe para os três
arquivos de `Fechadura/` conviverem lado a lado, mesmo padrão já usado para
`version_esp32.json` vs `version_esp32c3.json`), com o antigo arquivo único
reaproveitado como **migrador** — ver a seção dedicada abaixo.

## Arquitetura

Fluxo RFID físico:

1. O usuário aproxima uma tag RFID no `Caronte_RFID.ino`.
2. O Caronte envia o UID da tag por UART para o `Cerberos_UART.ino`.
3. O Cerberos chama `POST /caronte/autenticarTag` com `tag`, `mac` e `chave`.
4. O Sistema verifica se o Caronte existe, se a chave confere e se a tag pertence a um usuário autorizado no Tartaro.
5. Se autorizado, o Sistema coloca um acionamento na fila dos Cerberoses do ambiente.
6. O Cerberos consulta a fila via `POST /service/enviroments/enviroments/access/`.
7. Se `Allow` for verdadeiro, o relé é acionado e a porta abre.

Fluxo Caronte web/mobile:

1. O usuário acessa `GET /caronte`.
2. Faz login com `matricula`/`pin`, ou (se configurado) com **Entrar com o SUAP** — ver
   [Login via SUAP (OAuth2)](#login-via-suap-oauth2).
3. O navegador solicita permissão de geolocalização.
4. O portal busca ambientes próximos em `GET /caronte/ambientes-proximos?lat=&lon=` — só
   retorna Tartaros com `web_habilitado=True` e, se logado, onde o usuário tem permissão
   explícita de uso do Caronte web (`usuarios_web`).
5. O usuário toca em **Entrar**.
6. O servidor valida novamente a localização e a permissão (mesmos dois critérios do passo
   4, mais o Tartaro ter algum Cerberos `online`), depois aciona os Cerberoses do ambiente.

Fluxo de status:

1. Cerberos e Carontes informam inicialização em `POST /device/coldstart`.
2. Dispositivos enviam presença em `POST /device/heartbeat` ou usam endpoints legados, que também atualizam `last_seen`.
3. Uma thread de background marca como `offline` dispositivos sem contato há mais de 30 segundos.
4. `GET /api/status` e `GET /api/dashboard` expõem todos os Tartaros com seus dispositivos e estatísticas, para uso por integrações externas.
5. A própria Visão Geral do painel admin (`GET /admin/`) mostra esse status, sem precisar de uma aplicação separada.

Fluxo MQTT (alternativo ao REST, por dispositivo):

1. No painel admin, o Cerberos/Caronte é configurado com `protocolo=mqtt` e associado a um Broker MQTT cadastrado em `/admin/brokers`.
2. O `mqtt_service` conecta a todos os brokers ativos ao iniciar o Sistema (`_mqtt().start()`).
3. Ao ligar, o dispositivo publica `access-ng/coldstart/{mac}` (com `mac` e `chave`) e aguarda a resposta em `access-ng/coldstart/{mac}/result`. O Sistema valida a `chave`, atualiza `status`/`last_seen` e responde com `status:"ok"` + `ambiente_id`, `denied` (chave inválida) ou `unknown` (MAC não cadastrado). O dispositivo só prossegue ao receber `ok`; caso contrário repete a cada 15s.
4. Com o `ambiente_id` recebido, o dispositivo publica `access-ng/heartbeat/{mac}` periodicamente, enviando `mac`, `uptime_s` e `uptime` (formatado `dTHH:MM:SS`, calculado a partir de `time.time()` para não estourar como `time.ticks_ms()` faria após alguns dias), e monta os tópicos `access-ng/{ambiente_id}/...`.
5. Um Caronte MQTT publica a TAG lida em `access-ng/{amb_id}/caronte/{mac}/tag`; o Sistema autentica com `Tartaro.autenticarTAGDetalhado()`, responde em `access-ng/{amb_id}/caronte/{mac}/result` e, se autorizado, publica o comando de abertura para os Cerberoses do ambiente.
6. Um Cerberos MQTT assina `access-ng/{amb_id}/cerberos/{mac}/command`; ao receber `{"command":"unlock"}` aciona o relé.
7. Quando o Cerberos tem entradas físicas configuradas (botão/contato local), ele publica `access-ng/{amb_id}/cerberos/{mac}/entrada` com `{"mac":..., "pin":...}` ao detectar o acionamento; o Sistema grava o evento como `entrada_fisica` no log, mesmo sem MAC cadastrado.
8. Aberturas manuais (`/admin/cerberoses/<id>/abrir`), via Caronte web (`/caronte/solicitar`) e via Caronte fixo REST (`/caronte/autenticarTag`) também publicam o comando MQTT para os Cerberoses vinculados a um broker, além do mecanismo de fila REST existente.
9. O mesmo tópico de comando também aceita `{"command":"reboot"}` (reinício remoto), `{"command":"get_config"}` (o dispositivo reporta sua configuração efetiva) e `{"command":"set_config","params":{...}}` (o dispositivo grava novos valores em `config.json` e reinicia) — ver [Reinício e reconfiguração remota](#reinício-e-reconfiguração-remota).
10. A resposta ao `get_config`/`set_config` chega em `access-ng/{amb_id}/{cerberos|caronte}/{mac}/config/result`; o Sistema grava o payload em `Cerberos.config_atual`/`Caronte.config_atual` com o timestamp em `config_atualizado_em`.
11. O heartbeat MQTT pode incluir campos de diagnóstico (`ip`, `uptime`, `rssi`, `mem_free`, `cpu_temp`, `fs_free`/`fs_total`) e o coldstart pode incluir `boot_count`, `hardware`, `mcu` e `rssi` (o sinal WiFi no momento do boot ajuda a diagnosticar falhas logo na conexão) — usados nas páginas de detalhe do Cerberos/Caronte no painel (ver [Diagnóstico e histórico](#diagnóstico-e-histórico)).

O MAC nos tópicos usa `-` no lugar de `:` (compatibilidade com brokers que tratam `:` como separador). O Sistema aceita ambos os formatos ao consultar o banco.

Fluxo UART Caronte ↔ FECHO (fallback offline, opcional):

1. O Sistema publica `{"command":"set_tags","tags":[...]}` no tópico de comando do
   Caronte sempre que a lista de frequentadores/TAGs de um Tartaro muda (e depois de
   todo coldstart) — o firmware grava essa lista em `tags.json`, na própria placa.
2. Com `UART_ENABLED=true` nos dois firmwares, o Caronte manda um KEEP-ALIVE por UART
   ao FECHO a cada `UART_KEEPALIVE_S`; sem ACK do FECHO por 3 intervalos seguidos, ele é
   considerado offline e o fallback abaixo não é tentado.
3. O fluxo normal de autenticação continua sendo o MQTT de sempre (`.../tag` →
   aguardar `.../result`). Só quando o broker/servidor não responde a tempo
   (`AUTH_TIMEOUT_S`) é que o Caronte consulta a whitelist local (`tags.json`) e, se a
   TAG constar lá (e o FECHO estiver online), decide sozinho e manda o pedido de
   liberação direto ao FECHO via UART — sem depender do broker para essa decisão.
4. O FECHO não valida a TAG contra nada: só tenta acionar o relé (respeitando o
   cooldown de proteção da solenóide) e responde PERMITIDO/NEGADO. Publica a TAG
   liberada assim em `access-ng/{amb_id}/cerberos/{mac}/uart_tag`, para auditoria no
   servidor mesmo quando a decisão foi tomada offline.

Protocolo de quadros (mesmo dos dois firmwares): `7E LEN CMD [dados] CS`, onde `CS`
fecha a soma de `LEN+CMD+dados+CS` em `0 mod 256` (complemento de dois). Comandos:
KEEP-ALIVE (`01`), ACK (`13`), PERMITIDO (`02`), NEGADO (`03`) e ENVIO DE TAG (`04`,
com a TAG em 4 bytes + o tipo Wiegand `0x1A`/26 ou `0x22`/34). Detalhes completos no
cabeçalho de [`CaronteESP32C3.py`](Hardware/Autenticador/CaronteESP32C3.py) e
[`CerberosESP32C3.py`](Hardware/Fechadura/CerberosESP32C3.py).

### Tópicos MQTT (referência)

Prefixo fixo: `access-ng`. Tabela completa dos tópicos publicados/assinados pelo `mqtt_service.py`:

| Tópico | Direção | Payload | Descrição |
| --- | --- | --- | --- |
| `access-ng/coldstart/{mac}` | dispositivo → Sistema | `{"mac":..., "chave":..., "versao"?}` | Boot do dispositivo. |
| `access-ng/coldstart/{mac}/result` | Sistema → dispositivo | `{"status":"ok"\|"denied"\|"unknown", "ambiente_id"?}` | Resposta ao coldstart. |
| `access-ng/heartbeat/{mac}` | dispositivo → Sistema | `{"mac":..., "uptime_s":..., "uptime":..., "versao"?}` | Ping periódico. |
| `access-ng/{amb_id}/caronte/{mac}/tag` | dispositivo → Sistema | `{"tag":..., "chave":...}` | TAG lida por um Caronte MQTT. |
| `access-ng/{amb_id}/caronte/{mac}/result` | Sistema → dispositivo | `{"allow": true\|false, "motivo"?}` | Resultado da autenticação da TAG. |
| `access-ng/{amb_id}/cerberos/{mac}/command` | Sistema → dispositivo | `{"command":"unlock"}`, `{"command":"check_update"}`, `{"command":"reboot"}`, `{"command":"get_config"}` ou `{"command":"set_config","params":{...}}` | Comando de abertura, checagem de OTA, reinício remoto ou leitura/escrita de configuração do Cerberos. |
| `access-ng/{amb_id}/caronte/{mac}/command` | Sistema → dispositivo | `{"command":"check_update"}`, `{"command":"reboot"}`, `{"command":"get_config"}`, `{"command":"set_config","params":{...}}` ou `{"command":"set_tags","tags":[...]}` | Mesmos comandos do Cerberos, exceto `unlock` (só o Cerberos aciona relé), mais `set_tags` (grava a whitelist local de TAGs usada no fallback offline via UART com o FECHO). |
| `access-ng/{amb_id}/cerberos/{mac}/status` | dispositivo → Sistema | `{"status": "..."}` | Atualização de status enviada pelo próprio Cerberos (padrão `online` se omitido). |
| `access-ng/{amb_id}/cerberos/{mac}/entrada` | dispositivo → Sistema | `{"mac":..., "pin":...}` | Entrada física (botão/contato local) detectada pelo Cerberos. |
| `access-ng/{amb_id}/cerberos/{mac}/uart_tag` | dispositivo → Sistema | `{"mac":..., "tag":..., "tipo":..., "allow":...}` | TAG liberada pelo FECHO via fallback offline UART (decisão já tomada pelo Caronte) — só auditoria. |
| `access-ng/{amb_id}/cerberos/{mac}/config/result` | dispositivo → Sistema | `{...}` (config efetiva reportada pelo firmware) | Resposta ao `get_config`/`set_config` do Cerberos; grava em `Cerberos.config_atual`. |
| `access-ng/{amb_id}/caronte/{mac}/config/result` | dispositivo → Sistema | `{...}` (config efetiva reportada pelo firmware) | Resposta ao `get_config`/`set_config` do Caronte; grava em `Caronte.config_atual`. |

O Sistema assina `coldstart/+`, `heartbeat/+`, `+/caronte/+/tag`, `+/cerberos/+/status`,
`+/cerberos/+/entrada`, `+/cerberos/+/uart_tag`, `+/cerberos/+/config/result` e
`+/caronte/+/config/result`; os demais tópicos da tabela são publicados pelo próprio
Sistema para os dispositivos assinarem.

## Requisitos

- Python 3.10+ recomendado.
- SQLite.
- `paho-mqtt` (incluído em `Sistema/requirements.txt`) — necessário para o suporte a MQTT. Sem ele, o `mqtt_service` fica desabilitado e o Sistema funciona normalmente apenas com REST.
- `requests` (incluído em `Sistema/requirements.txt`) — necessário para o login via SUAP (troca de `code` por `access_token` e consulta à API do SUAP). Sem SUAP configurado, essa dependência fica ociosa.
- Para firmware:
  - Arduino IDE ou PlatformIO.
  - Bibliotecas Arduino usadas pelos sketches:
    - `WiFi`
    - `HTTPClient`
    - `ArduinoJson`
    - `SPI`
    - `MFRC522`

## Instalação

Crie um ambiente virtual e instale as dependências do Sistema:

```bash
cd Sistema
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

No Windows, use `.venv\Scripts\activate` no lugar de `source .venv/bin/activate`.

## Execução

Execute o Sistema principal:

```bash
cd Sistema
python api.py
```

Por padrão ele sobe em:

```text
http://0.0.0.0:9001
```

## Banco de dados

O banco é SQLite e é criado automaticamente pelo SQLAlchemy.

Nome do arquivo:

```text
Sistema/Acesso.db
```

O caminho é definido em `Sistema/Model.py` com base no diretório do próprio arquivo.
Assim, o banco do Sistema fica em `Sistema/Acesso.db` mesmo quando o servidor é
iniciado pela raiz do repositório.

### Migrações automáticas

`Sistema/Model.py` executa `meta.create_all(engine)` e depois aplica `ALTER TABLE`
quando colunas novas não existem. Assim, bancos SQLite existentes não precisam ser
recriados para os campos adicionados recentemente.

Colunas adicionadas automaticamente em `cerberoses` e `carontes`:

- `status VARCHAR(20)`
- `last_seen DATETIME`
- `coldstart_at DATETIME`
- `protocolo VARCHAR(10) DEFAULT 'rest'`
- `broker_id INTEGER`
- `versao_firmware VARCHAR(30)`
- `ip VARCHAR(50)`
- `uptime VARCHAR(20)`
- `boot_count INTEGER`
- `hardware VARCHAR(50)`
- `mcu VARCHAR(50)`
- `ssid VARCHAR(50)`
- `rssi INTEGER`
- `mem_free INTEGER`
- `mem_free_min INTEGER`
- `cpu_temp FLOAT`
- `wifi_status INTEGER`
- `wifi_channel INTEGER`
- `wifi_reconnects INTEGER`
- `wifi_last_reconnect_s INTEGER`
- `wifi_last_disconnect_status INTEGER`
- `ap_bssid VARCHAR(20)`
- `config_atual VARCHAR(2000)`
- `config_atualizado_em DATETIME`
- `debug_ativo BOOLEAN DEFAULT 0` — com `true`, todo heartbeat grava o payload completo em `access_logs` (por padrão só os heartbeats "ricos" de diagnóstico gravam — ver [Heartbeat sem sobrecarregar o log](#heartbeat-sem-sobrecarregar-o-log))
- `fs_free INTEGER` — espaço livre (bytes) no filesystem da placa (`os.statvfs('/')`), reportado junto com o restante do diagnóstico
- `fs_total INTEGER` — espaço total (bytes) do filesystem da placa

Colunas adicionadas automaticamente em `ambientes`:

- `latitude FLOAT`
- `longitude FLOAT`
- `raio_metros INTEGER`
- `web_habilitado BOOLEAN DEFAULT 0` — Tartaro precisa disso ligado para aparecer no Caronte web (ver [Caronte web: quem pode usar](#caronte-web-quem-pode-usar))

Coluna adicionada automaticamente em `usuarios`:

- `aprovado BOOLEAN DEFAULT 1` — `1` para quem já existia (cadastro manual já é confiável por definição); só quem se auto-cadastra via SUAP entra com `0`, sem acesso a nenhum Tartaro até aprovação — ver [Login via SUAP (OAuth2)](#login-via-suap-oauth2).

Tabelas novas (criadas via `meta.create_all`, sem precisar de `ALTER TABLE`):

- `usuarios_web` — associação N:N entre `usuarios` e `ambientes` (`usuario_id`, `ambiente_id`), separada de `usuarios_ambientes`: só quem está aqui pode usar o Caronte web para aquele Tartaro, mesmo que tenha acesso físico normal — ver [Caronte web: quem pode usar](#caronte-web-quem-pode-usar).
- `device_heartbeats` — `id`, `mac`, `timestamp`; registro leve (só isso, nenhum outro campo) de cada heartbeat recebido, usado exclusivamente para reconstruir o SLA sem inflar `access_logs` — ver [Heartbeat sem sobrecarregar o log](#heartbeat-sem-sobrecarregar-o-log).
- `suap_config` — linha única (`id=1`) com `client_id`/`client_secret`/`ativo` do login via SUAP — ver [Login via SUAP (OAuth2)](#login-via-suap-oauth2).

## Modelo de dados

### Usuario

Tabela: `usuarios`

- `id`
- `nome`
- `matricula`
- `pin`
- `admin`
- `aprovado` (padrão `true`; `false` só em cadastros auto-criados via SUAP, até um admin aprovar — ver [Login via SUAP (OAuth2)](#login-via-suap-oauth2))
- relacionamento um-para-muitos com `TAG` (`Usuario.tags`, `cascade="all, delete-orphan"`) — um usuário pode ter várias TAGs RFID (ex: cartões emitidos em Tartaros diferentes); qualquer uma delas autentica em qualquer Tartaro onde o usuário já tem permissão de acesso. Não existe conceito de TAG "padrão" — todas são equivalentes.
- relacionamento com `MAC`
- relacionamento muitos-para-muitos com `Ambiente` via `usuarios_ambientes` (frequentadores/acesso físico)
- relacionamento muitos-para-muitos com `Ambiente` via `usuarios_web` (quem pode usar o Caronte web em cada Tartaro — ver [Caronte web: quem pode usar](#caronte-web-quem-pode-usar))
- relacionamento um-para-muitos com `PapelAmbiente` (papéis por Tartaro — gerente/colaborador/leitor)

### TAG

Tabela: `tags`

- `id`
- `numero`
- `usuario_id`

Usada pelo Caronte RFID para autenticação física. `numero` é validado como único
no sistema inteiro **na camada de aplicação** (`api.py`, ao criar/editar um
usuário) — não há `UniqueConstraint` no banco, seguindo a mesma convenção
homemade de migração do resto do projeto. Gerenciar as TAGs de um usuário
(adicionar/remover) é uma ação exclusiva do painel admin
(`/admin/usuarios/novo` e `/admin/usuarios/<id>/editar`); `/caronte/perfil` só
exibe a lista, somente leitura.

Cada TAG tem também um relacionamento muitos-para-muitos com `Ambiente` via a
tabela `tags_ambientes` (`tag_id`, `ambiente_id`), opcional: **sem nenhuma
linha associada, a TAG vale em qualquer Tartaro onde o usuário já é
frequentador** (comportamento padrão/retrocompatível — toda TAG criada antes
dessa mudança continua funcionando exatamente como antes). Assim que a TAG
ganha pelo menos um Ambiente associado, ela passa a valer **só** nos Ambientes
marcados, mesmo que o usuário tenha acesso a outros. Isso é configurado por
Tartaro, na seção "Usuários" de `/admin/ambientes/<id>` — cada TAG do usuário
aparece com um botão "Tirar daqui"/"Liberar aqui" que liga/desliga aquele
Ambiente específico no escopo da TAG (`admin_ambiente_usuario_tag_escopo_toggle`
em `api.py`). O toggle pede confirmação quando a ação é a primeira restrição
de uma TAG até então universal, ou a remoção da última restrição (volta a
valer em qualquer Tartaro) — as duas situações onde o efeito colateral é mais
fácil de não perceber. `Tartaro.autenticarTAGDetalhado()` e
`mqtt_service._tags_do_ambiente()` (whitelist local dos Carontes) respeitam
esse escopo.

### MAC

Tabela: `macs`

- `id`
- `endereco`
- `usuario_id`

### Ambiente/Tartaro

Tabela: `ambientes`

- `id`
- `nome`
- `local`
- `latitude`
- `longitude`
- `raio_metros`
- `web_habilitado` (padrão `false` — Tartaro precisa disso ligado para aparecer no Caronte web, além de cada usuário precisar estar em `usuarios_web`)
- `frequentadores`
- `usuarios_web` (subconjunto de `frequentadores` autorizado a usar o Caronte web neste Tartaro)
- `papeis` (usuários com papel `gerente`/`colaborador`/`leitor` neste Tartaro)
- `cerberoses`
- `carontes`

`latitude`, `longitude` e `raio_metros` são usados pelo Caronte web para validar
proximidade. O raio padrão usado pelo código é 50 metros quando o campo está vazio.
Veja [Caronte web: quem pode usar](#caronte-web-quem-pode-usar) para `web_habilitado`/`usuarios_web`.

### PapelAmbiente

Tabela: `papeis_ambiente`

- `usuario_id` (FK, parte da chave primária composta)
- `ambiente_id` (FK, parte da chave primária composta)
- `papel`: `gerente`, `colaborador` ou `leitor`

Associa um usuário a um papel administrativo num Tartaro específico. A chave
primária composta (`usuario_id` + `ambiente_id`) garante um único papel por
par usuário/Tartaro. Veja a seção [Papéis e permissões](#papéis-e-permissões)
para o que cada papel pode fazer.

### BrokerMQTT

Tabela: `brokers_mqtt`

- `id`
- `nome`
- `host`
- `porta` (padrão `1883`)
- `usuario`
- `senha`
- `tls`
- `ativo`
- relacionamento um-para-muitos com `Cerberos` e `Caronte`

Cadastrado em `/admin/brokers`. Ao salvar/ativar um broker, o `mqtt_service`
conecta (ou reconecta) automaticamente; ao excluir/desativar, desconecta.

### Cerberos

Tabela: `cerberoses`

- `id`
- `nome`
- `mac`
- `chave`
- `ambiente_id`
- `status`
- `last_seen`
- `coldstart_at`
- `protocolo` (`rest` ou `mqtt`, padrão `rest`)
- `broker_id` (FK para `brokers_mqtt`, usado quando `protocolo=mqtt`)
- `versao_firmware`, `ip`, `uptime`, `boot_count`, `hardware`, `mcu`, `ssid` — reportados
  no coldstart/heartbeat, exibidos na página de detalhe do dispositivo
- `rssi`, `mem_free`, `cpu_temp`, `fs_free` — diagnóstico reportado periodicamente
  no heartbeat, com histórico consultável em `/admin/cerberoses/<id>/historico/<metric>`
- `mem_free_min` — menor valor de `mem_free` já visto desde o boot (equivalente ao
  `ESP.getMinFreeHeap()` do Arduino, calculado em software pelo firmware)
- `fs_total` — espaço total (bytes) do filesystem da placa, reportado junto com
  `fs_free` (via `os.statvfs('/')`); usado para saber se cabe a próxima atualização
  OTA e o crescimento do `tags.json` (whitelist local de TAGs RFID)
- `wifi_status`, `wifi_channel` — código bruto de `network.WLAN.status()` e canal
  WiFi atual, só o valor mais recente (sem histórico gráfico)
- `wifi_reconnects`, `wifi_last_reconnect_s`, `wifi_last_disconnect_status` —
  contador de reconexões WiFi desde o boot, segundos desde a última e o código de
  status capturado no momento da queda (motivo aproximado)
- `ap_bssid` — MAC do rádio do Access Point atualmente associado (não o IP do
  gateway, que costuma ser o mesmo em toda uma rede com múltiplos APs sob o
  mesmo SSID); histórico consultável junto com `rssi` para marcar troca de AP
  no gráfico de Sinal WiFi
- `config_atual` (JSON) e `config_atualizado_em` — última configuração efetiva
  reportada pelo firmware via `get_config`/`set_config`
- `debug_ativo` (padrão `false`) — com `true`, todo heartbeat grava o payload
  completo em `access_logs`, não só os de diagnóstico — ver [Heartbeat sem
  sobrecarregar o log](#heartbeat-sem-sobrecarregar-o-log)

Representa a fechadura/dispositivo acionável.

### Caronte

Tabela: `carontes`

- `id`
- `mac`
- `chave`
- `ambiente_id`
- `status`
- `last_seen`
- `coldstart_at`
- `protocolo` (`rest` ou `mqtt`, padrão `rest`)
- `broker_id` (FK para `brokers_mqtt`, usado quando `protocolo=mqtt`)
- `versao_firmware`, `ip`, `uptime`, `boot_count`, `hardware`, `mcu`, `ssid` — reportados
  no coldstart/heartbeat, exibidos na página de detalhe do dispositivo
- `rssi`, `mem_free`, `cpu_temp`, `fs_free` — diagnóstico reportado periodicamente
  no heartbeat, com histórico consultável em `/admin/carontes/<id>/historico/<metric>`
- `mem_free_min` — menor valor de `mem_free` já visto desde o boot (equivalente ao
  `ESP.getMinFreeHeap()` do Arduino, calculado em software pelo firmware)
- `fs_total` — espaço total (bytes) do filesystem da placa, reportado junto com
  `fs_free` (via `os.statvfs('/')`); usado para saber se cabe a próxima atualização
  OTA e o crescimento do `tags.json` (whitelist local de TAGs RFID)
- `wifi_status`, `wifi_channel` — código bruto de `network.WLAN.status()` e canal
  WiFi atual, só o valor mais recente (sem histórico gráfico)
- `wifi_reconnects`, `wifi_last_reconnect_s`, `wifi_last_disconnect_status` —
  contador de reconexões WiFi desde o boot, segundos desde a última e o código de
  status capturado no momento da queda (motivo aproximado)
- `ap_bssid` — MAC do rádio do Access Point atualmente associado (não o IP do
  gateway, que costuma ser o mesmo em toda uma rede com múltiplos APs sob o
  mesmo SSID); histórico consultável junto com `rssi` para marcar troca de AP
  no gráfico de Sinal WiFi
- `config_atual` (JSON) e `config_atualizado_em` — última configuração efetiva
  reportada pelo firmware via `get_config`/`set_config`
- `debug_ativo` (padrão `false`) — com `true`, todo heartbeat grava o payload
  completo em `access_logs`, não só os de diagnóstico — ver [Heartbeat sem
  sobrecarregar o log](#heartbeat-sem-sobrecarregar-o-log)

Representa o leitor/autenticador fixo.

### DeviceHeartbeat

Tabela: `device_heartbeats`

- `id`
- `mac`
- `timestamp`

Só isso — de propósito. Um registro por heartbeat recebido, usado
exclusivamente para reconstruir o SLA sem inflar `access_logs` com uma
linha completa a cada ~25s por dispositivo. Ver [Heartbeat sem
sobrecarregar o log](#heartbeat-sem-sobrecarregar-o-log).

### SuapConfig

Tabela: `suap_config`

- `id` (sempre `1` — linha única)
- `client_id`
- `client_secret`
- `ativo`

Credenciais da aplicação OAuth cadastrada no SUAP e o liga/desliga do botão
"Entrar com o SUAP" no Caronte web. Editada em `/admin/integracao-suap`. Ver
[Login via SUAP (OAuth2)](#login-via-suap-oauth2).

## Papéis e permissões

Além do administrador geral (`Usuario.admin = True`, acesso irrestrito), o
painel suporta papéis **por Tartaro**, atribuídos via `PapelAmbiente`:

| Papel | Pode | Não pode |
| --- | --- | --- |
| **Administrador geral** | Tudo: Tartaros, Brokers MQTT, Cerberoses/Carontes/Usuários/Logs de qualquer Tartaro, conceder qualquer papel ou `admin`. | — |
| **Gerente** | Cadastrar/editar/excluir usuários, Cerberoses e Carontes do seu Tartaro; nomear `colaborador`/`leitor` para gente do mesmo Tartaro; ler os logs do seu Tartaro. | Criar/editar Tartaros ou Brokers MQTT; conceder `admin` geral ou nomear outro `gerente`. |
| **Colaborador** | Cadastrar novos usuários no seu Tartaro. | Editar/excluir usuários existentes, gerenciar Cerberoses/Carontes, ver logs, atribuir papéis. |
| **Leitor** | Visualizar (somente leitura) os logs/eventos do seu Tartaro. | Qualquer ação de escrita no painel. |
| **Usuário regular** (sem papel) | Acessar o portal Caronte (`/caronte`) e atualizar o próprio PIN em `/caronte/perfil` (as TAGs RFID aparecem em modo somente leitura). | Entrar no painel `/admin`; adicionar/remover a própria TAG (só um admin faz isso). |

Os papéis são hierárquicos dentro do mesmo Tartaro: `gerente` já cobre as
capacidades de `colaborador` (cadastrar usuários) e `leitor` (ler logs), além
de gerenciar dispositivos. Cada usuário tem no máximo um papel por Tartaro —
`PapelAmbiente` usa chave primária composta (`usuario_id` + `ambiente_id`).

Qualquer usuário com `admin=True` ou com pelo menos um papel pode entrar em
`/admin/login`; o menu lateral e o conteúdo das telas se ajustam
automaticamente ao que aquele usuário pode ver/fazer. Tartaros, Brokers MQTT
e a exclusão/limpeza de logs continuam exclusivos do administrador geral.

## Login via SUAP (OAuth2)

Além do login por matrícula/PIN, o Caronte web aceita **Entrar com o SUAP**
(authorization code do OAuth2) como caminho adicional — os dois convivem, e a
matrícula devolvida pelo SUAP é a chave de sincronização com `Usuario.matricula`.

### Configuração

1. Cadastre uma aplicação em "Meus Aplicativos" no SUAP com **Authorization
   grant type = Authorization code** e **Client type = Confidential**.
2. A **Redirect URI** precisa ser exatamente a que aparece em
   `/admin/integracao-suap` (comparação exata, sem barra a mais/a menos) —
   normalmente `https://SEU-HOST/caronte/suap/callback` (ou com o prefixo do
   proxy reverso, se houver, ex.: `.../access-ng/caronte/suap/callback`).
3. Copie o `client_id`/`client_secret` gerados para `/admin/integracao-suap`
   (admin geral) e marque **Ativo** — o `client_secret` só é mostrado pelo
   SUAP uma única vez, no momento da criação da aplicação.

### Fluxo

1. `GET /caronte/suap` redireciona para o SUAP com `state` aleatório guardado
   na sessão (proteção CSRF).
2. `GET /caronte/suap/callback` recebe `code`+`state`, valida o `state`, troca
   o `code` por um `access_token` (`POST /o/token/` no SUAP) e busca a
   identificação em `GET /api/rh/eu/` com esse token.
3. A matrícula (`identificacao`) é usada para achar o `Usuario`:
   - **Já existe**: sincroniza o nome (`nome_usual`/`nome`) e loga.
   - **Não existe**: cria um `Usuario` novo com `aprovado=False` — sem
     nenhum Tartaro vinculado, PIN aleatório (nunca comunicado) — e mostra
     uma página de "cadastro pendente", sem logar.
4. Um cadastro com `aprovado=False` não consegue logar (mesmo com matrícula
   batendo) até um admin geral aprovar em `/admin/usuarios` (badge "pendente
   (SUAP)", botão **Aprovar**, filtro `?pendente=1`).

O endpoint de identificação do SUAP mudou pelo menos uma vez no passado
(`/api/eu/` → `/api/rh/eu/`); se voltar a mudar, o caminho certo pode ser
conferido no schema OpenAPI ao vivo do SUAP
(`https://suap.ifrn.edu.br/api/openapi.json`, `operationId
api_endpoints_rh_eu`) — só a constante `SUAP_USERINFO_URL` em `api.py`
precisa mudar.

## Caronte web: quem pode usar

Antes, qualquer Tartaro com latitude/longitude configurado já era utilizável
pelo Caronte web, e qualquer frequentador (mesmo critério do acesso físico
por TAG) podia abrir por ali. Agora são dois portões independentes, e os
dois precisam estar abertos:

1. **Por Tartaro** — `Ambiente.web_habilitado` (checkbox "Permite Caronte
   Web" no formulário de Tartaro). Sem isso, o Tartaro nem aparece em
   `GET /caronte/ambientes-proximos`, não importa a localização de ninguém.
2. **Por usuário, dentro daquele Tartaro** — tabela `usuarios_web`. Mesmo
   sendo frequentador normal (acesso físico/TAG funcionando), o usuário só
   abre pelo Caronte web se também estiver nessa lista. Gerenciado na página
   do próprio Tartaro (`/admin/ambientes/<id>`, seção "Usuários"): botão
   Habilitado/Desabilitado por pessoa, ou já marcando "Caronte Web" na hora
   de vincular/criar o usuário.

`Tartaro.autenticarWeb()` e `Tartaro.ambientesProximos()` checam os dois
critérios (mais estar dentro do raio e o Tartaro ter algum Cerberos
`online`) — a lista de "ambientes próximos" já vem filtrada pela permissão
de quem está logado, então o app nunca mostra um Tartaro como "disponível"
que seria negado na hora de tentar abrir.

## Endpoints do Sistema

Base local padrão:

```text
http://127.0.0.1:9001
```

### Saúde e tela inicial

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/` | Renderiza a tela inicial simples do Sistema. |
| `GET` | `/api/status` | JSON com todos os Tartaros e o status (`online`/`offline`/`unknown`) de seus dispositivos. Sem autenticação — pensado para integrações externas. |
| `GET` | `/api/dashboard` | JSON com contagens de dispositivos, estatísticas de acesso do dia, eventos recentes e detalhamento por Tartaro. Mesma finalidade do `/api/status`, com mais detalhe. |
| `GET` | `/ota/<filepath>` | Serve os `.py` e `version*.json` de firmware para OTA. Sem autenticação (é consultado pelos próprios dispositivos); restrito a uma whitelist fixa de arquivos — ver [OTA](#ota-atualização-remota-de-firmware). |

### Endpoints IoT legados

Os endpoints legados foram mantidos para retrocompatibilidade com firmware já
existente. Todos também atualizam `last_seen` e `status=online` via `_touch_device()`.

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/caronte/autenticarTag` | Autentica tag RFID enviada por um Caronte fixo. |
| `POST` | `/service/enviroments/enviroments/access/` | Cerberos consulta se há abertura pendente para seu MAC. |
| `POST` | `/service/microcontrollers/microcontrollers/esp8266/is-alive/` | Endpoint legado de presença/heartbeat. |

Exemplo de autenticação RFID:

```bash
curl -X POST http://127.0.0.1:9001/caronte/autenticarTag \
  -H 'Content-Type: application/json' \
  -d '{"tag":"A1B2C3D4","mac":"24:6F:28:17:CA:90","chave":"123"}'
```

Resposta:

```json
{"Allow":"True"}
```

Exemplo de consulta de abertura:

```bash
curl -X POST http://127.0.0.1:9001/service/enviroments/enviroments/access/ \
  -H 'Content-Type: application/json' \
  -d '{"mac":"AA:BB:CC:DD:EE:FF"}'
```

Resposta:

```json
{"Allow":false}
```

### Endpoints novos de dispositivos

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/device/coldstart` | Dispositivo ligou. Registra `coldstart_at`, `last_seen` e `status=online`. |
| `POST` | `/device/heartbeat` | Ping periódico. Atualiza `last_seen` e `status=online`. |
| `POST` | `/device/command` | Cerberos consulta comando de abertura com espera curta configurável. |
| `GET` | `/api/status` | Lista Tartaros, Cerberoses e Carontes com status. |

Exemplo de coldstart:

```bash
curl -X POST http://127.0.0.1:9001/device/coldstart \
  -H 'Content-Type: application/json' \
  -d '{"mac":"AA:BB:CC:DD:EE:FF","chave":"123"}'
```

Respostas possíveis:

```json
{"status":"ok","device":"cerberos","mac":"AA:BB:CC:DD:EE:FF","ambiente_id":1}
```

```json
{"status":"denied","mac":"AA:BB:CC:DD:EE:FF"}
```

```json
{"status":"unknown","mac":"AA:BB:CC:DD:EE:FF"}
```

`status:"ok"` retorna o `ambiente_id` cadastrado para o dispositivo — o
firmware usa esse valor para montar os tópicos/rotas do ambiente. `denied`
indica `chave` inválida e `unknown` indica MAC não cadastrado; em ambos os
casos o dispositivo deve repetir o coldstart periodicamente até obter `ok`.

Exemplo de heartbeat:

```bash
curl -X POST http://127.0.0.1:9001/device/heartbeat \
  -H 'Content-Type: application/json' \
  -d '{"mac":"AA:BB:CC:DD:EE:FF"}'
```

Resposta:

```json
{"received":"AA:BB:CC:DD:EE:FF"}
```

Exemplo de comando para Cerberos:

```bash
curl -X POST http://127.0.0.1:9001/device/command \
  -H 'Content-Type: application/json' \
  -d '{"mac":"AA:BB:CC:DD:EE:FF","wait":20}'
```

Respostas:

```json
{"command":"unlock"}
```

```json
{"command":null}
```

Exemplo de status:

```bash
curl http://127.0.0.1:9001/api/status
```

Formato da resposta:

```json
[
  {
    "id": 1,
    "nome": "Laboratorio",
    "local": "Bloco A",
    "cerberoses": [
      {
        "id": 1,
        "nome": "Porta principal",
        "mac": "AA:BB:CC:DD:EE:FF",
        "status": "online",
        "last_seen": "2026-06-03T12:00:00",
        "coldstart_at": "2026-06-03T11:59:30"
      }
    ],
    "carontes": [
      {
        "id": 1,
        "mac": "11:22:33:44:55:66",
        "status": "offline",
        "last_seen": "2026-06-03T11:58:00",
        "coldstart_at": null
      }
    ]
  }
]
```

### Caronte web/mobile

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/caronte` | Tela de login com matrícula e PIN. |
| `POST` | `/caronte/login` | Autentica usuário e cria sessão. |
| `GET` | `/caronte/portal` | Portal mobile com geolocalização. |
| `GET` | `/caronte/ambientes-proximos?lat=&lon=` | Retorna ambientes cujo raio contém as coordenadas. |
| `POST` | `/caronte/solicitar` | Valida geolocalização e permissão, depois aciona Cerberoses. |
| `GET` | `/caronte/meus-logs` | Histórico de acessos do próprio usuário (tentativas, autorizações, login/logout). |
| `GET/POST` | `/caronte/perfil` | Autoatendimento: nome, matrícula e TAG(s) RFID somente leitura; atualiza só o próprio PIN. |
| `GET` | `/caronte/logout` | Encerra sessão. |

Payload de `/caronte/solicitar`:

```json
{
  "ambiente_id": 1,
  "lat": -5.795,
  "lon": -35.21
}
```

Respostas:

```json
{"allow":true}
```

```json
{"allow":false,"motivo":"Sem permissão para este ambiente"}
```

```json
{"allow":false,"motivo":"Fora do raio (120m > 50m)"}
```

### Painel administrativo

O painel fica em:

```text
http://127.0.0.1:9001/admin/login
```

Acesso exige um usuário com `admin=True` **ou** com pelo menos um papel em
`PapelAmbiente` (`gerente`/`colaborador`/`leitor`) — veja
[Papéis e permissões](#papéis-e-permissões). Quem não é administrador geral
só vê/gerencia os Tartaros onde tem papel.

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET/POST` | `/admin/login` | Login administrativo. |
| `GET` | `/admin/logout` | Logout administrativo. |
| `GET` | `/admin/` | Visão Geral: contagens de ambientes/Cerberoses/Carontes/usuários, uma lista "Ambientes (Tartaros)" com status agregado de cada um, card "Status dos Dispositivos" (online/offline/desconhecido, somando Cerberos e Caronte), gráficos de linha de latência média da API (24h) e de aberturas por dia (14 dias), e últimas atividades/tentativas de acesso. |
| `GET` | `/admin/ambientes` | Lista Tartaros. |
| `GET/POST` | `/admin/ambientes/novo` | Cria Tartaro. |
| `GET` | `/admin/ambientes/<id>` | Visão do Tartaro: gráfico de linha de aberturas por dia com período personalizável (`?desde=AAAA-MM-DD&ate=AAAA-MM-DD`, padrão últimos 14 dias), a lista dos equipamentos daquele Tartaro com o SLA (24h) de cada um, e a seção "Usuários" (quem tem acesso, papel, TAG(s) — com toggle "Tirar/Liberar daqui" pra restringir cada TAG a este Tartaro — e, se `web_habilitado`, permissão de Caronte Web — com botão "+ Adicionar usuário"). |
| `GET/POST` | `/admin/ambientes/<id>/editar` | Edita Tartaro, incluindo o checkbox "Permite Caronte Web" (`web_habilitado`). |
| `POST` | `/admin/ambientes/<id>/excluir` | Remove Tartaro. |
| `GET` | `/admin/ambientes/<id>/usuarios/adicionar` | Tela para vincular um usuário existente (busca por nome/matrícula/TAG) ou criar um novo já pré-vinculado a esse Tartaro (sem passar pelo checklist de todos os ambientes). |
| `POST` | `/admin/ambientes/<id>/usuarios/vincular` | Vincula um usuário existente ao Tartaro, com papel opcional e permissão opcional de Caronte Web. |
| `POST` | `/admin/ambientes/<id>/usuarios/<usuario_id>/remover` | Desvincula o usuário do Tartaro (remove papel e permissão de Caronte Web também). |
| `POST` | `/admin/ambientes/<id>/usuarios/<usuario_id>/web` | Liga/desliga a permissão desse usuário usar o Caronte Web nesse Tartaro específico (`usuarios_web`). |
| `POST` | `/admin/ambientes/<id>/usuarios/<usuario_id>/tags/<tag_id>/escopo` | Liga/desliga se uma TAG específica do usuário vale neste Tartaro (ver [Modelo de dados](#modelo-de-dados)). |
| `GET` | `/admin/cerberoses` | Lista Cerberoses. |
| `POST` | `/admin/cerberoses/verificar-atualizacao` | Notifica via MQTT (`check_update`) todos os Cerberoses listados (escopados ao papel do usuário) para verificarem se há firmware novo agora. |
| `GET/POST` | `/admin/cerberoses/novo` | Cria Cerberos. |
| `GET` | `/admin/cerberoses/<id>` | Visão do Cerberos: gauge de SLA (% online) das últimas 24h, versão de firmware reportada, e gráfico de uptime com período personalizável em horas ou dias (`?unidade=hora\|dia&quantidade=N`). |
| `GET/POST` | `/admin/cerberoses/<id>/editar` | Edita Cerberos. |
| `POST` | `/admin/cerberoses/<id>/abrir` | Envia comando manual de abertura para o Cerberos. |
| `POST` | `/admin/cerberoses/<id>/verificar-atualizacao` | Notifica esse Cerberos via MQTT para verificar atualização de firmware agora. |
| `POST` | `/admin/cerberoses/<id>/reiniciar` | Envia comando de reinício remoto (`reboot`) via MQTT. |
| `POST` | `/admin/cerberoses/<id>/debug` | Liga/desliga `debug_ativo` — com ele ativo, todo heartbeat grava o payload completo no log (ver [Heartbeat sem sobrecarregar o log](#heartbeat-sem-sobrecarregar-o-log)). |
| `GET` | `/admin/cerberoses/<id>/config` | Mostra a última configuração efetiva reportada pelo Cerberos (campos sensíveis mascarados). |
| `POST` | `/admin/cerberoses/<id>/config/atualizar` | Publica `get_config` via MQTT, pedindo ao Cerberos que reporte sua configuração atual. |
| `POST` | `/admin/cerberoses/<id>/config` | Publica `set_config` via MQTT com os campos alterados; o dispositivo grava e reinicia. |
| `GET` | `/admin/cerberoses/<id>/historico/<metric>` | JSON com a série histórica (24h) de `rssi`, `mem_free`, `cpu_temp` ou `fs_free`, para os gráficos de diagnóstico; para `rssi` a resposta também inclui `bssids` (o AP associado em cada ponto), usado para marcar troca de Access Point no gráfico. |
| `POST` | `/admin/cerberoses/<id>/excluir` | Remove Cerberos. |
| `GET` | `/admin/carontes` | Lista Carontes fixos. |
| `POST` | `/admin/carontes/verificar-atualizacao` | Notifica via MQTT todos os Carontes listados (escopados ao papel do usuário) para verificarem atualização agora. |
| `GET/POST` | `/admin/carontes/novo` | Cria Caronte fixo. |
| `GET` | `/admin/carontes/<id>` | Visão do Caronte: gauge de SLA (% online) das últimas 24h, versão de firmware reportada, e gráfico de uptime com período personalizável em horas ou dias (`?unidade=hora\|dia&quantidade=N`). |
| `GET/POST` | `/admin/carontes/<id>/editar` | Edita Caronte fixo. |
| `POST` | `/admin/carontes/<id>/verificar-atualizacao` | Notifica esse Caronte via MQTT para verificar atualização de firmware agora. |
| `POST` | `/admin/carontes/<id>/reiniciar` | Envia comando de reinício remoto (`reboot`) via MQTT. |
| `POST` | `/admin/carontes/<id>/debug` | Liga/desliga `debug_ativo`, mesma regra do Cerberos. |
| `POST` | `/admin/carontes/<id>/tags/sincronizar` | Reenvia a whitelist local de TAGs do ambiente (`set_tags`), usada no fallback offline via UART com o FECHO — normalmente já acontece sozinho a cada coldstart/mudança de frequentador, esse botão é só pra forçar na hora. |
| `GET` | `/admin/carontes/<id>/config` | Mostra a última configuração efetiva reportada pelo Caronte (campos sensíveis mascarados). |
| `POST` | `/admin/carontes/<id>/config/atualizar` | Publica `get_config` via MQTT, pedindo ao Caronte que reporte sua configuração atual. |
| `POST` | `/admin/carontes/<id>/config` | Publica `set_config` via MQTT com os campos alterados; o dispositivo grava e reinicia. |
| `GET` | `/admin/carontes/<id>/historico/<metric>` | JSON com a série histórica (24h) de `rssi`, `mem_free`, `cpu_temp` ou `fs_free`, para os gráficos de diagnóstico; para `rssi` a resposta também inclui `bssids` (o AP associado em cada ponto), usado para marcar troca de Access Point no gráfico. |
| `POST` | `/admin/carontes/<id>/excluir` | Remove Caronte fixo. |
| `GET` | `/admin/brokers` | Lista Brokers MQTT. |
| `GET/POST` | `/admin/brokers/novo` | Cria Broker MQTT e conecta o `mqtt_service`. |
| `GET/POST` | `/admin/brokers/<id>/editar` | Edita Broker MQTT e reconecta/desconecta conforme `ativo`. |
| `POST` | `/admin/brokers/<id>/excluir` | Desconecta e remove Broker MQTT. |
| `GET` | `/admin/usuarios` | Lista usuários, paginada (30 por página), com busca (`?search=` — nome/matrícula/TAG) e filtro de pendentes (`?pendente=1`). |
| `GET/POST` | `/admin/usuarios/novo` | Cria usuário e define ambientes permitidos. Com `?ambiente_id=<id>` (link a partir da página do Tartaro), pula o checklist e já pré-vincula só àquele Tartaro. |
| `GET/POST` | `/admin/usuarios/<id>/editar` | Edita usuário e permissões. |
| `POST` | `/admin/usuarios/<id>/aprovar` | Aprova um cadastro pendente (`aprovado=False`, auto-criado via login SUAP) — exige admin geral, já que sem Tartaro ainda não há gerente pra decidir. |
| `POST` | `/admin/usuarios/<id>/excluir` | Remove usuário. |
| `GET/POST` | `/admin/integracao-suap` | Configura o login via SUAP: `client_id`/`client_secret`, liga/desliga, e mostra a Redirect URI exata a cadastrar no SUAP. Admin geral. |
| `GET` | `/caronte/suap` | Inicia o login via SUAP (redireciona pro `/o/authorize/` do SUAP). |
| `GET` | `/caronte/suap/callback` | Callback do OAuth2: troca `code` por token, busca identificação, loga ou cria cadastro pendente — ver [Login via SUAP (OAuth2)](#login-via-suap-oauth2). |
| `GET` | `/admin/logs` | Visualiza logs de acesso à API e tentativas de dispositivos. |
| `POST` | `/admin/logs/excluir` | Exclui logs selecionados. |
| `POST` | `/admin/logs/limpar` | Limpa logs conforme filtros aplicados. |

> A listagem e o CRUD de Tartaros (`/admin/ambientes`, `novo`, `editar`,
> `excluir`) e de Brokers MQTT, além da exclusão/limpeza de logs, exigem
> `admin=True`. A exceção é `/admin/ambientes/<id>` (visão/gráfico do
> Tartaro): aceita também quem tem papel `gerente` ou `leitor` *nesse*
> Tartaro especificamente — é a página que aparece como "Meu Tartaro" no
> menu para esses papéis, já que eles não veem a listagem completa. As
> demais rotas desta tabela aceitam também `gerente`, `colaborador` ou
> `leitor`, mas filtradas/restritas ao Tartaro onde o usuário tem papel —
> ver [Papéis e permissões](#papéis-e-permissões).
>
> `/admin/cerberoses/<id>` e `/admin/carontes/<id>` (a página de SLA de cada
> equipamento) seguem a mesma regra de `/admin/ambientes/<id>`: admin geral
> ou quem tem papel `gerente`/`leitor` no Tartaro daquele dispositivo —
> diferente das rotas de editar/abrir/excluir, que exigem papel `gerente`
> (ou admin) via `pode_gerenciar_dispositivos`. Um `leitor` chega até essa
> página pelo link "Ver" na tabela de equipamentos de "Meu Tartaro", já que
> o menu lateral só mostra "Cerberoses"/"Carontes" para quem tem papel
> `gerente`. O SLA é calculado em cima do histórico de contato do dispositivo —
> qualquer linha não-`device_offline` em `AccessLog` (coldstart, tag, status
> etc.) mais os heartbeats leves em `DeviceHeartbeat` (ver [Heartbeat sem
> sobrecarregar o log](#heartbeat-sem-sobrecarregar-o-log)).
>
> As rotas `verificar-atualizacao` (por dispositivo e em massa) exigem
> `pode_gerenciar_dispositivos` — a mesma regra de `abrir`/`excluir` (admin
> geral ou papel `gerente` no Tartaro daquele dispositivo); ver
> [OTA (atualização remota de firmware)](#ota-atualização-remota-de-firmware).
>
> O dashboard de estatísticas em `/admin/` (online/offline, gráficos de
> linha de latência média e de aberturas por dia, e atividades recentes) é
> restrito ao administrador geral (todos os Tartaros) e a quem tem papel
> `gerente` ou `leitor` (só do(s) Tartaro(s) onde tem o papel). Quem só tem
> papel `colaborador` vê a Visão Geral sem esses widgets e não tem acesso a
> `/admin/ambientes/<id>` nem a `/admin/logs`.
>
> As rotas de usuários dentro de um Tartaro (`/admin/ambientes/<id>/usuarios/*`)
> seguem a mesma régua de `admin_usuarios`/`admin_usuario_editar`: adicionar
> (`adicionar`/`vincular`, e criar novo com `?ambiente_id=`) exige
> `pode_criar_usuarios` (admin, `gerente` ou `colaborador` daquele Tartaro);
> remover e o toggle de Caronte Web exigem `pode_editar_usuarios` (admin ou
> `gerente`). Já `/admin/usuarios/<id>/aprovar` e `/admin/integracao-suap` são
> exclusivos do administrador geral — um cadastro pendente ainda não tem
> Tartaro nenhum para um `gerente` decidir por ele.
>
> `/admin/cerberoses/<id>/debug`, `/admin/carontes/<id>/debug` e
> `/admin/carontes/<id>/tags/sincronizar` exigem `pode_gerenciar_dispositivos`,
> igual `reiniciar`/`abrir`/`excluir`.

> Se não houver um administrador cadastrado, o sistema agora cria um usuário padrão automaticamente na primeira execução:
> - Matrícula: `admin`
> - PIN: `0000`
> Use essas credenciais para entrar em `/admin/login` e depois altere o PIN.

## Log de acessos da API

A API registra todos os acessos em `access_logs`, no banco `Sistema/Acesso.db`. Cada entrada guarda:

- `timestamp` — data e hora do acesso
- `path` — rota acessada
- `method` — método HTTP
- `ip` — origem da requisição
- `mac` — endereço MAC do dispositivo, se presente
- `tag` — tag usada na tentativa, se presente
- `event_type` — tipo do evento, como `api_request`, `login_admin`, `login_caronte` (matrícula/PIN ou SUAP), `comando_abertura`, `mqtt_heartbeat`, `mqtt_status`, `mqtt_command`, `entrada_fisica`, `uart_tag` (TAG liberada via fallback offline pelo FECHO) ou `usuario_aprovado`
- `result` — resultado resumido do evento, como `sucesso` ou `negado`
- `ambiente_id` e `ambiente_nome` — Tartaro relacionado, quando identificado
- `usuario_id` e `usuario_nome` — usuário relacionado, quando identificado
- `status_code` — código HTTP retornado
- `payload` — corpo da requisição
- `message` — resposta ou mensagem retornada pela API
- `duration_ms` — tempo de processamento da requisição em milissegundos, usado para a latência média mostrada na Visão Geral do painel

Isso permite auditar o que acontece na API, incluindo tentativas de dispositivos
cadastrados ou não, logins administrativos, logouts e comandos manuais de abertura.

O formulário de Tartaro usa Leaflet/OpenStreetMap para selecionar latitude e
longitude no mapa e configurar o raio de acesso do Caronte web.

## Status online/offline

Campos usados:

- `status`: `online`, `offline` ou `unknown`.
- `last_seen`: último contato recebido.
- `coldstart_at`: último boot informado pelo dispositivo.

Regras:

- `POST /device/coldstart` marca o dispositivo como `online`.
- `POST /device/heartbeat` marca o dispositivo como `online`.
- Endpoints legados também chamam `_touch_device()` e marcam como `online`.
- Uma thread em background roda a cada 15 segundos.
- Dispositivos `online` sem contato por mais de 30 segundos viram `offline`.
- Dispositivos sem histórico aparecem como `unknown`.

## CI/CD

O projeto possui dois workflows em `.github/workflows/`:

| Arquivo | Finalidade |
| --- | --- |
| `python-app.yml` | Verificação de sintaxe e instalação de dependências (legado). |
| `deploy.yml` | Pipeline principal de CI + CD para o servidor de produção. |

### Pipeline `deploy.yml`

Dispara em:

- `push` para `main` → roda CI e, se aprovado, faz o deploy.
- `pull_request` para `main` → roda apenas o CI.

Etapas:

```text
ci  →  deploy (somente push em main)
```

**Job `ci`**

1. Faz checkout do repositório.
2. Instala `Sistema/requirements.txt`.
3. Executa `python -m compileall Sistema`.

**Job `deploy`**

1. Conecta ao servidor via SSH.
2. Faz `git pull origin main`.
3. Atualiza dependências com `pip install`.
4. Reinicia os processos com `pm2 reload ecosystem.config.js --update-env`.

### Secrets necessários

Configure em **Settings → Secrets and variables → Actions** do repositório:

| Secret | Exemplo | Obrigatório |
| --- | --- | --- |
| `SSH_HOST` | `192.168.1.100` ou `meuservidor.com` | Sim |
| `SSH_USER` | `ubuntu` | Sim |
| `SSH_KEY` | conteúdo de `~/.ssh/id_rsa` | Sim |
| `SSH_PORT` | `22` | Não (padrão: 22) |
| `DEPLOY_PATH` | `/home/ubuntu/Access-NG` | Sim |

### PM2 — `ecosystem.config.js`

O arquivo `ecosystem.config.js` na raiz do repositório define o processo:

| Nome PM2 | Diretório | Porta |
| --- | --- | --- |
| `access-ng-api` | `./Sistema` | 9001 |

Logs ficam em `logs/` na raiz do repositório (criado automaticamente pelo PM2).

**Primeira inicialização no servidor:**

```bash
cd /home/ubuntu/Access-NG
pm2 start ecosystem.config.js
pm2 save
pm2 startup   # gera o comando systemd para iniciar com o servidor
```

Após o `pm2 startup`, execute o comando que ele imprimir com `sudo` para persistir
os processos após reboot.

**Usando virtualenv:**

Se as dependências estiverem num virtualenv, altere o campo `interpreter` em
`ecosystem.config.js`:

```js
interpreter: '/home/ubuntu/Access-NG/venv/bin/python3',
```

**Comandos úteis:**

```bash
pm2 list                              # status dos processos
pm2 logs access-ng-api                # logs em tempo real
pm2 reload ecosystem.config.js        # zero-downtime reload
pm2 restart access-ng-api             # restart forçado
```

## Firmware

### Arquitetura boot.py/main.py/accessng/ dos firmwares MQTT

Os 4 firmwares MQTT (Caronte, FECHO, Cerberos enxuto, BitDogLab) são
divididos em três arquivos no dispositivo, mais um pacote compartilhado:

- **`boot.py`** — supervisor mínimo (~40 linhas), roda antes de tudo.
  Decide se o dispositivo está saudável o bastante para seguir para
  `main.py` ou se precisa entrar em modo de recuperação: `config.json`
  ausente/inválido, Wi-Fi não conecta em 3 tentativas, ou crash-loop
  detectado (`boot_count` sem confirmar saúde por 3 boots seguidos).
  Depende só de `accessng/` e `device_defaults.py` — nunca importa
  `main.py`, exatamente porque a aplicação pode ser o que está travando.
- **`device_defaults.py`** — só dados (`DEFAULTS`/`SENSITIVE_KEYS`), sem
  nenhum import de `machine`/`network`. Existe porque tanto `boot.py`
  quanto o portal de recovery (`accessng/provisioning.py`) precisam desses
  valores sem depender de `main.py`.
- **`main.py`** — a aplicação de sempre (Wiegand/UART/OLED/relé/MQTT/
  heartbeat/OTA, conforme o dispositivo), agora usando `accessng.config`/
  `wifi`/`ota`/`watchdog` em vez de reimplementar cada parte.
- **`accessng/`** — pacote compartilhado pelos 4 firmwares (instalado uma
  vez, não atualizado por OTA automático nesta fase):
  - `config.py` — leitura/escrita atômica (write-tmp-then-rename) de
    `config.json` e `boot_state.json`.
  - `wifi.py` — conexão Wi-Fi; detecta automaticamente (via
    `os.uname().machine`) se o hardware é um ESP32-C3, que tem um bug de
    driver que deixa o rádio preso após uma falha de conexão
    (`OSError("Wifi Internal State Error")` em toda tentativa seguinte) —
    só nesse caso aplica o workaround de resetar o rádio.
  - `recovery.py` — `is_crash_looping()` (generaliza a detecção pra
    qualquer causa de boot ruim, não só update pendente) e `enter()`, que
    sobe o portal de recuperação e nunca retorna.
  - `provisioning.py` — Access Point (`AccessNG-<Tipo>-<sufixo do MAC>`,
    sem senha, `192.168.4.1`) + servidor HTTP mínimo em socket cru
    (sem framework): formulário **genérico**, guiado por
    `DEFAULTS`/`SENSITIVE_KEYS` (tipo do campo por `type(default)` —
    bool→select, int/float→number, str→text ou password se sensível;
    campos do tipo lista, ex. `INPUT_PINS` do Cerberos enxuto, não são
    editáveis por aqui, só direto no `config.json`/`mpremote`, já que o
    formulário não sabe recompor uma lista a partir de um único campo de
    texto). Campos não-sensíveis vêm pré-preenchidos com o `config.json`
    atual do dispositivo (sensíveis nunca — só um aviso "já configurado"
    quando já existe um valor gravado). `POST /save` faz **merge** no
    `config.json` existente em vez de substituí-lo — deixar um campo em
    branco significa "manter o valor atual", nunca "apagar" (mesmo pra
    senha/chave); só um dispositivo sem `config.json` nenhum ainda exige
    `WIFI_SSID` preenchido, já que não há valor anterior pra cair de
    volta. Depois de salvar, zera `boot_state.json` e reinicia. Usa sockets **não-bloqueantes** (`setblocking(False)` +
    polling) tanto no `accept()` quanto na leitura da requisição — não
    `settimeout()`, que na prática não garantiu retorno a tempo de
    alimentar o watchdog nesse hardware e já causou um crash-loop real em
    campo (ver `watchdog.py` abaixo). Também ativa a `STA_IF` em paralelo
    à AP e tenta reconectar à rede configurada a cada 60s em segundo
    plano (`_maybe_recover_wifi()`) — sem isso, um dispositivo que caiu
    em recovery por uma queda de rede só **temporária** (router
    reiniciando, blip do provedor) ficaria preso servindo o AP para
    sempre, mesmo depois da rede voltar, até alguém aparecer fisicamente
    e reenviar o mesmo formulário só pra forçar uma nova tentativa. Se a
    reconexão em segundo plano funcionar, zera `boot_count` e reinicia
    direto pro boot normal, sem esperar ninguém. Se a placa/build não
    suportar AP+STA simultâneo, falha graciosamente (portal continua
    funcionando normalmente, só sem a reconexão automática).
  - `ota.py` — download/validação (`compile()` para arquivos pequenos,
    checagem de tamanho+substring por streaming para o `main.py`,
    grande demais pra `compile()` sem estourar memória)/troca (`main.py`
    → `main.bak`, novo → `main.py`)/rollback, mais
    `ensure_dependencies()` — busca em `Hardware/bibliotecas/` qualquer
    biblioteca listada no `version*.json` daquele firmware (campo
    `bibliotecas`) que ainda não exista localmente, sem exigir passo
    manual de `mip.install()` (que dependeria do dispositivo já ter
    internet — problema de bootstrapping justamente pro cenário que este
    redesenho existe pra resolver). Também tem
    `check_for_package_update()`/`apply_package_update()`/
    `rollback_package_if_pending()` — o mecanismo de atualização
    automática do **pacote `accessng/` em si**, ver subseção própria
    abaixo.
  - `watchdog.py` — `machine.WDT` armado logo no início de `boot.py`
    (timeout de 8000ms — o RP2040/Pico W limita o watchdog de hardware a
    ~8.3s, então o mesmo valor conservador vale pros 4 firmwares), com
    `feed()` chamado nos laços principais e de conexão de `main.py`. Rede
    de segurança contra travamentos de verdade (não só exceções, já
    tratadas nos próprios `try`/`except`) — se `boot.py`/`accessng`
    travar em vez de lançar exceção, o watchdog força um reset e
    `boot_count` continua subindo até o limiar de crash-loop. `arm()`
    aceita um `machine.WDT` já criado por fora (`existing=`) — necessário
    porque `boot.py` agora arma o watchdog **antes** até de importar
    `accessng` (ver subseção abaixo), e a maioria dos ports do
    MicroPython não permite criar um segundo `machine.WDT()`.

`boot_state.json` substitui os quatro marcadores soltos que o esquema
antigo usava (`ota_pending.txt`, `ota_boot_attempts.txt`, `boot_count.txt`,
`soft_reset.flag`) por um único arquivo:

```json
{
  "boot_count": 0,
  "last_boot_ok": false,
  "current_version": "1.3.12",
  "previous_version": null,
  "pending_update": false
}
```

`boot_count` incrementa em **todo** boot (físico ou soft) e só zera quando
`main.py` confirma saúde (`ota.confirm_boot_ok()`, logo após o primeiro
coldstart bem-sucedido) — generaliza a antiga rede de segurança (que só
disparava com update OTA pendente) para qualquer boot ruim repetido.

#### Atualização automática do pacote `accessng/`

Motivação: um dispositivo já migrado que caiu em recovery por um bug em
`accessng/` (ex.: a correção de reconexão em segundo plano descrita
acima) não recebia esse tipo de correção sozinho — o OTA de `main.py`
nunca tocava em `accessng/`, então cada dispositivo já em campo exigia
`mpremote`/reinstalação física pra receber qualquer fix do pacote
compartilhado. Cada `main_*.py`/`main.py` agora também verifica (na
mesma chamada de `ota_check_and_maybe_apply()`, logo depois de checar o
próprio firmware) se há uma versão nova de `Hardware/accessng/
version.json` — um manifesto único, compartilhado pelos 4 firmwares
(`{"versao": "1.0.0", "arquivos": [...]}`, já que `accessng/` em si é
idêntico entre eles):

1. Baixa e valida (`compile()`) **todos** os arquivos do pacote (mais as
   bibliotecas que aquele firmware específico usa) antes de trocar
   qualquer um.
2. Só troca depois que **todos** validarem — cada arquivo trocado ganha
   um backup individual (`<nome>.bak`).
3. Grava `accessng_pending_update=True`/`accessng_version` em
   `boot_state.json` e reinicia.

**Risco reconhecido e mitigado em duas camadas**: diferente de `main.py`,
`boot.py` importa `accessng/config.py`, `wifi.py`, `recovery.py` e
`watchdog.py` **antes** de poder decidir se entra em recovery — um
pacote corrompido poderia, em teoria, deixar o dispositivo preso
falhando todo boot sem sequer conseguir mostrar o AP. Por isso:

- Se o dispositivo confirma saúde normalmente mas depois entra em
  crash-loop (`is_crash_looping()`), `boot.py` chama
  `ota.rollback_package_if_pending()` **antes** do rollback de `main.py`
  — restaura os `.bak` do pacote e reinicia, mesmo raciocínio do
  rollback de firmware de sempre.
- Se o próprio `from accessng import ...` falhar (o pacote não importa
  de jeito nenhum), `boot.py` arma um `machine.WDT` **antes** dessa
  linha (autocontido, sem depender de `accessng.watchdog` — é
  exatamente o que pode estar quebrado) e, se o import falhar, chama
  `_package_self_repair()`: lê `boot_state.json` na mão (sem usar
  `accessng.config`) e restaura os mesmos `.bak`, sem depender de nada
  em `accessng/`. Se não houver update de pacote pendente pra restaurar
  (import falhou por outro motivo), a exceção original propaga como
  sempre — o watchdog armado garante que, na pior hipótese, o
  dispositivo reinicia sozinho em ~8s e tenta de novo, em vez de ficar
  travado para sempre.

Essa é a camada de defesa mais nova do projeto e a que mais pesa em
brick físico se algo sair errado — qualquer mudança em
`accessng/config.py`/`wifi.py`/`recovery.py`/`watchdog.py` merece
atenção redobrada e, idealmente, teste ponta a ponta em hardware real
antes de publicar.

#### Migração de dispositivos já em campo

O arquivo antigo (`CaronteESP32C3.py`, `CerberosESP32C3.py`,
`CerberosESP32.py`, `Cerberos_BitDogLab_MQTT.py`) não foi apagado — ele
virou um **migrador**: continua sendo a aplicação de sempre (mesmo
código, funcionando normalmente), mas ganhou uma rotina de migração
(`_do_migration()`) acionada por `{"command":"migrate"}` no mesmo tópico
MQTT de comando usado por `reboot`/`check_update`. Isso permite levar um
dispositivo já em campo, rodando a versão antiga (sem `boot.py`), até o
esquema novo **100% online**, sem religar fisicamente:

1. O dispositivo antigo se atualiza para o migrador via OTA normal (bump
   de versão no `version*.json`), continua operacional.
2. No painel (`/admin/carontes/<id>` ou `/admin/cerberoses/<id>`), o botão
   **"Migrar (boot.py)"** publica `{"command":"migrate"}`.
3. O migrador baixa `accessng/*.py`, `bibliotecas/umqtt/*.py` (+ driver de
   display específico), `device_defaults.py`, `boot.py` e o `main.py`
   definitivo — **valida cada um com `compile()`** (checagem de sintaxe,
   não só tamanho/substring) antes de tocar em qualquer coisa que já
   funciona. Se qualquer download/validação falhar, aborta sem trocar
   nada — o dispositivo continua rodando o migrador normalmente, pronto
   pra uma nova tentativa.
4. Só depois de TODOS validarem: instala `accessng`/bibliotecas/
   `device_defaults.py` (adições puras), promove `boot.py`, troca
   `main.py` (o migrador vira `main.bak`) pelo definitivo, grava
   `boot_state.json` com `pending_update=True` e reinicia.
5. Se o `main.py` novo confirmar saúde (coldstart ok), a migração está
   concluída. Se não confirmar em 3 boots, `boot.py` restaura o migrador
   via `main.bak` — volta ao estado do passo 1, pronto pra uma nova
   tentativa de migração.

**Como confirmar que deu certo remotamente**: o campo "Firmware" pode não
ajudar (migrador e `main.py` definitivo têm `FIRMWARE_VERSAO`
independentes — a validação do `main.py` baixado não exige nenhum valor
específico). O sinal confiável é o campo **"Hardware"**: o `main.py`
definitivo reporta um sufixo `(boot.py)` (ex.: `"Caronte ESP32-C3
(boot.py)"`) que o migrador não reporta. "Contador de Boots" também deve
incrementar (soft-reset ao trocar de arquivo); se "Hardware" voltar a
aparecer sem `(boot.py)`, foi um rollback.

#### Dispositivo novo (`installer_*.py`)

Para um microcontrolador com MicroPython recém-gravado e nenhum arquivo de
aplicação ainda, cada dispositivo tem um `installer_*.py`
(`Hardware/Autenticador/installer.py`, `Hardware/Fechadura/
installer_esp32c3.py`/`installer_esp32.py`/`installer_bitdoglab.py`) —
autocontido (não depende de `accessng/` nem `device_defaults.py`, porque é
exatamente isso que ainda não existe no dispositivo), com um dicionário
`CONFIG` no topo pra editar com os dados reais (Wi-Fi, `MQTT_BROKER`,
`DEVICE_KEY`) antes de rodar. Baixa todos os arquivos necessários via HTTP
puro pela mesma rota `/ota/<filepath>`, valida cada um (mesmo critério do
migrador) e só depois grava `config.json`/reinicia. `ERASE_ANTES=True`
recupera um dispositivo preso num estado ruim, apagando a aplicação
inteira antes de reinstalar do zero (preserva `config.json` por padrão).
Uso: `mpremote connect <porta> cp installer_X.py :installer.py` seguido de
`mpremote connect <porta> run installer.py`.

**Nunca commitar o `installer_*.py` com credenciais reais preenchidas no
`CONFIG`** — edite localmente só para rodar contra o dispositivo, sem
subir esse arquivo alterado (o Git detecta a mudança porque o arquivo é
versionado com placeholders).

### BitDogLab V6 (Raspberry Pi Pico W) — MicroPython

Dois firmwares prontos em `Hardware/Fechadura/`:

- `Cerberos_BitDogLab.py` — modo REST (HTTP/HTTPS), padrão.
- `Cerberos_BitDogLab_MQTT.py` — modo MQTT exclusivo.

Ambos carregam configuração de um `config.json` no mesmo diretório, com fallback
para valores padrão quando o arquivo não existe.

#### `config.json` do modo MQTT

```json
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
    "RELAY_ACTIVE_MS"    : 2000
}
```

`DEVICE_KEY` deve corresponder ao campo `chave` cadastrado para o Cerberos/Caronte
no banco, e o dispositivo precisa estar com `protocolo=mqtt` e um `broker_id`
apontando para um broker cadastrado em `/admin/brokers`. `HEARTBEAT_INTERVAL` deve
ser menor que o limite de 30s usado pelo monitor de offline do Sistema.

O `AMBIENTE_ID` não é configurado no dispositivo: ao ligar, o firmware publica
um coldstart em `access-ng/coldstart/{mac}` e aguarda a resposta em
`access-ng/coldstart/{mac}/result`. O servidor responde com `status:"ok"` e o
`ambiente_id` cadastrado, que o dispositivo usa para montar os tópicos
`access-ng/{ambiente_id}/...`. Se a resposta for `denied` (chave inválida),
`unknown` (MAC não cadastrado) ou não chegar, o dispositivo não inicia a
operação normal — ele tenta novamente a cada 15 segundos até obter `ok`.

Depois do coldstart aceito, o heartbeat MQTT é publicado em
`access-ng/heartbeat/{mac}` com o tempo que o microcontrolador está ligado:

```json
{
    "mac": "AA:BB:CC:DD:EE:FF",
    "uptime_s": 123,
    "uptime": "0T00:02:03"
}
```

O Sistema grava esse payload nos logs do evento `mqtt_heartbeat`, útil para
debug de reinicializações e quedas de energia.

O firmware MQTT requer a biblioteca `umqtt` instalada na placa via `mip`. Qual
variante priorizar muda por dispositivo — cada firmware tenta importar a sua
preferida primeiro e cai para a outra só se a preferida não estiver instalada:

```python
import mip
mip.install("umqtt.robust")   # Cerberos_BitDogLab_MQTT.py
mip.install("umqtt.simple")   # CerberosESP32.py e CaronteESP32C3.py
```

Não há problema em ter as duas instaladas ao mesmo tempo — o `try`/`except`
na importação escolhe a certa automaticamente.

- **`Cerberos_BitDogLab_MQTT.py`** prefere `umqtt.robust`: essa foi a
  combinação (junto com `qos=1` no publish do coldstart) validada em campo
  como estável para essa placa depois de testes extensivos.
- **`CerberosESP32.py`/`CaronteESP32C3.py`** preferem `umqtt.simple`: a
  `umqtt.robust` sobrescreve `publish()`/`check_msg()` para capturar
  `OSError` sozinha e ficar tentando reconectar em loop silencioso (sem log,
  já que `DEBUG=False` por padrão), o que pode travar o loop principal por
  tempo indeterminado em qualquer soluço de rede sem deixar rastro na serial
  — e o `reconnect()` dela usa `connect(False)`, que não reinscreve em
  nenhum tópico, deixando o dispositivo surdo a comandos até um reboot
  completo. Com `umqtt.simple`, o `OSError` propaga normalmente para o
  `except OSError` do `main()`, que já faz a recuperação correta (reconecta,
  repete o coldstart e reinscreve nos tópicos).

`Cerberos_BitDogLab_MQTT.py` agora é o **migrador** para o esquema
`boot.py`/`main.py`/`accessng/` — a aplicação de verdade vive em
`boot_bitdoglab.py`/`device_defaults_bitdoglab.py`/`main_bitdoglab.py`
(mesmo `config.json`/pinagem/tópicos MQTT documentados acima). Ver
[Arquitetura boot.py/main.py/accessng/](#arquitetura-bootpymainpyaccessng-dos-firmwares-mqtt).

### ESP32 (MicroPython) — Cerberos enxuto

`Hardware/Fechadura/CerberosESP32.py` é um firmware MQTT-only para um Cerberos
dedicado apenas a abrir a fechadura — sem lógica de Caronte/RFID embutida.
Mesmo esquema de `config.json` com fallback a valores padrão dos demais
firmwares MicroPython, com campos próprios:

```json
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

    "LED_LINK_PIN"       : 12,
    "LED_STATUS_PIN"     : 13,
    "RELAY_PIN"          : 15,
    "RELAY_ACTIVE_MS"    : 2000,
    "INPUT_ENABLED"      : true,
    "INPUT_PINS"         : [26, 34],
    "INPUT_DEBOUNCE_MS"  : 200,
    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}
```

- `INPUT_PINS` são entradas lógicas (ativo baixo, ex.: botão local) que abrem o
  relé diretamente no firmware e publicam `access-ng/{ambiente_id}/cerberos/{mac}/entrada`
  com `{"mac":..., "pin":...}`; o Sistema grava isso como evento `entrada_fisica`
  no log, mesmo que o MAC não esteja cadastrado.
- `INPUT_ENABLED` (padrão `true`) liga/desliga a entrada física por inteiro —
  com `false` nenhum pino é inicializado nem gera IRQ, útil quando o botão/pino
  está com ruído ou acionamento espúrio e ainda não há como corrigir o hardware.
- Cada pino tem seu próprio debounce (`INPUT_DEBOUNCE_MS`) controlado por
  timestamp pré-alocado por pino, para evitar alocação de memória dentro da
  interrupção (IRQ).
- No ESP32, `GPIO34` é somente entrada e não possui pull-up interno — use
  resistor pull-up externo quando o sinal for ativo baixo.
- Segue o mesmo fluxo de coldstart/heartbeat/comando MQTT dos demais firmwares
  e também requer `umqtt` instalado via `mip`.
- Recebe OTA como o `Cerberos_BitDogLab_MQTT.py`, mas com arquivo de versão
  próprio (`Hardware/Fechadura/version_esp32.json`) para ter ciclo de release
  independente — veja [OTA (atualização remota de firmware)](#ota-atualização-remota-de-firmware).

`CerberosESP32.py` agora é o **migrador** para o esquema `boot.py`/
`main.py`/`accessng/` — a aplicação de verdade vive em `boot_esp32.py`/
`device_defaults_esp32.py`/`main_esp32.py`. Ver [Arquitetura
boot.py/main.py/accessng/](#arquitetura-bootpymainpyaccessng-dos-firmwares-mqtt).

### ESP32-C3 (MicroPython) — Caronte com leitor Wiegand

`Hardware/Autenticador/CaronteESP32C3.py` é o firmware do Caronte fixo para um
ESP32-C3 com leitor RFID Wiegand (D0/D1), substituindo o leitor MFRC522/UART
de `Caronte_RFID.ino` para essa placa. Não possui Cerberos embutido — lê a TAG,
publica via MQTT e, opcionalmente, fala com o FECHO por UART como fallback.

```json
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

    "WG_D0_PIN"          : 5,
    "WG_D1_PIN"          : 7,
    "BUZZER_PIN"         : 6,
    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "WG_TIMEOUT_MS"      : 25,
    "AUTH_TIMEOUT_S"     : 5,

    "UART_ENABLED"       : false,
    "UART_ID"            : 1,
    "UART_TX_PIN"        : 21,
    "UART_RX_PIN"        : 20,
    "UART_BAUDRATE"      : 9600,
    "UART_KEEPALIVE_S"   : 5,

    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}
```

- Os pulsos Wiegand são acumulados em um buffer pré-alocado dentro da ISR (sem
  GC); a leitura é considerada completa após `WG_TIMEOUT_MS` de silêncio nos
  pinos D0/D1.
- A TAG é decodificada para uma string hexadecimal maiúscula (`_decode_wiegand`),
  com tratamento dedicado para os formatos Wiegand de 26 e 34 bits (remoção dos
  bits de paridade) e fallback genérico para outros tamanhos. Cadastre a `TAG.numero`
  do usuário exatamente nesse formato hexadecimal, já que a comparação em
  `Tartaro.autenticarTAGDetalhado()` é sensível a maiúsculas/minúsculas. Um
  usuário pode ter mais de uma TAG cadastrada (ex: cartões emitidos em
  Tartaros diferentes) — qualquer uma delas autentica normalmente.
- Publica a TAG em `access-ng/{ambiente_id}/caronte/{mac}/tag` com `{"tag":...,"chave":...}`
  e aguarda o resultado em `access-ng/{ambiente_id}/caronte/{mac}/result` por até
  `AUTH_TIMEOUT_S` segundos, sinalizando o resultado com bipes/LEDs
  (`feedback_allow`/`feedback_deny`).
- **Whitelist local + fallback UART** (opcional, `UART_ENABLED`): mantém uma
  cópia local das TAGs autorizadas do ambiente em `tags.json`, atualizada via
  comando MQTT `set_tags` (empurrado pelo Sistema a cada mudança de
  frequentador/TAG e a cada coldstart). O fluxo normal continua sendo MQTT; só
  quando ele não responde a tempo (`AUTH_TIMEOUT_S`) é que decide pela
  whitelist local e manda a liberação direto ao FECHO via UART — ver [Fluxo
  UART Caronte ↔ FECHO](#arquitetura).
- **Display OLED** (opcional, `OLED_ENABLED`, driver `sh1106.py`): mesmo
  princípio do FECHO abaixo — se não detectar o display no boot, segue
  normalmente sem ele.
- **Heartbeat visual** (LEDs VD2/VD3): pulso curto alternando entre os dois a
  cada ~2s enquanto operacional (WiFi+MQTT conectados, aguardando leitura de
  TAG) — indicação visual não-bloqueante de "sistema online". Pausa sozinho
  durante reconexão de WiFi/MQTT.
- Segue o mesmo fluxo de coldstart/heartbeat MQTT dos demais firmwares e também
  requer `umqtt` instalado via `mip`.

`CaronteESP32C3.py` agora é o **migrador** para o esquema `boot.py`/
`main.py`/`accessng/` — a aplicação de verdade vive em `boot.py`/
`device_defaults.py`/`main.py` (sem sufixo, único firmware em
`Autenticador/`). Ver [Arquitetura
boot.py/main.py/accessng/](#arquitetura-bootpymainpyaccessng-dos-firmwares-mqtt).

### ESP32-C3 (MicroPython) — FECHO (Cerberos com UART/OLED)

`Hardware/Fechadura/CerberosESP32C3.py` é outro firmware para a fechadura,
apelidado de **FECHO** pela equipe de hardware — mesma placa ESP32-C3 do
Caronte acima, só que cumprindo o papel de Cerberos: LEDs de feedback, relé
da tranca e display OLED, com um link UART opcional para o Caronte. Continua
aceitando comando remoto de abertura via MQTT normalmente.

```json
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

    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "RELAY_PIN"          : 6,
    "RELAY_ACTIVE_MS"    : 2000,
    "RELAY_COOLDOWN_MS"  : 3000,

    "UART_ENABLED"       : false,
    "UART_ID"            : 1,
    "UART_TX_PIN"        : 21,
    "UART_RX_PIN"        : 20,
    "UART_BAUDRATE"      : 9600,

    "OLED_ENABLED"       : true,
    "OLED_SCL_PIN"       : 7,
    "OLED_SDA_PIN"       : 8,
    "OLED_WIDTH"         : 128,
    "OLED_HEIGHT"        : 64,
    "OLED_ADDR"          : 60,

    "OTA_ENABLED"        : true,
    "OTA_CHECK_INTERVAL" : 3600
}
```

- `RELAY_ACTIVE_MS` é sempre limitado a 2000ms no código (independente do que
  vier em `config.json`), e `RELAY_COOLDOWN_MS` impõe um intervalo mínimo
  entre acionamentos — proteção da solenóide contra picos de comando
  repetidos via MQTT ou UART, pedido explícito da equipe de hardware.
- **UART com o Caronte** (`UART_ENABLED`): responde KEEP-ALIVE com ACK, e ao
  receber uma TAG (já autorizada pelo Caronte — o FECHO não valida nada)
  tenta abrir e responde PERMITIDO/NEGADO. Publica a TAG liberada em
  `access-ng/{amb_id}/cerberos/{mac}/uart_tag` para auditoria — ver [Fluxo
  UART Caronte ↔ FECHO](#arquitetura).
- **Display OLED** (`OLED_ENABLED`, driver `sh1106.py`, mesmo diretório):
  tenta inicializar no boot via `SoftI2C` com timeout (evita travar o
  firmware se o barramento I2C emperrar); se não detectar o display
  fisicamente, loga e segue operando normalmente sem ele. Com o display
  presente, mostra mensagens nos eventos principais (boot, WiFi, MQTT,
  coldstart, abertura/negação da tranca, TAG recebida via UART, OTA).
- Segue o mesmo fluxo de coldstart/heartbeat MQTT/OTA dos demais firmwares,
  com arquivo de versão próprio (`Hardware/Fechadura/version_esp32c3.json`)
  para ciclo de release independente dos outros dois firmwares do mesmo
  diretório.

`CerberosESP32C3.py` agora é o **migrador** para o esquema `boot.py`/
`main.py`/`accessng/` — a aplicação de verdade vive em `boot_esp32c3.py`/
`device_defaults_esp32c3.py`/`main_esp32c3.py`. Ver [Arquitetura
boot.py/main.py/accessng/](#arquitetura-bootpymainpyaccessng-dos-firmwares-mqtt).

## OTA (atualização remota de firmware)

Os quatro firmwares MQTT em campo atualizam a si mesmos sem precisar
reconectar via USB/Thonny. O firmware continua vivendo só no GitHub — não
há upload pelo painel nem tabela no banco guardando o código.

O mecanismo abaixo (whitelist, `version*.json`, comparação numérica,
rollback em 3 boots) é o mesmo tanto para os quatro arquivos migradores
(`Cerberos_BitDogLab_MQTT.py`, `CerberosESP32.py`, `CerberosESP32C3.py`/
FECHO, `CaronteESP32C3.py`) quanto para os `main_*.py`/`main.py`
definitivos do esquema novo — a diferença é só **quem** faz a checagem:
nos migradores é código local (`check_for_update()`/`apply_update()`
inline, mantidos por compatibilidade com dispositivos ainda não
migrados); nos `main_*.py` é `accessng.ota.check_for_update()`/
`apply_update()`. Ver [Arquitetura
boot.py/main.py/accessng/](#arquitetura-bootpymainpyaccessng-dos-firmwares-mqtt)
para o mecanismo de migração em si (`{"command":"migrate"}`) e para
`ensure_dependencies()` (busca automática de bibliotecas vendorizadas).

### Como funciona

1. Cada dispositivo tem uma constante `FIRMWARE_VERSAO` no topo do arquivo.
2. Existe um arquivo de versão no repositório, ao lado do firmware:
   `Hardware/Fechadura/version.json` (`Cerberos_BitDogLab_MQTT.py`),
   `Hardware/Fechadura/version_esp32.json` (`CerberosESP32.py`),
   `Hardware/Fechadura/version_esp32c3.json` (`CerberosESP32C3.py`/FECHO) e
   `Hardware/Autenticador/version.json` (`CaronteESP32C3.py`) — um arquivo por
   firmware, para que cada um tenha ciclo de release independente mesmo os
   três primeiros compartilhando o diretório `Fechadura/`. Formato:
   `{"versao": "1.3.11", "ref": "main", "bibliotecas": ["umqtt/simple.py", ...]}`
   (o campo `ref` não é mais usado pelo firmware — ver observação abaixo;
   `bibliotecas` lista, com caminhos relativos a `Hardware/bibliotecas/`,
   as bibliotecas vendorizadas que aquele firmware específico precisa —
   usado só por `accessng.ota.ensure_dependencies()` no esquema novo, não
   pelos migradores). **Importante**: a `versao` aqui precisa bater com o
   `FIRMWARE_VERSAO` tanto do migrador quanto do `main_*.py` definitivo —
   os dois comparam contra o mesmo `version*.json`, então se um dos dois
   ficar desalinhado ele se vê como "desatualizado em relação a si mesmo"
   e tenta se auto-substituir num loop (o número de versão de cada um é
   independente do outro só no sentido de que a *validação* do arquivo
   baixado não exige um valor específico — não que possam divergir do
   `version*.json` compartilhado).
3. Os arquivos de firmware e de versão são servidos pelo **próprio Sistema**,
   em `GET /ota/<filepath>` ([api.py](Sistema/api.py)), restrito a uma
   whitelist fixa (`_OTA_ALLOWED_FILES`) que nunca lê arquivo fora dessa
   lista — **todo firmware/version novo precisa ser adicionado a essa lista
   manualmente**, ou a OTA dele fica quebrada (404) mesmo com o arquivo
   existindo no repositório. O dispositivo busca `version.json`/
   `version_esp32.json`/`version_esp32c3.json` em
   `http://{OTA_HOST}:{OTA_PORT}/access-ng/ota/{OTA_VERSION_PATH}` — HTTP
   puro nos quatro firmwares (não HTTPS): no ESP32/ESP32-C3 o handshake
   TLS/RSA estourava a memória disponível (`MBEDTLS_ERR_RSA_PUBLIC_FAILED`),
   e os arquivos de OTA são públicos, sem segredo em trânsito, então HTTP
   puro é aceitável — mesma lógica de expor o broker MQTT em texto puro na
   porta 1883. A comparação de versão é numérica (tupla `(major, minor,
   patch)`), não por string ou igualdade — evita tanto reinstalar uma versão
   igual/mais antiga quanto o bug clássico de comparação textual (ex.:
   `"1.3.10" < "1.3.7"` letra a letra). Se a `versao` remota for maior que a
   local, o dispositivo baixa o `.py` do mesmo host, valida o conteúdo, grava
   como `main.new`, troca com `main.py` (guardando o anterior em `main.bak`)
   e reinicia. `OTA_HOST`/`OTA_PORT` apontam para o domínio onde o Sistema
   está publicado (ex.: `laica.ifrn.edu.br`, porta 80), **não** mais para
   `raw.githubusercontent.com` — a rede da instituição não entrega esse
   domínio de forma confiável para arquivos maiores.
4. A checagem ocorre em três momentos: (a) logo após o coldstart, (b)
   periodicamente a cada `OTA_CHECK_INTERVAL` segundos (padrão 3600), e (c)
   imediatamente ao receber `{"command":"check_update"}` no tópico MQTT de
   comando do dispositivo — publicado pelo painel ao clicar em "Verificar
   atualização" (por dispositivo ou em massa) nas páginas
   `/admin/cerberoses` e `/admin/carontes`.

### Publicando uma nova versão

Como o firmware é servido pelo próprio Sistema (a partir do checkout local do
repositório, atualizado a cada `git pull` do [pipeline de deploy](#cicd)), não
é mais necessário criar tags/refs específicos por firmware:

1. Edite o firmware e suba a constante `FIRMWARE_VERSAO`.
2. Atualize o `versao` no `version.json` correspondente para o mesmo valor.
3. Faça push para `main` — o `deploy.yml` roda o CI e, se aprovado, faz
   `git pull` no servidor, deixando o novo `.py`/`version.json` disponíveis em
   `/ota/...` imediatamente.
4. (Opcional) clique em "Verificar atualização" no painel para notificar os
   dispositivos na hora; senão, eles encontram a atualização no próximo
   polling periódico.

### Rede de segurança contra "brick"

Se a versão nova não conseguir completar um coldstart com sucesso em até 3
boots, o dispositivo restaura automaticamente `main.bak` (a versão anterior,
conhecida como boa) e reinicia — sem isso, um firmware com bug exigiria
reconectar a placa fisicamente, exatamente o que a OTA existe para evitar.

Nos dispositivos já migrados para o esquema `boot.py`/`main.py`, essa
proteção é generalizada por `boot.py`/`accessng/recovery.py`: não dispara
só pra update OTA pendente, mas pra **qualquer** boot ruim repetido
(config corrompida, bug não relacionado a OTA, etc.) — ver [Arquitetura
boot.py/main.py/accessng/](#arquitetura-bootpymainpyaccessng-dos-firmwares-mqtt).

### Visibilidade da versão instalada

Coldstart e heartbeat (REST e MQTT) podem incluir um campo opcional
`versao`, gravado em `Cerberos.versao_firmware`/`Caronte.versao_firmware`.
A versão reportada por último aparece nas páginas
`/admin/cerberoses/<id>` e `/admin/carontes/<id>`.

### Diagnóstico e histórico

Além da `versao`, o coldstart MQTT pode reportar `boot_count`, `hardware`
(ex.: identifica se é a variante BitDogLab), `mcu` e `rssi` — o sinal WiFi
já no boot ajuda a diagnosticar dispositivos que falham por sinal fraco
antes mesmo do primeiro heartbeat. O heartbeat MQTT, por sua vez, pode
reportar `ip`, `uptime`, `rssi` (sinal WiFi em dBm), `mem_free` (memória
livre em bytes), `cpu_temp` (°C) e `fs_free`/`fs_total` (espaço livre/total
do filesystem em bytes, via `os.statvfs('/')`) — só uma fração dos
heartbeats carrega esses campos de diagnóstico, para não sobrecarregar o
payload. `fs_free`/`fs_total` servem tanto para saber se cabe a próxima
atualização OTA quanto o crescimento do `tags.json` (whitelist local de
TAGs RFID do Caronte). Esses valores sempre atualizam `Cerberos`/`Caronte`
direto; a persistência em `AccessLog` (base dos gráficos) é mais seletiva —
ver [Heartbeat sem sobrecarregar o log](#heartbeat-sem-sobrecarregar-o-log)
logo abaixo.

A página `/admin/cerberoses/<id>` (e a equivalente de Caronte) mostra esses
valores mais recentes; clicar em "Sinal WiFi", "Memória Livre", "Temperatura
CPU" ou "Espaço em Disco" abre um gráfico com a série das últimas 24h,
obtida via `GET /admin/cerberoses/<id>/historico/<metric>` (`metric` é
`rssi`, `mem_free`, `cpu_temp` ou `fs_free`).

No gráfico de RSSI, a linha é colorida por faixa de qualidade do sinal
(verde/laranja/vermelho) e uma legenda de referência é exibida ao lado:

| RSSI (dBm) | Qualidade | Situação |
| --- | --- | --- |
| -30 a -50 | Excelente | Muito próximo do AP |
| -50 a -60 | Muito bom | Ideal para IoT |
| -60 a -67 | Bom | Funciona perfeitamente |
| -67 a -70 | Aceitável | Pode haver alguma perda |
| -70 a -80 | Fraco | Quedas ocasionais |
| < -80 | Muito ruim | Conexão instável |

Faixas de cor usadas no gráfico: **verde** de -50 a -30 dBm, **laranja** de
-70 a -50 dBm e **vermelho** abaixo de -70 dBm. Um RSSI nessa faixa verde/laranja
não descarta problema de conectividade — se o dispositivo ainda cair com sinal
bom, a causa provável está em outro lugar (roteador, firmware, alimentação),
não na intensidade do rádio.

### Heartbeat sem sobrecarregar o log

Heartbeat MQTT chega a cada `HEARTBEAT_INTERVAL` (tipicamente 25s) **por
dispositivo** — gravar cada um como uma linha cheia em `access_logs` (usada
também para busca/auditoria geral em `/admin/logs`) inundava essa tabela
rapidinho e deixava o painel lento. A solução separa dois interesses que
antes estavam misturados na mesma tabela:

- **`device_heartbeats`** (tabela nova, só `mac`+`timestamp`) recebe **todo**
  heartbeat, sempre — é o que `_intervalos_online()` usa pra reconstruir o
  SLA (junto com qualquer outro evento não-`device_offline` do mesmo `mac`
  em `AccessLog`, como coldstart/tag/status). O SLA continua com a mesma
  precisão de antes.
- **`access_logs`** só ganha uma linha `mqtt_heartbeat` completa (com o
  payload inteiro) quando o heartbeat é "rico" em diagnóstico (carrega
  `rssi`/`mem_free`/`cpu_temp`/etc. — uma fração dos heartbeats, ver acima)
  **ou** quando o dispositivo está com `debug_ativo=True`.

`debug_ativo` é um botão por dispositivo ("Ativar debug"/"Debug ativo") nas
páginas `/admin/cerberoses/<id>` e `/admin/carontes/<id>` — liga o log
completo de cada heartbeat pra investigar um dispositivo específico, sem
precisar ligar isso pra todo mundo. `POST /admin/cerberoses/<id>/debug` e
`/admin/carontes/<id>/debug` fazem o toggle.

#### Diagnóstico WiFi estendido

Inspirado no conjunto clássico de diagnóstico WiFi do Arduino/ESP-IDF
(`WiFi.RSSI()`, `WiFi.status()`, `WiFi.channel()`, `ESP.getFreeHeap()`,
`ESP.getMinFreeHeap()`, contagem/motivo de reconexões), o heartbeat MQTT
também reporta, adaptado às APIs disponíveis no MicroPython
(`network.WLAN`):

| Campo | Equivalente Arduino/ESP-IDF | Origem no firmware |
| --- | --- | --- |
| `mem_free_min` | `ESP.getMinFreeHeap()` | menor `gc.mem_free()` já visto desde o boot (calculado em software; MicroPython não expõe um "heap mínimo" nativo) |
| `wifi_status` | `WiFi.status()` | `network.WLAN(network.STA_IF).status()` — código bruto, **não traduzido**: os valores de `STAT_*` variam por port/versão do MicroPython (ESP32 vs. RP2/cyw43 do BitDogLab), então mapear para texto de forma confiável exigiria testar em cada placa |
| `wifi_channel` | `WiFi.channel()` | `network.WLAN(network.STA_IF).config('channel')` |
| `wifi_reconnects` | contagem de reconexões | contador incrementado toda vez que `connect_wifi()` detecta que a conexão caiu (não conta a conexão inicial do boot); zera a cada reinício |
| `wifi_last_reconnect_s` | tempo entre reconexões | segundos desde a última (re)conexão, calculado a partir de `time.time()` (não `time.ticks_ms()`, que estoura/zera sozinho depois de alguns dias de uptime contínuo) |
| `wifi_last_disconnect_status` | motivo da desconexão | o mesmo código de `wifi_status` capturado no instante em que a queda foi percebida, antes de tentar reconectar |
| `bssid` | `WiFi.BSSIDstr()` | ver abaixo — `None` se nenhuma das formas funcionar nesse hardware |

Os seis primeiros campos são só "valor mais recente" no painel (sem gráfico
de histórico, ao contrário de `rssi`/`mem_free`/`cpu_temp`) — aparecem no
card de Diagnóstico de `/admin/cerberoses/<id>` e `/admin/carontes/<id>`
(persistidos em `Cerberos`/`Caronte`, campo `ap_bssid` para o BSSID).

`_read_ap_bssid()` tenta três formas, em ordem, e usa a primeira que
funcionar nesse hardware/build:

1. `network.WLAN(network.STA_IF).config('bssid')`
2. `network.WLAN(network.STA_IF).status('bssid')`
3. `wlan.scan()` casando o SSID atual (`WIFI_SSID`) na lista de redes
   encontradas, formatando o BSSID retornado com `ubinascii.hexlify()`

Nos ESP32 usados aqui, confirmado em campo que nem `config('bssid')` nem
`status('bssid')` são suportados (`"unknown config param"`/`"unknown status
param"`) — só a opção 3 funciona. Ela só é tentada como último recurso
porque escanear tira o rádio do canal associado por um instante e pode
interromper brevemente a conexão ativa; as opções 1 e 2 não têm esse risco
e evitam o scan sempre que suportadas pela placa.

O `bssid` identifica qual Access Point físico o dispositivo está associado —
diferente do IP do gateway (que costuma ser o mesmo em toda uma rede com
múltiplos APs sob o mesmo SSID, então não serve para detectar roaming). Ele
é lido junto com o `rssi` no mesmo heartbeat de diagnóstico, então o
histórico de ambos vem sempre da mesma linha de `AccessLog` — sem risco de
desalinhar duas séries buscadas separadamente. No gráfico "Sinal WiFi" do
painel, os pontos onde o BSSID mudou em relação ao ponto anterior aparecem
com marcador maior/triangular e borda dourada, com o BSSID (e se houve
troca) no tooltip — útil para saber se o dispositivo está trocando muito de
AP ou se o AP que ele usava caiu.

### Reinício e reconfiguração remota

Os quatro firmwares MQTT também aceitam, no mesmo tópico de comando usado
para abertura/OTA:

- `{"command":"reboot"}` — reinicia o dispositivo imediatamente. Acionado
  pelo botão "Reiniciar" em `/admin/cerberoses/<id>` /
  `/admin/carontes/<id>` (rota `POST .../reiniciar`).
- `{"command":"get_config"}` — pede ao dispositivo que publique sua
  configuração efetiva (a que está realmente em uso, lida do
  `config.json` gravado na placa) no tópico `.../config/result`. Acionado
  pela página `/admin/cerberoses/<id>/config` (e equivalente de Caronte).
- `{"command":"set_config","params":{...}}` — grava novos valores no
  `config.json` do dispositivo, que reinicia para aplicar. Campos em
  branco no formulário não são enviados, evitando apagar sem querer senha
  de WiFi/MQTT ou a `chave` do dispositivo; campos sensíveis
  (`WIFI_PASS`, `DEVICE_KEY`, `MQTT_PASS`) nunca aparecem no log de
  auditoria.

O conjunto de campos editáveis muda conforme o firmware (BitDogLab,
`CerberosESP32.py`, `CerberosESP32C3.py`/FECHO ou `CaronteESP32C3.py`),
detectado pelo `hardware` reportado no coldstart.

Os migradores aceitam ainda `{"command":"migrate"}` — leva o dispositivo
ao esquema `boot.py`/`main.py`/`accessng/` sem religar fisicamente.
Acionado pelo botão "Migrar (boot.py)" nas páginas de detalhe do
dispositivo. Ver [Migração de dispositivos já em
campo](#migração-de-dispositivos-já-em-campo).

### Fora de escopo desta versão

A variante REST do Cerberos (`Cerberos_BitDogLab.py`) e os firmwares legados
Arduino (`Cerberos_UART.ino`, `Cerberos.ino`, `Caronte_RFID.ino`) não recebem
OTA — o mecanismo cobre só os quatro firmwares MQTT atualmente em campo.

### Configuração de IP

No sketch `Hardware/Fechadura/Cerberos_UART.ino`, ajuste:

```cpp
#define SERVER_IP "192.168.0.100:9001"
```

Use o IP e porta onde o `Sistema/api.py` está rodando.

### Atualização necessária do coldstart

O backend novo espera:

```text
POST /device/coldstart
```

O sketch atual ainda usa o endpoint legado:

```cpp
http.begin(client, "http://" SERVER_IP "/access-control/gateway/devices/microcontrollers/cold-start");
String body = "{\"id\": \"5\"}";
```

Atualize a função `coldStart()` do `Cerberos_UART.ino` para enviar o MAC real:

```cpp
void coldStart(){
  WiFiClient client;
  HTTPClient http;

  http.begin(client, "http://" SERVER_IP "/device/coldstart");
  http.addHeader("Content-Type", "application/json");

  String body = "{\"mac\": \"" + WiFi.macAddress() + "\", \"chave\": \"123\"}";
  int httpCode = http.POST(body);
  Serial.println(body);

  if (httpCode > 0) {
    Serial.printf("[HTTP] POST... code: %d\n", httpCode);
    if (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_CREATED) {
      const String& payload = http.getString();
      Serial.println(payload);
    }
  } else {
    Serial.printf("[HTTP] POST... failed, error: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
}
```

### Heartbeat periódico

Para que o status não fique `offline`, Carontes e Cerberoses devem chamar
`/device/heartbeat` periodicamente, por exemplo a cada 10 segundos.

Exemplo:

```cpp
void heartbeat(){
  if (WiFi.status() != WL_CONNECTED) return;

  WiFiClient client;
  HTTPClient http;

  http.begin(client, "http://" SERVER_IP "/device/heartbeat");
  http.addHeader("Content-Type", "application/json");

  String body = "{\"mac\": \"" + WiFi.macAddress() + "\"}";
  int httpCode = http.POST(body);
  Serial.printf("[HEARTBEAT] code: %d\n", httpCode);

  http.end();
}
```

Exemplo de uso no `loop()`:

```cpp
unsigned long lastHeartbeat = 0;

void loop() {
  if (millis() - lastHeartbeat > 10000) {
    lastHeartbeat = millis();
    heartbeat();
  }

  // restante da lógica do dispositivo...
}
```

### MAC hardcoded no sketch atual

O `Cerberos_UART.ino` atual envia um MAC fixo em `/caronte/autenticarTag`:

```cpp
String body = "{\"tag\":\""+ tag.substring(0, 8) + "\", \"mac\": \"24:6F:28:17:CA:90\", \"chave\": \"123\"}";
```

Para produção, prefira `WiFi.macAddress()` ou garanta que o MAC cadastrado no banco
seja exatamente o mesmo do firmware.

Exemplo:

```cpp
String body = "{\"tag\":\"" + tag.substring(0, 8) +
              "\", \"mac\": \"" + WiFi.macAddress() +
              "\", \"chave\": \"123\"}";
```

## Observações de segurança

- A chave Flask padrão é `tartaro-dev-key-change-in-prod`.
- Em produção, defina `SECRET_KEY` no ambiente antes de iniciar o Sistema.
- PINs são armazenados em texto puro no modelo atual.
- `chave` de Cerberos/Caronte também é armazenada em texto puro.
- O Caronte web valida geolocalização no cliente e novamente no servidor, mas GPS de navegador pode ser impreciso ou falsificado.
- Use HTTPS em produção; navegadores modernos normalmente exigem contexto seguro para geolocalização fora de `localhost`.
- Restrinja acesso ao painel `/admin`.

Exemplo:

```bash
export SECRET_KEY='troque-esta-chave'
python Sistema/api.py
```

## Dicas de operação

- Na primeira execução, se não houver administrador, o Sistema cria automaticamente
  `matricula=admin` e `pin=0000`. Entre em `/admin/login` e altere esses dados.
- Cadastre Tartaros com latitude, longitude e raio para habilitar o Caronte web.
- Cadastre Cerberoses e Carontes com os mesmos MACs enviados pelo firmware.
- Associe usuários aos Tartaros permitidos.
- Para RFID, associe uma ou mais `TAG.numero` ao usuário pelo painel admin
  (`/admin/usuarios/novo` ou `/admin/usuarios/<id>/editar`) — qualquer uma
  delas autentica onde o usuário já tem acesso. `/caronte/perfil` só exibe as
  TAGs do próprio usuário, sem poder editá-las.
- Mantenha heartbeats em intervalo menor que 30 segundos. O recomendado é cerca de 10 segundos.
- Para delegar a gestão de um Tartaro sem dar acesso de administrador geral,
  cadastre um usuário com papel `gerente` nesse Tartaro pelo painel — ele
  poderá cadastrar Cerberoses, Carontes, usuários e nomear `colaborador`/`leitor`
  só dentro do próprio Tartaro (veja [Papéis e permissões](#papéis-e-permissões)).

## Problemas comuns

### Visão Geral sem estatísticas

Se a Visão Geral (`/admin/`) não mostra os cards de online/offline e os
gráficos de latência/aberturas, confirme que o usuário logado é
administrador geral ou tem papel `gerente`/`leitor` em algum Tartaro — quem
só tem papel `colaborador` não vê esses widgets (veja [Painel
administrativo](#painel-administrativo)). Os gráficos aparecem vazios até
que existam requisições e aberturas registradas no período correspondente
(24h para latência, 14 dias por padrão para aberturas — ajustável em
`/admin/ambientes/<id>`).

### Dispositivo aparece como unknown

O dispositivo existe no banco, mas ainda não enviou `coldstart`, `heartbeat` ou
chamou um endpoint legado que atualize `last_seen`.

### Dispositivo fica offline rapidamente

O monitor marca offline após mais de 30 segundos sem contato. Implemente heartbeat
periódico no firmware.

### Coldstart retorna unknown

O MAC enviado não está cadastrado em `cerberoses` nem em `carontes`.

### Caronte web não mostra ambientes próximos

Verifique:

- se o navegador recebeu permissão de localização;
- se o Tartaro possui latitude e longitude;
- se o raio em metros cobre a localização atual;
- se o usuário está autenticado.

### Caronte web mostra ambiente, mas nega acesso

O usuário logado provavelmente não está associado ao Tartaro em `usuarios_ambientes`.

### Firmware MQTT não conecta (`ECONNABORTED`)

`[MQTT] Falha na conexão: [Errno 103] ECONNABORTED` indica que o TCP foi recusado
antes do handshake MQTT — geralmente não é erro de configuração. Verifique:

- `MQTT_PORT`/`MQTT_TLS` no `config.json` batem com o broker (porta 1883 sem TLS
  ou 8883 com TLS).
- O host resolve para o IP esperado (`[Diag]` no boot do firmware mostra o IP
  resolvido e testa um socket TCP cru antes do `umqtt`).
- A rede Wi-Fi do dispositivo tem rota/firewall liberado até o broker — redes
  segmentadas (ex: VLAN de IoT separada da VLAN do broker) costumam derrubar a
  conexão mesmo com DNS funcionando.

## Estado atual importante

- O backend do Sistema já possui endpoints novos de coldstart, heartbeat e status.
- O painel admin e o Caronte web estão presentes em `Sistema/templates/`.
- O firmware ainda precisa ser ajustado para usar `/device/coldstart` com MAC real.
- Carontes fixos precisam de heartbeat periódico para status online confiável.
- Pipeline CI/CD configurado em `.github/workflows/deploy.yml`; configure os 5 secrets no repositório para ativar o deploy automático.
- `ecosystem.config.js` na raiz define o processo PM2 `access-ng-api`.
- Suporte a MQTT adicionado: `mqtt_service.py`, CRUD de Brokers em `/admin/brokers`,
  campos `protocolo`/`broker_id` em Cerberos e Caronte, e firmware
  `Cerberos_BitDogLab_MQTT.py`. Requer `paho-mqtt` no Sistema e `umqtt` na placa.
- Novos firmwares ESP32/ESP32-C3 MQTT-only: `Hardware/Fechadura/CerberosESP32.py`
  (Cerberos enxuto com entrada física/botão, evento `entrada_fisica` no log) e
  `Hardware/Autenticador/CaronteESP32C3.py` (Caronte com leitor Wiegand,
  publica a TAG como string hexadecimal).
- Sistema de papéis por Tartaro (`gerente`/`colaborador`/`leitor`) via a
  tabela `PapelAmbiente`, com painel admin compartilhado (`painel_required`)
  e autoatendimento do usuário regular em `/caronte/perfil` (TAG e PIN) —
  veja [Papéis e permissões](#papéis-e-permissões).
- O Dashboard separado (porta 3002) foi removido. A Visão Geral do painel
  (`/admin/`) passou a mostrar o dashboard de estatísticas (dispositivos
  online/offline, gráficos de linha de latência média da API e de
  aberturas por dia, e atividades recentes), restrito a admin geral,
  `gerente` ou `leitor`.
- Cada Tartaro tem sua própria página (`/admin/ambientes/<id>`) com um
  gráfico de linha de aberturas por dia e período personalizável
  (`desde`/`ate`). Admin geral acessa qualquer Tartaro pela listagem; quem
  tem papel `gerente`/`leitor` acessa o próprio pelo link "Meu Tartaro" no
  menu.
- Cada Cerberos/Caronte tem sua própria página de SLA
  (`/admin/cerberoses/<id>` / `/admin/carontes/<id>`) com um gauge da % de
  tempo online nas últimas 24h e um gráfico de uptime com período
  personalizável em horas ou dias. O SLA é derivado do histórico de
  contato em `AccessLog` (sem tabela nova), usando o mesmo limiar de
  `OFFLINE_THRESHOLD` do monitor de offline. A página do Tartaro lista
  todos os seus equipamentos com o SLA (24h) de cada um e um link "Ver".
- OTA para os firmwares MQTT (`Cerberos_BitDogLab_MQTT.py`, `CerberosESP32.py`,
  `CaronteESP32C3.py`): cada um busca seu próprio arquivo de versão
  (`version.json`, `version_esp32.json` e `version.json` do Autenticador,
  respectivamente), baixa e troca `main.py` quando há versão nova, com
  rollback automático via `main.bak` se a versão nova falhar repetidamente no
  boot. Os arquivos agora são servidos pelo próprio Sistema em `GET
  /ota/<filepath>` (whitelist fixa), e não mais por
  `raw.githubusercontent.com` — mudança feita porque a rede da IFRN não
  entregava esse domínio de forma confiável para arquivos maiores. O painel
  pode notificar a verificação na hora via MQTT
  (`/admin/cerberoses/<id>/verificar-atualizacao`,
  `/admin/carontes/<id>/verificar-atualizacao`, e as variantes em massa) —
  veja [OTA (atualização remota de firmware)](#ota-atualização-remota-de-firmware).
- Reinício remoto (`POST /admin/cerberoses/<id>/reiniciar` e equivalente em
  Carontes) e reconfiguração remota (`/admin/cerberoses/<id>/config` e
  `/config/atualizar`, e equivalentes em Carontes) via MQTT
  (`reboot`/`get_config`/`set_config`), com a configuração efetiva reportada
  guardada em `Cerberos.config_atual`/`Caronte.config_atual` — veja
  [Reinício e reconfiguração remota](#reinício-e-reconfiguração-remota).
- Diagnóstico de dispositivo (`ip`, `uptime`, `boot_count`, `hardware`, `mcu`,
  `ssid`, `rssi`, `mem_free`, `cpu_temp`, `fs_free`/`fs_total`) reportado via
  coldstart/heartbeat MQTT, com gráficos históricos de 24h em
  `/admin/cerberoses/<id>/historico/<metric>` e equivalente em Carontes —
  veja [Diagnóstico e histórico](#diagnóstico-e-histórico).
- Diagnóstico WiFi estendido (`mem_free_min`, `wifi_status`, `wifi_channel`,
  `wifi_reconnects`, `wifi_last_reconnect_s`, `wifi_last_disconnect_status`,
  `bssid`), adaptado do conjunto clássico `WiFi.RSSI()/status()/channel()` +
  `ESP.getFreeHeap()/getMinFreeHeap()` do Arduino para as APIs do
  MicroPython (`network.WLAN`) — veja [Diagnóstico WiFi
  estendido](#diagnóstico-wifi-estendido).
- Rastreio de Access Point (`ap_bssid` em `Cerberos`/`Caronte`): identifica
  qual AP físico o dispositivo está associado (diferente do IP do gateway,
  que não muda entre APs numa rede com múltiplos pontos sob o mesmo SSID).
  O gráfico "Sinal WiFi" do painel sobrepõe marcadores nos pontos onde o AP
  mudou, para diagnosticar roaming excessivo ou queda do AP em uso — veja
  [Diagnóstico WiFi estendido](#diagnóstico-wifi-estendido).
- Novo firmware `Hardware/Fechadura/CerberosESP32C3.py` — "FECHO", mesma
  placa ESP32-C3 do Caronte, papel de Cerberos (LEDs, relé, display OLED
  SH1106 opcional via `sh1106.py`), com ciclo de release próprio
  (`version_esp32c3.json`) — veja a seção [Firmware](#firmware).
- Caronte (`CaronteESP32C3.py`) ganhou whitelist local de TAGs (`tags.json`,
  atualizada via comando MQTT `set_tags`) e link UART opcional com o FECHO:
  fallback offline quando o MQTT não responde a tempo, decidindo pela
  whitelist local e mandando a liberação direto ao FECHO — veja [Fluxo UART
  Caronte ↔ FECHO](#arquitetura). Ganhou também display OLED opcional e um
  heartbeat visual (LEDs VD2/VD3 piscando em chase enquanto operacional).
- Volume de heartbeat na `AccessLog` reduzido: heartbeat MQTT agora só grava
  linha completa no log quando é "rico" em diagnóstico ou quando o
  dispositivo está com `debug_ativo=True` (novo toggle por dispositivo); todo
  heartbeat continua alimentando o SLA através da nova tabela leve
  `DeviceHeartbeat` — veja [Heartbeat sem sobrecarregar o
  log](#heartbeat-sem-sobrecarregar-o-log).
- Caronte web ganhou dois portões de permissão independentes: `Ambiente.web_habilitado`
  (Tartaro precisa habilitar) e a tabela `usuarios_web` (usuário precisa de
  permissão explícita por Tartaro, além do acesso físico normal) — veja
  [Caronte web: quem pode usar](#caronte-web-quem-pode-usar).
- Gestão de usuários direto na página do Tartaro
  (`/admin/ambientes/<id>`, seção "Usuários"): vincular usuário existente,
  criar um novo já pré-vinculado (sem o checklist de todos os ambientes), ou
  remover — além do toggle de Caronte Web por pessoa. A listagem geral de
  usuários (`/admin/usuarios`) ganhou busca e paginação.
- Visão Geral (`/admin/`) reorganizada: cards de status agrupados por
  Tartaro (lista "Ambientes (Tartaros)" com status agregado de cada um) ao
  lado de um card "Status dos Dispositivos" — antes só mostrava contadores
  soltos rotulados "Fechaduras", mesmo somando Cerberos e Caronte.
- Login via SUAP (OAuth2) no Caronte web, além do login por matrícula/PIN:
  sincronização por matrícula, cadastro automático pendente de aprovação
  para matrícula nova, tela de configuração em `/admin/integracao-suap` —
  veja [Login via SUAP (OAuth2)](#login-via-suap-oauth2).
- Usuário pode ter múltiplas TAGs RFID (`Usuario.tags`, relacionamento 1:N,
  sem conceito de TAG "padrão" — qualquer uma autentica onde o usuário já
  tem acesso). `TAG.numero` passou a ser validado como único no sistema
  inteiro ao salvar (só na aplicação, sem constraint de banco). Gerenciar
  as TAGs de um usuário é ação exclusiva do painel admin; `/caronte/perfil`
  agora só exibe a lista, somente leitura — veja [Modelo de
  dados](#modelo-de-dados).
- Cada TAG pode opcionalmente ser restrita a um subconjunto dos Tartaros do
  usuário (tabela N:N `tags_ambientes`) — sem restrição, continua valendo em
  todos, como antes. Configurável na seção "Usuários" de
  `/admin/ambientes/<id>`, com um toggle por TAG que avisa antes de
  restringir uma TAG até então universal ou de remover a última restrição —
  veja [Modelo de dados](#modelo-de-dados).
