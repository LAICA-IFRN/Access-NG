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
    ├── Fechadura/
    │   ├── Cerberos_UART.ino          # ESP com Wi-Fi/API/relé e UART para leitor RFID
    │   ├── Cerberos.ino               # Sketch alternativo/legado
    │   ├── CerberosESP32.py           # Firmware MicroPython (ESP32) — Cerberos MQTT enxuto, com entrada física
    │   ├── Cerberos_BitDogLab.py      # Firmware MicroPython (Pico W) — modo REST
    │   └── Cerberos_BitDogLab_MQTT.py # Firmware MicroPython (Pico W) — modo MQTT
    ├── Autenticador/
    │   ├── Caronte_RFID.ino           # ESP leitor RFID via MFRC522, envia tag por UART ao Cerberos
    │   └── CaronteESP32C3.py          # Firmware MicroPython (ESP32-C3) — Caronte com leitor Wiegand, MQTT
    ├── Ambiente/
    │   └── TempHumi.ino               # Sensor de temperatura/umidade
    └── ModPotencia/
        └── Servo.ino                  # Módulo de potência/servo
```

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
2. Faz login com `matricula` e `pin`.
3. O navegador solicita permissão de geolocalização.
4. O portal busca ambientes próximos em `GET /caronte/ambientes-proximos?lat=&lon=`.
5. O usuário toca em **Entrar**.
6. O servidor valida novamente a localização, confere permissão do usuário e aciona os Cerberoses do ambiente.

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
4. Com o `ambiente_id` recebido, o dispositivo publica `access-ng/heartbeat/{mac}` periodicamente, enviando `mac`, `uptime_ms` e `uptime_s`, e monta os tópicos `access-ng/{ambiente_id}/...`.
5. Um Caronte MQTT publica a TAG lida em `access-ng/{amb_id}/caronte/{mac}/tag`; o Sistema autentica com `Tartaro.autenticarTAGDetalhado()`, responde em `access-ng/{amb_id}/caronte/{mac}/result` e, se autorizado, publica o comando de abertura para os Cerberoses do ambiente.
6. Um Cerberos MQTT assina `access-ng/{amb_id}/cerberos/{mac}/command`; ao receber `{"command":"unlock"}` aciona o relé.
7. Quando o Cerberos tem entradas físicas configuradas (botão/contato local), ele publica `access-ng/{amb_id}/cerberos/{mac}/entrada` com `{"mac":..., "pin":...}` ao detectar o acionamento; o Sistema grava o evento como `entrada_fisica` no log, mesmo sem MAC cadastrado.
8. Aberturas manuais (`/admin/cerberoses/<id>/abrir`), via Caronte web (`/caronte/solicitar`) e via Caronte fixo REST (`/caronte/autenticarTag`) também publicam o comando MQTT para os Cerberoses vinculados a um broker, além do mecanismo de fila REST existente.

O MAC nos tópicos usa `-` no lugar de `:` (compatibilidade com brokers que tratam `:` como separador). O Sistema aceita ambos os formatos ao consultar o banco.

## Requisitos

- Python 3.10+ recomendado.
- SQLite.
- `paho-mqtt` (incluído em `Sistema/requirements.txt`) — necessário para o suporte a MQTT. Sem ele, o `mqtt_service` fica desabilitado e o Sistema funciona normalmente apenas com REST.
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

Colunas adicionadas automaticamente em `ambientes`:

- `latitude FLOAT`
- `longitude FLOAT`
- `raio_metros INTEGER`

## Modelo de dados

### Usuario

Tabela: `usuarios`

- `id`
- `nome`
- `matricula`
- `pin`
- `admin`
- relacionamento com `TAG`
- relacionamento com `MAC`
- relacionamento muitos-para-muitos com `Ambiente` via `usuarios_ambientes` (frequentadores/acesso físico)
- relacionamento um-para-muitos com `PapelAmbiente` (papéis por Tartaro — gerente/colaborador/leitor)

### TAG

Tabela: `tags`

- `id`
- `numero`
- `usuario_id`

Usada pelo Caronte RFID para autenticação física.

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
- `frequentadores`
- `papeis` (usuários com papel `gerente`/`colaborador`/`leitor` neste Tartaro)
- `cerberoses`
- `carontes`

`latitude`, `longitude` e `raio_metros` são usados pelo Caronte web para validar
proximidade. O raio padrão usado pelo código é 50 metros quando o campo está vazio.

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

Representa o leitor/autenticador fixo.

## Papéis e permissões

Além do administrador geral (`Usuario.admin = True`, acesso irrestrito), o
painel suporta papéis **por Tartaro**, atribuídos via `PapelAmbiente`:

| Papel | Pode | Não pode |
| --- | --- | --- |
| **Administrador geral** | Tudo: Tartaros, Brokers MQTT, Cerberoses/Carontes/Usuários/Logs de qualquer Tartaro, conceder qualquer papel ou `admin`. | — |
| **Gerente** | Cadastrar/editar/excluir usuários, Cerberoses e Carontes do seu Tartaro; nomear `colaborador`/`leitor` para gente do mesmo Tartaro; ler os logs do seu Tartaro. | Criar/editar Tartaros ou Brokers MQTT; conceder `admin` geral ou nomear outro `gerente`. |
| **Colaborador** | Cadastrar novos usuários no seu Tartaro. | Editar/excluir usuários existentes, gerenciar Cerberoses/Carontes, ver logs, atribuir papéis. |
| **Leitor** | Visualizar (somente leitura) os logs/eventos do seu Tartaro. | Qualquer ação de escrita no painel. |
| **Usuário regular** (sem papel) | Acessar o portal Caronte (`/caronte`) e atualizar a própria TAG e PIN em `/caronte/perfil`. | Entrar no painel `/admin`. |

Os papéis são hierárquicos dentro do mesmo Tartaro: `gerente` já cobre as
capacidades de `colaborador` (cadastrar usuários) e `leitor` (ler logs), além
de gerenciar dispositivos. Cada usuário tem no máximo um papel por Tartaro —
`PapelAmbiente` usa chave primária composta (`usuario_id` + `ambiente_id`).

Qualquer usuário com `admin=True` ou com pelo menos um papel pode entrar em
`/admin/login`; o menu lateral e o conteúdo das telas se ajustam
automaticamente ao que aquele usuário pode ver/fazer. Tartaros, Brokers MQTT
e a exclusão/limpeza de logs continuam exclusivos do administrador geral.

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
| `GET/POST` | `/caronte/perfil` | Autoatendimento: nome e matrícula somente leitura; atualiza a própria TAG RFID e o PIN. |
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
| `GET` | `/admin/` | Visão Geral: contagens de ambientes/Cerberoses/Carontes/usuários, dispositivos online/offline, gráficos de linha de latência média da API (24h) e de aberturas por dia (14 dias), e últimas atividades/tentativas de acesso. |
| `GET` | `/admin/ambientes` | Lista Tartaros. |
| `GET/POST` | `/admin/ambientes/novo` | Cria Tartaro. |
| `GET` | `/admin/ambientes/<id>` | Visão do Tartaro: gráfico de linha de aberturas por dia, com período personalizável (`?desde=AAAA-MM-DD&ate=AAAA-MM-DD`, padrão últimos 14 dias), e a lista dos equipamentos daquele Tartaro com o SLA (24h) de cada um. |
| `GET/POST` | `/admin/ambientes/<id>/editar` | Edita Tartaro. |
| `POST` | `/admin/ambientes/<id>/excluir` | Remove Tartaro. |
| `GET` | `/admin/cerberoses` | Lista Cerberoses. |
| `GET/POST` | `/admin/cerberoses/novo` | Cria Cerberos. |
| `GET` | `/admin/cerberoses/<id>` | Visão do Cerberos: gauge de SLA (% online) das últimas 24h e gráfico de uptime com período personalizável em horas ou dias (`?unidade=hora\|dia&quantidade=N`). |
| `GET/POST` | `/admin/cerberoses/<id>/editar` | Edita Cerberos. |
| `POST` | `/admin/cerberoses/<id>/abrir` | Envia comando manual de abertura para o Cerberos. |
| `POST` | `/admin/cerberoses/<id>/excluir` | Remove Cerberos. |
| `GET` | `/admin/carontes` | Lista Carontes fixos. |
| `GET/POST` | `/admin/carontes/novo` | Cria Caronte fixo. |
| `GET` | `/admin/carontes/<id>` | Visão do Caronte: gauge de SLA (% online) das últimas 24h e gráfico de uptime com período personalizável em horas ou dias (`?unidade=hora\|dia&quantidade=N`). |
| `GET/POST` | `/admin/carontes/<id>/editar` | Edita Caronte fixo. |
| `POST` | `/admin/carontes/<id>/excluir` | Remove Caronte fixo. |
| `GET` | `/admin/brokers` | Lista Brokers MQTT. |
| `GET/POST` | `/admin/brokers/novo` | Cria Broker MQTT e conecta o `mqtt_service`. |
| `GET/POST` | `/admin/brokers/<id>/editar` | Edita Broker MQTT e reconecta/desconecta conforme `ativo`. |
| `POST` | `/admin/brokers/<id>/excluir` | Desconecta e remove Broker MQTT. |
| `GET` | `/admin/usuarios` | Lista usuários. |
| `GET/POST` | `/admin/usuarios/novo` | Cria usuário e define ambientes permitidos. |
| `GET/POST` | `/admin/usuarios/<id>/editar` | Edita usuário e permissões. |
| `POST` | `/admin/usuarios/<id>/excluir` | Remove usuário. |
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
> `gerente`. O SLA é calculado em cima do histórico de contato já registrado
> em `AccessLog` (toda requisição de um dispositivo — REST ou heartbeat
> MQTT — grava uma linha com o `mac`); não há tabela nova nem coluna nova.
>
> O dashboard de estatísticas em `/admin/` (online/offline, gráficos de
> linha de latência média e de aberturas por dia, e atividades recentes) é
> restrito ao administrador geral (todos os Tartaros) e a quem tem papel
> `gerente` ou `leitor` (só do(s) Tartaro(s) onde tem o papel). Quem só tem
> papel `colaborador` vê a Visão Geral sem esses widgets e não tem acesso a
> `/admin/ambientes/<id>` nem a `/admin/logs`.

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
- `event_type` — tipo do evento, como `api_request`, `login_admin`, `comando_abertura`, `mqtt_heartbeat`, `mqtt_status`, `mqtt_command` ou `entrada_fisica`
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
    "uptime_ms": 123456,
    "uptime_s": 123
}
```

O Sistema grava esse payload nos logs do evento `mqtt_heartbeat`, útil para
debug de reinicializações e quedas de energia.

O firmware MQTT requer a biblioteca `umqtt` (`umqtt.robust` ou `umqtt.simple`)
instalada na placa via `mip`:

```python
import mip
mip.install("umqtt.robust")
```

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
    "INPUT_PINS"         : [26, 34],
    "INPUT_DEBOUNCE_MS"  : 200
}
```

- `INPUT_PINS` são entradas lógicas (ativo baixo, ex.: botão local) que abrem o
  relé diretamente no firmware e publicam `access-ng/{ambiente_id}/cerberos/{mac}/entrada`
  com `{"mac":..., "pin":...}`; o Sistema grava isso como evento `entrada_fisica`
  no log, mesmo que o MAC não esteja cadastrado.
- Cada pino tem seu próprio debounce (`INPUT_DEBOUNCE_MS`) controlado por
  timestamp pré-alocado por pino, para evitar alocação de memória dentro da
  interrupção (IRQ).
- No ESP32, `GPIO34` é somente entrada e não possui pull-up interno — use
  resistor pull-up externo quando o sinal for ativo baixo.
- Segue o mesmo fluxo de coldstart/heartbeat/comando MQTT dos demais firmwares
  e também requer `umqtt` instalado via `mip`.

### ESP32-C3 (MicroPython) — Caronte com leitor Wiegand

`Hardware/Autenticador/CaronteESP32C3.py` é o firmware do Caronte fixo para um
ESP32-C3 com leitor RFID Wiegand (D0/D1), substituindo o leitor MFRC522/UART
de `Caronte_RFID.ino` para essa placa. Não possui Cerberos embutido — apenas lê
a TAG e publica via MQTT.

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
    "AUTH_TIMEOUT_S"     : 5
}
```

- Os pulsos Wiegand são acumulados em um buffer pré-alocado dentro da ISR (sem
  GC); a leitura é considerada completa após `WG_TIMEOUT_MS` de silêncio nos
  pinos D0/D1.
- A TAG é decodificada para uma string hexadecimal maiúscula (`_decode_wiegand`),
  com tratamento dedicado para os formatos Wiegand de 26 e 34 bits (remoção dos
  bits de paridade) e fallback genérico para outros tamanhos. Cadastre a `TAG.numero`
  do usuário exatamente nesse formato hexadecimal, já que a comparação em
  `Tartaro.autenticarTAGDetalhado()` é sensível a maiúsculas/minúsculas.
- Publica a TAG em `access-ng/{ambiente_id}/caronte/{mac}/tag` com `{"tag":...,"chave":...}`
  e aguarda o resultado em `access-ng/{ambiente_id}/caronte/{mac}/result` por até
  `AUTH_TIMEOUT_S` segundos, sinalizando o resultado com bipes/LEDs
  (`feedback_allow`/`feedback_deny`).
- Segue o mesmo fluxo de coldstart/heartbeat MQTT dos demais firmwares e também
  requer `umqtt` instalado via `mip`.

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
- Para RFID, associe uma `TAG.numero` ao usuário.
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
