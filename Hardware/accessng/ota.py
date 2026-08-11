"""OTA (atualização remota de firmware) - compartilhado pelos 4 firmwares
MQTT.

A maior parte deste módulo é a lógica de OTA que cada firmware já tinha
individualmente (http_request/parse_version/validate_payload/
check_for_update/apply_update), só desglobalizada: parâmetros em vez de
globais do módulo, e sem chamar machine.reset() internamente - quem
decide reiniciar é sempre o chamador (main.py/boot.py).

rollback_if_pending()/confirm_boot_ok() substituem o antigo
_ota_boot_guard()/_ota_confirmar_versao_boa(), que viviam presos ao
conceito de "update pendente" via ota_pending.txt/ota_boot_attempts.txt -
agora só cuidam da parte específica de OTA (restaurar main.bak), a
detecção de crash-loop em si é genérica e vive em accessng.recovery.

ensure_dependencies() é novo: busca em Hardware/bibliotecas/ qualquer
biblioteca listada no manifesto (version.json) que ainda não exista
localmente. Não é o mecanismo de update de main.py - só cria o que falta,
sem comparar versão nem substituir nada que já funcione.

check_for_package_update()/apply_package_update()/rollback_package_if_pending()
são o mecanismo de update do PACOTE accessng/ em si (mais as bibliotecas
que cada firmware usa) - versionado à parte de main.py, contra
Hardware/accessng/version.json (um manifesto só, compartilhado pelos 4
firmwares, já que accessng/ é idêntico entre eles). Risco reconhecido:
boot.py importa accessng/config.py, wifi.py, recovery.py e watchdog.py
ANTES de poder decidir se entra em recovery - um pacote corrompido
poderia deixar o dispositivo preso falhando todo boot sem conseguir
nem mostrar o AP. Duas camadas de proteção cobrem isso: (1) validação
por compile() de TODO arquivo antes de trocar qualquer um (mesmo
critério que os migradores já usam), com backup individual (*.bak) de
cada arquivo trocado; (2) boot.py arma o watchdog de hardware ANTES de
tentar importar accessng/ e, se o import falhar mesmo assim, restaura
os *.bak sozinho (sem depender de nada em accessng/, que é exatamente o
que pode estar quebrado) - ver a docstring de boot.py.
"""

import gc
import json
import os
import socket
import time

from accessng import watchdog


def http_request(host, path, port=80, dest_file=None, timeout=10):
    """GET HTTP em host:port+path (sem TLS - o handshake RSA estoura a
    memória disponível no ESP32-C3; os arquivos servidos por /ota/ são
    públicos, sem segredo em trânsito). Se dest_file for informado, grava
    o corpo da resposta direto nesse arquivo (streaming) e retorna
    (status, None); senão acumula o corpo em memória e retorna
    (status, body_str). Retorna (None, None) em qualquer falha de rede."""
    sock = None
    t0 = time.time()
    gc.collect()
    try:
        ai = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        addr = ai[0][-1]
        sock = socket.socket()
        sock.settimeout(timeout)
        sock.connect(addr)

        req = (
            "GET " + path + " HTTP/1.1\r\n"
            "Host: " + host + "\r\n"
            "User-Agent: access-ng\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.write(req.encode("utf-8"))

        buf = b""
        status = None
        out = None
        total_bytes = None
        received = 0
        if dest_file:
            out = open(dest_file, "wb")
        header_done = False
        try:
            while True:
                watchdog.feed()
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
                    if out:
                        if rest:
                            out.write(rest)
                            received += len(rest)
                            if total_bytes and received >= total_bytes:
                                break
                    else:
                        buf = rest
                else:
                    if out:
                        out.write(chunk)
                        received += len(chunk)
                        if total_bytes and received >= total_bytes:
                            break
                    else:
                        buf += chunk
        finally:
            if out:
                out.close()

        if status is None:
            return None, None
        if dest_file and total_bytes is not None and received < total_bytes:
            print("[OTA] Download incompleto: %d/%d bytes" % (received, total_bytes))
            return None, None
        return status, (None if out else buf.decode("utf-8", "ignore"))
    except Exception as e:
        print("[OTA] Erro HTTP (%.1fs):" % (time.time() - t0), e)
        return None, None
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        gc.collect()


def parse_version(v):
    """Converte "1.3.10" em (1, 3, 10) para comparação numérica.
    Comparar como string quebra em versões de dois dígitos (ex.:
    "1.3.10" < "1.3.7" lexicograficamente)."""
    try:
        return tuple(int(p) for p in str(v).split("."))
    except (ValueError, AttributeError):
        return None


def check_for_update(host, port, version_path, current_version, enabled=True):
    """Busca version.json no repo. Retorna o dict remoto se a versão
    remota for numericamente MAIOR que a atual, ou None (sem update /
    OTA desativado / qualquer falha). Nunca reinstala uma versão igual
    ou mais antiga."""
    if not enabled:
        return None
    status, body = http_request(host, "/access-ng/ota/" + version_path, port=port)
    if status != 200 or not body:
        return None
    try:
        remote = json.loads(body)
    except Exception:
        return None

    remota_versao = remote.get("versao")
    remota_t = parse_version(remota_versao)
    atual_t = parse_version(current_version)
    if remota_t is None or atual_t is None:
        if remota_versao == current_version:
            return None
    elif remota_t <= atual_t:
        return None

    print("[OTA] Nova versão disponível:", remota_versao)
    return remote


def validate_payload(path, versao):
    """Checagem barata de sanidade do .py baixado antes de instalar. Lê
    em blocos para não carregar o firmware inteiro na RAM."""
    try:
        if os.stat(path)[6] < 500:
            return False
        needle_fw = b"FIRMWARE_VERSAO"
        needle_ver = str(versao).encode("utf-8")
        found_fw = False
        found_ver = False
        tail = b""
        with open(path, "rb") as f:
            while True:
                chunk = f.read(512)
                if not chunk:
                    break
                data = tail + chunk
                if not found_fw and needle_fw in data:
                    found_fw = True
                if needle_ver and not found_ver and needle_ver in data:
                    found_ver = True
                if found_fw and found_ver:
                    return True
                tail = data[-64:]
        return False
    except Exception as e:
        print("[OTA] Erro ao validar payload:", e)
        return False


def apply_update(state, host, port, firmware_path, remote,
                  target_file="main.py", backup_file="main.bak"):
    """Baixa o firmware, valida, troca target_file/backup_file e atualiza
    o boot_state.json (pending_update/previous_version/current_version).
    NÃO reinicia - quem decide reiniciar (e persistir o state atualizado)
    é o chamador. Retorna True se a troca foi aplicada."""
    versao = remote.get("versao", "")
    path = "/access-ng/ota/" + firmware_path
    print("[OTA] Baixando", "http://" + host + path)
    tmp_file = target_file + ".new"
    status, _ = http_request(host, path, port=port, dest_file=tmp_file, timeout=30)
    if status != 200 or not validate_payload(tmp_file, versao):
        print("[OTA] Download inválido (status=%s) - abortando" % status)
        try:
            os.remove(tmp_file)
        except OSError:
            pass
        return False

    try:
        os.remove(backup_file)
    except OSError:
        pass
    os.rename(target_file, backup_file)
    os.rename(tmp_file, target_file)

    state["pending_update"] = True
    state["previous_version"] = state.get("current_version")
    state["current_version"] = versao
    print("[OTA] Atualizado para", versao)
    return True


def rollback_if_pending(state, target_file="main.py", backup_file="main.bak"):
    """Chamado por boot.py quando há um crash-loop generalizado E há um
    update pendente: restaura o backup (rollback cirúrgico, sem precisar
    do modo AP completo). Só se aplica UMA vez por update pendente -
    depois de restaurar, pending_update vira False, então uma NOVA
    sequência de falhas (agora sem pending_update) cai direto no modo
    recovery completo em vez de tentar rollback de novo."""
    if not state.get("pending_update"):
        return False
    try:
        os.stat(backup_file)
    except OSError:
        return False
    try:
        os.remove(target_file)
    except OSError:
        pass
    os.rename(backup_file, target_file)
    state["pending_update"] = False
    state["current_version"] = state.get("previous_version")
    state["boot_count"] = 0
    return True


def confirm_boot_ok(state, version):
    """Chamado após o primeiro coldstart+heartbeat bem-sucedidos na
    versão atual: a versão é considerada estável. backup_file permanece
    como rede de segurança até a próxima atualização."""
    state["last_boot_ok"] = True
    state["boot_count"] = 0
    state["pending_update"] = False
    state["current_version"] = version


# --- Atualização do pacote accessng/ (independente da versão de main.py) ---

_ACCESSNG_MANIFEST_PATH = "Hardware/accessng/version.json"

# Fixo e igual pros 4 firmwares - accessng/ em si não varia por
# dispositivo (ao contrário de bibliotecas, que cada main_*.py passa
# explicitamente pra apply_package_update()/tem sua própria lista pra
# rollback_package_if_pending() verificar).
_ACCESSNG_FILES = (
    "__init__.py", "config.py", "wifi.py", "recovery.py",
    "provisioning.py", "ota.py", "watchdog.py",
)

# Candidatos de rollback: os 7 arquivos fixos de accessng/ + a união das
# bibliotecas usadas por QUALQUER um dos 4 firmwares - checar um .bak que
# não existe é barato (só um os.stat() que falha), então não precisa
# saber de antemão quais bibliotecas este dispositivo específico usa.
_PACKAGE_ROLLBACK_CANDIDATES = tuple(
    "/accessng/" + name for name in _ACCESSNG_FILES
) + (
    "/umqtt/__init__.py", "/umqtt/simple.py", "/umqtt/robust.py",
    "/sh1106.py", "/ssd1306.py",
)


def compile_check(path):
    """Validação de sintaxe via compile() - mesma checagem que os
    migradores já usam pra validar accessng/bibliotecas antes de
    instalar (não executa o módulo, só confirma que o bytecode é
    válido). Mais forte que validate_payload() (tamanho+substring), mas
    caro demais pra rodar no main.py (~40KB, estoura RAM) - só serve
    pros arquivos pequenos de accessng/bibliotecas."""
    gc.collect()
    try:
        with open(path) as f:
            source = f.read()
        compile(source, path, "exec")
        return True
    except Exception as e:
        print("[OTA] Erro de sintaxe em %s: %s" % (path, e))
        return False


def check_for_package_update(host, port, current_version, enabled=True):
    """Verifica se há uma versão nova do pacote accessng/ - mesmo
    mecanismo de check_for_update(), contra o manifesto fixo
    compartilhado pelos 4 firmwares (Hardware/accessng/version.json)."""
    return check_for_update(host, port, _ACCESSNG_MANIFEST_PATH, current_version, enabled)


def apply_package_update(state, host, port, remote, bibliotecas=()):
    """Baixa e valida TODOS os arquivos de accessng/ (mais as
    bibliotecas passadas) antes de trocar qualquer um - nenhum arquivo
    que já funciona é sobrescrito até o último ter passado por
    download + compile(). Backup individual de cada arquivo trocado
    (<nome>.bak), restaurável por rollback_package_if_pending(). NÃO
    reinicia - quem decide reiniciar (e persistir o state atualizado) é
    o chamador. Retorna True se a troca foi aplicada."""
    versao = remote.get("versao", "")
    pairs = [("Hardware/accessng/" + name, "/accessng/" + name) for name in _ACCESSNG_FILES]
    pairs += [("Hardware/bibliotecas/" + rel, "/" + rel) for rel in bibliotecas]

    baixados = []
    for repo_path, local_path in pairs:
        tmp = local_path + ".new"
        status, _ = http_request(host, "/access-ng/ota/" + repo_path, port=port,
                                  dest_file=tmp, timeout=20)
        if status != 200 or not compile_check(tmp):
            print("[OTA] Falha ao baixar/validar %s - abortando atualização do pacote" % repo_path)
            for _, local_ok in baixados:
                try:
                    os.remove(local_ok + ".new")
                except OSError:
                    pass
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
        baixados.append((repo_path, local_path))

    for _, local_path in baixados:
        try:
            os.remove(local_path + ".bak")
        except OSError:
            pass
        try:
            os.rename(local_path, local_path + ".bak")
        except OSError:
            pass  # arquivo pode não existir ainda (ex.: biblioteca nova)
        os.rename(local_path + ".new", local_path)

    state["accessng_pending_update"] = True
    state["accessng_previous_version"] = state.get("accessng_version")
    state["accessng_version"] = versao
    print("[OTA] Pacote accessng/ atualizado para", versao)
    return True


def rollback_package_if_pending(state):
    """Chamado por boot.py quando há crash-loop generalizado E há uma
    atualização de PACOTE pendente: restaura o .bak de cada arquivo
    trocado. Só se aplica UMA vez por update pendente - mesmo
    raciocínio de rollback_if_pending() (a versão de main.py)."""
    if not state.get("accessng_pending_update"):
        return False
    restored = False
    for path in _PACKAGE_ROLLBACK_CANDIDATES:
        bak = path + ".bak"
        try:
            os.stat(bak)
        except OSError:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
        os.rename(bak, path)
        restored = True
    state["accessng_pending_update"] = False
    state["accessng_version"] = state.get("accessng_previous_version")
    state["boot_count"] = 0
    return restored


def ensure_dependencies(host, port, bibliotecas):
    """Busca em Hardware/bibliotecas/<nome> qualquer arquivo listado no
    manifesto que ainda não existe localmente (checagem por presença, não
    por versão - bibliotecas raramente mudam, e isto não é o mecanismo de
    update do main.py). Cria diretórios intermediários (ex.: /umqtt/) se
    precisar. Nunca propaga exceção - cada arquivo ausente só significa
    que o degrade-gracioso/retry-limitado já existente em main.py assume."""
    for rel_path in bibliotecas:
        local = "/" + rel_path
        try:
            os.stat(local)
            continue  # já presente, não mexe
        except OSError:
            pass
        try:
            _ensure_dir(local)
            path = "/access-ng/ota/Hardware/bibliotecas/" + rel_path
            status, _ = http_request(host, path, port=port, dest_file=local, timeout=20)
            if status != 200:
                try:
                    os.remove(local)
                except OSError:
                    pass
            else:
                print("[Deps] Baixado %s" % rel_path)
        except Exception as e:
            print("[Deps] Falha ao buscar %s: %s" % (rel_path, e))


def _ensure_dir(file_path):
    parts = file_path.split("/")[1:-1]
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            os.mkdir(cur)
        except OSError:
            pass  # já existe
