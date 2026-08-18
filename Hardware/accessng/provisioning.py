"""AP + servidor HTTP mínimo de provisionamento - compartilhado pelos 4
firmwares MQTT.

Sobe um Access Point (AccessNG-<tipo>-<sufixo do MAC>, 192.168.4.1) e um
servidor HTTP em socket cru (sem framework) servindo um formulário
genérico guiado por DEFAULTS/SENSITIVE_KEYS - o mesmo dict que cada
firmware já usa para cfg()/fallback, então este módulo nunca precisa
conhecer o schema de nenhum dispositivo específico.

Roda inteiramente dentro da fase de boot.py, antes de main.py (e
portanto antes de MQTT/Wiegand/UART/driver de display) ser importado -
o servidor tem a heap inteira do dispositivo disponível, sem
concorrência com nenhum módulo de aplicação.

Sem gatilho de saída além do portal, um dispositivo que caiu aqui por
causa de uma rede Wi-Fi só temporariamente indisponível (router
reiniciando, interferência, blip do provedor) ficaria preso servindo o
AP para sempre, mesmo depois da rede voltar - até alguém aparecer
fisicamente e reenviar o mesmo formulário só para forçar uma nova
tentativa. Por isso start() também tenta reconectar à rede configurada
em segundo plano, periodicamente, em paralelo ao AP - ver
_maybe_recover_wifi().
"""

import gc
import machine
import network
import socket
import time

from accessng import config, wifi, watchdog

# Intervalo entre tentativas de reconexão em segundo plano enquanto o
# portal está no ar. Bloqueia o laço de accept() por até ~10s durante
# cada tentativa (accessng.wifi.try_connect_once) - um cliente tentando
# abrir o portal nesse meio-tempo só fica com a conexão enfileirada
# (backlog do listen()), não perdida.
_WIFI_RETRY_INTERVAL_MS = 60000

_HTTP_404 = b"HTTP/1.0 404 Not Found\r\nConnection: close\r\n\r\n"
_HTTP_OK_HEADER = (
    b"HTTP/1.0 200 OK\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"Connection: close\r\n\r\n"
)

_PAGE_CSS = (
    "body{font-family:sans-serif;max-width:480px;margin:1.5rem auto;padding:0 1rem;color:#222}"
    "h1{font-size:1.2rem}"
    ".row{margin-bottom:.7rem;display:flex;flex-direction:column}"
    "label{font-size:.85rem;color:#555;margin-bottom:.2rem}"
    "input,select{padding:.4rem;font-size:1rem}"
    "button{padding:.6rem 1rem;font-size:1rem;margin-top:.5rem}"
    ".error{color:#b00020;font-weight:bold}"
    ".info{color:#555;font-size:.85rem}"
)


def _start_ap(device_type, mac_suffix):
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ssid = "AccessNG-%s-%s" % (device_type, mac_suffix)
    ap.config(essid=ssid, authmode=network.AUTH_OPEN)
    try:
        ap.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
    except Exception as e:
        print("[Provisioning] ifconfig padrão mantido:", e)
    return ap, ssid


def start(device_type, mac_suffix, defaults, sensitive_keys):
    """Nunca retorna em operação normal - fica servindo o AP+portal até
    um POST válido gravar config.json e reiniciar, OU até a reconexão
    em segundo plano (_maybe_recover_wifi) confirmar que a rede
    configurada voltou a funcionar."""
    _, ssid = _start_ap(device_type, mac_suffix)
    cfg_file, _ = config.load()
    sta = _init_sta_retry()
    next_wifi_check = time.ticks_add(time.ticks_ms(), _WIFI_RETRY_INTERVAL_MS)

    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    s.bind(("0.0.0.0", 80))
    s.listen(1)
    # Não-bloqueante + poll, e NÃO settimeout(): visto em campo que
    # accept() com settimeout(3) ainda assim não devolvia o controle a
    # tempo de alimentar o watchdog de 8s (accessng.watchdog) nesta placa
    # - resultado foi um crash-loop real (ESP-IDF task_wdt abortando o
    # processo) toda vez que o dispositivo entrava em modo recovery, já
    # que "ninguém conectou ainda" é o estado NORMAL enquanto se espera
    # um técnico, não uma falha. setblocking(False) garante que accept()
    # sempre retorna na hora (com OSError se não há conexão pendente),
    # sem depender do timeout do socket funcionar como esperado.
    s.setblocking(False)
    print("[Provisioning] http://192.168.4.1/  (SSID: %s)" % ssid)
    while True:
        watchdog.feed()
        if sta is not None and time.ticks_diff(time.ticks_ms(), next_wifi_check) >= 0:
            next_wifi_check = time.ticks_add(time.ticks_ms(), _WIFI_RETRY_INTERVAL_MS)
            _maybe_recover_wifi(cfg_file, defaults)
        try:
            conn, addr = s.accept()
        except OSError:
            time.sleep_ms(200)
            continue
        try:
            _handle(conn, device_type, mac_suffix, defaults, sensitive_keys)
        except Exception as e:
            print("[Provisioning] erro atendendo %s: %s" % (addr, e))
        finally:
            try:
                conn.close()
            except Exception:
                pass
            gc.collect()


def _init_sta_retry():
    """Ativa a interface STA em paralelo à AP para permitir a tentativa
    periódica de reconexão em segundo plano (_maybe_recover_wifi). Em
    boot.py, a STA_IF já foi ativada (e a conexão já falhou) antes de
    chegar aqui - reativá-la é um no-op seguro. Em alguma placa/build
    onde o modo AP+STA simultâneo não for suportado, isso falha e o
    portal segue funcionando normalmente, só sem a reconexão automática
    (mesmo comportamento de antes desta mudança)."""
    try:
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        return sta
    except Exception as e:
        print("[Provisioning] STA indisponível junto com AP (%s) - "
              "sem reconexão automática em segundo plano" % e)
        return None


def _maybe_recover_wifi(cfg_file, defaults):
    """Tenta reconectar à rede Wi-Fi configurada, em paralelo ao AP - a
    STA_IF já foi deixada ativa por _init_sta_retry(), então
    wifi.try_connect_once() (que sempre busca network.WLAN(STA_IF) por
    conta própria) já tem o que precisa sem receber o objeto aqui. Se
    conseguir, a causa mais provável de ter caído em recovery (rede fora
    do ar, sinal fraco, blip temporário) já não existe mais - zera o
    contador de crash-loop (mesmo efeito de reenviar o formulário com a
    config atual) e reinicia direto para o boot normal, sem esperar
    ninguém aparecer fisicamente. Nunca propaga exceção - uma falha
    aqui não pode derrubar o portal."""
    ssid = config.get(cfg_file, defaults, "WIFI_SSID")
    senha = config.get(cfg_file, defaults, "WIFI_PASS")
    if not ssid:
        return
    print("[Provisioning] Tentando reconectar a %s em segundo plano..." % ssid)
    try:
        ok = wifi.try_connect_once(ssid, senha, timeout_s=10)
    except Exception as e:
        print("[Provisioning] Falha ao tentar reconectar:", e)
        return
    if not ok:
        return

    print("[Provisioning] Rede recuperada - saindo do modo recovery")
    state = config.load_state()
    state["boot_count"] = 0
    state["last_boot_ok"] = False
    config.save_state(state)
    time.sleep(1)
    machine.reset()


def _handle(conn, device_type, mac_suffix, defaults, sensitive_keys):
    # Não-bloqueante durante a LEITURA da requisição, pelo mesmo motivo do
    # accept() em start() - um cliente que demora pra mandar os dados não
    # pode travar o watchdog. Volta pra bloqueante antes de escrever a
    # resposta (conexão local de AP, poucos KB - sem risco real de travar
    # por muito tempo, e write() em modo não-bloqueante corre o risco de
    # mandar só parte dos dados).
    conn.setblocking(False)
    method, path, headers, body = _read_request(conn)
    conn.setblocking(True)

    if method == "GET":
        cfg_file, _ = config.load()
        conn.write(_render_form(device_type, mac_suffix, defaults, sensitive_keys, cfg_file))
        return

    if method == "POST" and path == "/save":
        cfg_file, _ = config.load()
        params = _parse_form(body)
        # Começa do que já está gravado (não de um dict vazio) - um campo
        # deixado em branco no formulário significa "manter o valor atual",
        # nunca "apagar". Sem isso, reenviar o formulário só pra forçar uma
        # nova tentativa de conexão (ex.: depois que a rede voltou) exigia
        # redigitar TODOS os campos, inclusive os sensíveis, ou eles
        # silenciosamente sumiam do config.json.
        cfg = dict(cfg_file)
        for key, default in defaults.items():
            if key in params and params[key] != "":
                cfg[key] = _coerce(default, params[key])
        if not cfg.get("WIFI_SSID"):
            conn.write(_render_form(device_type, mac_suffix, defaults, sensitive_keys, cfg_file,
                                     error="WIFI_SSID é obrigatório"))
            return

        config.save(cfg)
        state = config.load_state()
        state["boot_count"] = 0
        state["last_boot_ok"] = False
        state["pending_update"] = False
        config.save_state(state)

        conn.write(_render_ack())
        conn.close()
        time.sleep(1)  # deixa o navegador renderizar antes do reboot
        machine.reset()
        return

    if method == "POST" and path == "/reboot":
        # Só reinicia - não toca em config.json nem no estado de boot
        # (boot_count/pending_update). Existe pra quem quer forçar uma
        # nova tentativa de conexão na hora (ex.: acabou de arrumar o
        # roteador) sem esperar o _maybe_recover_wifi periódico, e sem
        # precisar passar pelo formulário de "salvar" só pra isso.
        conn.write(_render_ack(mensagem="Reiniciando sem salvar alterações..."))
        conn.close()
        time.sleep(1)
        machine.reset()
        return

    conn.write(_HTTP_404)


def _read_request(conn, max_head=2048, timeout_ms=5000):
    """Leitura não-bloqueante com prazo total (não settimeout() por
    chamada - mesmo motivo do accept() em start(), ver comentário lá:
    não dá pra confiar que o timeout do socket sozinho garanta retorno a
    tempo de alimentar o watchdog). request-line + headers cabem em
    max_head, corpo do POST lido exatamente por Content-Length (nunca
    "até fechar"), ao contrário do download confiável de accessng.ota
    (aqui o cliente é um navegador arbitrário, não o servidor Access-NG).
    conn precisa já estar em modo não-bloqueante (setblocking(False))."""
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < max_head:
        watchdog.feed()
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            return None, None, {}, b""
        try:
            chunk = conn.read(512)
        except OSError:
            chunk = None  # nada disponível ainda (EAGAIN) - tenta de novo
        if chunk:
            buf += chunk
        elif chunk == b"":
            break  # conexão fechada pelo cliente
        else:
            time.sleep_ms(50)
    sep = buf.find(b"\r\n\r\n")
    if sep == -1:
        return None, None, {}, b""

    head = buf[:sep].decode("utf-8", "ignore")
    lines = head.split("\r\n")
    parts = lines[0].split()
    if len(parts) < 2:
        return None, None, {}, b""
    method, path = parts[0], parts[1]

    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    try:
        content_length = int(headers.get("content-length", "0") or "0")
    except ValueError:
        content_length = 0
    body = buf[sep + 4:]
    while len(body) < content_length:
        watchdog.feed()
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            break
        try:
            chunk = conn.read(min(512, content_length - len(body)))
        except OSError:
            chunk = None
        if chunk:
            body += chunk
        elif chunk == b"":
            break
        else:
            time.sleep_ms(50)

    return method, path, headers, body


def _parse_form(body):
    text = body.decode("utf-8", "ignore")
    result = {}
    for pair in text.split("&"):
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        result[_unquote(k)] = _unquote(v)
    return result


def _unquote(s):
    s = s.replace("+", " ")
    raw = s.encode("utf-8")
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        if raw[i:i + 1] == b"%" and i + 2 < n:
            try:
                out.append(int(raw[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(raw[i])
        i += 1
    return bytes(out).decode("utf-8", "ignore")


def _coerce(default, value_str):
    if isinstance(default, bool):
        return value_str.strip().lower() in ("1", "true", "yes", "sim", "on")
    if isinstance(default, int):
        try:
            return int(value_str)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(value_str)
        except ValueError:
            return default
    return value_str


def _html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _render_form(device_type, mac_suffix, defaults, sensitive_keys, cfg_file, error=None):
    rows = []
    for key, default in defaults.items():
        if isinstance(default, list):
            # Sem coerção de volta pra lista em _coerce() (só bool/int/
            # float são tratados) - editar via um <input> de texto único
            # gravaria uma string solta em config.json, corrompendo a
            # config em vez de corrigi-la. Campos assim (ex.: INPUT_PINS
            # do Cerberos enxuto) continuam só editáveis direto no
            # config.json/mpremote, não pelo portal de recovery.
            continue
        sensitive = key in sensitive_keys
        # Pré-preenche com o que já está em config.json quando existir -
        # sem isso, reenviar o formulário (ex.: só pra corrigir um campo)
        # exigiria redigitar todos os outros também, já que _handle()
        # trata "em branco" como "manter o valor atual" (não mais como
        # "apagar" - ver comentário lá).
        current = cfg_file.get(key, default)
        if isinstance(default, bool):
            atual = bool(current)
            sel_yes = " selected" if atual else ""
            sel_no = "" if atual else " selected"
            field = (
                '<select name="%s"><option value="1"%s>Sim</option>'
                '<option value="0"%s>Não</option></select>'
            ) % (key, sel_yes, sel_no)
        elif isinstance(default, int) or isinstance(default, float):
            val = "" if sensitive else _html_escape(str(current))
            field = '<input type="number" step="any" name="%s" value="%s">' % (key, val)
        else:
            if sensitive:
                # Nunca preenche o valor de verdade (senha/chave não pode
                # ir pro HTML) - só sinaliza que já existe um, pra deixar
                # claro que ficar em branco aqui MANTÉM o que já está
                # gravado em vez de apagar.
                placeholder = ("já configurado - deixe em branco para manter"
                                if key in cfg_file else "")
                field = ('<input type="password" name="%s" value="" '
                         'placeholder="%s" autocomplete="off">'
                         ) % (key, _html_escape(placeholder))
            else:
                val = _html_escape(str(current))
                field = '<input type="text" name="%s" value="%s">' % (key, val)
        rows.append('<div class="row"><label>%s</label>%s</div>' % (key, field))

    error_html = '<p class="error">%s</p>' % _html_escape(error) if error else ""
    state = config.load_state()
    versao = state.get("current_version") or "?"

    body = (
        "<html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Configuração Access-NG</title><style>%s</style></head><body>"
        "<h1>Configuração Access-NG</h1>"
        "<p class='info'>Equipamento: %s-%s &middot; Firmware: %s</p>"
        "%s"
        "<form method='POST' action='/save'>%s"
        "<button type='submit'>Salvar e reiniciar</button>"
        "</form>"
        "<form method='POST' action='/reboot' style='margin-top:.5rem;'>"
        "<button type='submit'>Reiniciar sem salvar</button>"
        "</form></body></html>"
    ) % (_PAGE_CSS, device_type, mac_suffix, versao, error_html, "".join(rows))

    return _HTTP_OK_HEADER + body.encode("utf-8")


def _render_ack(mensagem=None):
    body = (
        "<html><head><meta charset='utf-8'></head><body>"
        "<h1>%s</h1>"
        "<p>%s</p>"
        "</body></html>"
    ) % (
        "Configuração salva" if mensagem is None else "Reiniciando",
        mensagem or "O dispositivo vai reiniciar e tentar conectar à rede informada.",
    )
    return _HTTP_OK_HEADER + body.encode("utf-8")
