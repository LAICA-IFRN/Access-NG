"""
Access-NG - Instalador do esquema boot.py/main.py/accessng/ para uma
BitDogLab V6 / Pico W com MicroPython recém-gravado (sem nenhum arquivo
de aplicação ainda) - ou para recuperar um dispositivo travado, apagando
tudo primeiro (ver ERASE_ANTES abaixo).

Autocontido de propósito: não pode depender de accessng/ nem de
device_defaults.py, porque é exatamente isso que ainda não existe no
dispositivo neste ponto. Baixa tudo direto do servidor Access-NG por
HTTP puro (mesma rota /ota/<filepath> que o OTA normal já usa). Mesmo
padrão dos instaladores do Caronte, do FECHO e do Cerberos enxuto.

--- USO -----------------------------------------------------------------

  1. Grave o firmware MicroPython no Pico W normalmente (drag-and-drop do
     .uf2), se ainda não estiver gravado.
  2. Edite o dicionário CONFIG logo abaixo com os dados reais deste
     dispositivo (Wi-Fi + o que mais você souber - MQTT_BROKER,
     DEVICE_KEY cadastrada no painel, etc.). Campos que você não
     preencher ficam com o valor padrão do firmware até serem
     completados depois pelo portal de provisionamento.
  3. Copie e rode com mpremote (troque /dev/ttyACM0 pela porta certa):

       mpremote connect /dev/ttyACM0 cp installer_bitdoglab.py :installer.py
       mpremote connect /dev/ttyACM0 run installer.py

     (ou copie e rode via Thonny/REPL - `import installer`)

  4. Ao terminar, o dispositivo reinicia sozinho. Se CONFIG tiver pelo
     menos WIFI_SSID/WIFI_PASS/MQTT_BROKER/DEVICE_KEY corretos, ele já
     sobe operacional. Campos faltando (ou errados) fazem boot.py cair
     no modo recovery (AP "AccessNG-BitDogLab-XXXX" em 192.168.4.1)
     depois de algumas tentativas - complete por lá.

--- ERASE_ANTES = True ---------------------------------------------------

  Útil para recuperar um dispositivo que ficou preso num estado ruim
  (ex.: boot.py/main.py corrompidos) - apaga os arquivos da aplicação
  (boot.py, main.py, device_defaults.py, accessng/, umqtt/, ssd1306.py,
  boot_state.json) antes de reinstalar do zero. NÃO apaga config.json
  (preserva a configuração já gravada, se você não quiser perdê-la - mas
  CONFIG abaixo só é escrito em config.json se ele ainda não existir,
  então tanto faz).
"""

import network
import socket
import time
import os
import json

# ============================================================
# EDITE AQUI antes de rodar - dados deste dispositivo específico.
# Campos vazios/ausentes ficam no valor padrão do firmware (main.py
# DEFAULTS) até serem completados pelo portal de provisionamento.
# ============================================================
CONFIG = {
    "WIFI_SSID": "nome-da-rede",
    "WIFI_PASS": "senha-da-rede",
    "MQTT_BROKER": "laica.ifrn.edu.br",
    "MQTT_PORT": 1883,
    "DEVICE_KEY": "chave-cadastrada-no-painel",
}

ERASE_ANTES = False

OTA_HOST = "laica.ifrn.edu.br"
OTA_PORT = 80

# ============================================================

_FILES = [
    ("Hardware/accessng/__init__.py", "/accessng/__init__.py"),
    ("Hardware/accessng/config.py", "/accessng/config.py"),
    ("Hardware/accessng/wifi.py", "/accessng/wifi.py"),
    ("Hardware/accessng/recovery.py", "/accessng/recovery.py"),
    ("Hardware/accessng/provisioning.py", "/accessng/provisioning.py"),
    ("Hardware/accessng/ota.py", "/accessng/ota.py"),
    ("Hardware/accessng/watchdog.py", "/accessng/watchdog.py"),
    ("Hardware/bibliotecas/umqtt/__init__.py", "/umqtt/__init__.py"),
    ("Hardware/bibliotecas/umqtt/simple.py", "/umqtt/simple.py"),
    ("Hardware/bibliotecas/umqtt/robust.py", "/umqtt/robust.py"),
    ("Hardware/bibliotecas/ssd1306.py", "/ssd1306.py"),
    ("Hardware/Fechadura/device_defaults_bitdoglab.py", "/device_defaults.py"),
    ("Hardware/Fechadura/boot_bitdoglab.py", "/boot.py"),
    ("Hardware/Fechadura/main_bitdoglab.py", "/main.py"),
]

_ERASE_PATHS = [
    "/boot.py", "/main.py", "/device_defaults.py", "/boot_state.json",
    "/main.bak", "/main.py.new", "/ssd1306.py",
    "/accessng/__init__.py", "/accessng/config.py", "/accessng/wifi.py",
    "/accessng/recovery.py", "/accessng/provisioning.py", "/accessng/ota.py",
    "/accessng/watchdog.py",
    "/umqtt/__init__.py", "/umqtt/simple.py", "/umqtt/robust.py",
]


def _load_existing_config():
    """Se já existe um config.json no dispositivo (ex.: preservado por um
    ERASE_ANTES=True anterior, ou de uma instalação parcial), seus
    valores têm prioridade sobre CONFIG (topo deste arquivo) pra
    conectar - sem isso, recuperar um dispositivo que já tinha Wi-Fi
    configurado exigia redigitar as credenciais reais em CONFIG de novo,
    mesmo o docstring de ERASE_ANTES prometendo que a config já gravada
    seria reaproveitada."""
    try:
        with open("config.json") as f:
            return json.load(f)
    except Exception:
        return {}


def _connect_wifi(existing_cfg):
    ssid = existing_cfg.get("WIFI_SSID") or CONFIG["WIFI_SSID"]
    senha = existing_cfg.get("WIFI_PASS") or CONFIG["WIFI_PASS"]
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    time.sleep_ms(100)
    if not wlan.isconnected():
        print("[Installer] Conectando em %s..." % ssid)
        wlan.connect(ssid, senha)
        for _ in range(30):
            if wlan.isconnected():
                break
            time.sleep_ms(500)
    if not wlan.isconnected():
        raise RuntimeError(
            "Não foi possível conectar ao Wi-Fi - confira "
            "CONFIG['WIFI_SSID']/CONFIG['WIFI_PASS'] no topo deste arquivo"
            + (" (ou o config.json já existente no dispositivo)" if existing_cfg.get("WIFI_SSID") else ""))
    print("[Installer] Wi-Fi OK, IP:", wlan.ifconfig()[0])


def _mkdir(path):
    parts = path.split("/")[1:-1]
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            os.mkdir(cur)
        except OSError:
            pass


def _erase():
    print("[Installer] ERASE_ANTES=True - apagando instalação anterior...")
    for path in _ERASE_PATHS:
        try:
            os.remove(path)
            print("[Installer]   removido", path)
        except OSError:
            pass


def _http_download(repo_path, dest_path, timeout=20):
    """GET HTTP simples (sem TLS) - grava direto em dest_path por
    streaming, mesma lógica já testada em accessng.ota.http_request(),
    reproduzida aqui de forma autocontida porque accessng ainda não
    existe no dispositivo neste ponto."""
    sock = None
    try:
        addr = socket.getaddrinfo(OTA_HOST, OTA_PORT, 0, socket.SOCK_STREAM)[0][-1]
        sock = socket.socket()
        sock.settimeout(timeout)
        sock.connect(addr)

        req = (
            "GET /access-ng/ota/" + repo_path + " HTTP/1.1\r\n"
            "Host: " + OTA_HOST + "\r\n"
            "User-Agent: access-ng-installer\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.write(req.encode("utf-8"))

        buf = b""
        status = None
        total_bytes = None
        received = 0
        out = open(dest_path, "wb")
        header_done = False
        try:
            while True:
                chunk = sock.read(1024)
                if not chunk:
                    break
                if not header_done:
                    buf += chunk
                    sep = buf.find(b"\r\n\r\n")
                    if sep == -1:
                        continue
                    header_done = True
                    header_str = buf[:sep].decode("utf-8", "ignore")
                    status = int(header_str.split("\r\n", 1)[0].split()[1])
                    for line in header_str.split("\r\n")[1:]:
                        if line.lower().startswith("content-length:"):
                            try:
                                total_bytes = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                pass
                    rest = buf[sep + 4:]
                    buf = b""
                    if rest:
                        out.write(rest)
                        received += len(rest)
                        if total_bytes and received >= total_bytes:
                            break
                else:
                    out.write(chunk)
                    received += len(chunk)
                    if total_bytes and received >= total_bytes:
                        break
        finally:
            out.close()

        if status != 200:
            os.remove(dest_path)
            raise RuntimeError("status=%s" % status)
        if total_bytes is not None and received < total_bytes:
            os.remove(dest_path)
            raise RuntimeError("download incompleto: %d/%d bytes" % (received, total_bytes))
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def _validate(path, dest_path):
    """compile() para arquivos pequenos (accessng/bibliotecas/boot.py) -
    barato e pega download corrompido, incluindo __init__.py legitimamente
    vazio (compile("") é válido). main.py (~40KB) é grande demais pra
    isso (visto em campo: estoura memória) - só checa tamanho mínimo,
    mesmo critério do OTA normal (_valida_payload). dest_path (não path,
    que é o nome do arquivo temporário "*.tmp") é o que decide qual
    critério aplicar."""
    size = os.stat(path)[6]
    if dest_path == "/main.py":
        return size > 500
    try:
        with open(path) as f:
            source = f.read()
        compile(source, path, "exec")
        return True
    except Exception as e:
        print("[Installer] Erro de sintaxe em %s: %s" % (path, e))
        return False


def _write_config():
    try:
        os.stat("config.json")
        print("[Installer] config.json já existe - não sobrescrevendo "
              "(apague manualmente antes se quiser substituir)")
        return
    except OSError:
        pass
    with open("config.json.tmp", "w") as f:
        f.write(json.dumps(CONFIG))
    os.rename("config.json.tmp", "config.json")
    print("[Installer] config.json gravado")


def run():
    print("\n" + "=" * 56)
    print("  Access-NG - Instalador (BitDogLab V6 / Pico W)")
    print("=" * 56)

    if ERASE_ANTES:
        _erase()

    _connect_wifi(_load_existing_config())

    for repo_path, dest_path in _FILES:
        print("[Installer] Baixando %s -> %s ..." % (repo_path, dest_path))
        _mkdir(dest_path)
        tmp = dest_path + ".tmp"
        _http_download(repo_path, tmp)
        if not _validate(tmp, dest_path):
            os.remove(tmp)
            raise RuntimeError("Falha ao validar %s - instalação abortada, "
                                "nada foi trocado além do que já baixou "
                                "até aqui" % repo_path)
        try:
            os.remove(dest_path)
        except OSError:
            pass
        os.rename(tmp, dest_path)
        print("[Installer]   OK (%d bytes)" % os.stat(dest_path)[6])

    _write_config()

    print("[Installer] Concluído! Reiniciando em 3s...")
    time.sleep(3)
    import machine
    machine.reset()


run()
