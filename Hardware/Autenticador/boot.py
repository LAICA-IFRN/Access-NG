# Caronte ESP32-C3 - supervisor de boot
#
# Mantido pequeno de propósito: decide RECOVERY (AP+portal) vs seguir
# para main.py. Quanto menos lógica aqui, menor o risco de uma
# atualização (ou um bug) deixar o dispositivo irrecuperável - ver
# accessng/ para a lógica de fato (config/wifi/recovery/provisioning/ota).

DEVICE_TYPE = "Caronte"

import machine
import json
import os

# 0) Watchdog de hardware ARMADO ANTES de qualquer import arriscado -
#    accessng/ agora pode ser atualizado remotamente
#    (accessng.ota.apply_package_update), então um pacote corrompido não
#    pode travar o boot pra sempre sem que o watchdog force um reset.
#    Criado aqui de forma autocontida (sem depender de
#    accessng.watchdog, que é exatamente o que pode estar quebrado) e
#    repassado pra accessng.watchdog.arm() reaproveitar assim que (se) o
#    import abaixo funcionar - a maioria dos ports do MicroPython não
#    permite criar um segundo machine.WDT().
try:
    _early_wdt = machine.WDT(timeout=8000)
except Exception:
    _early_wdt = None


def _package_self_repair():
    """accessng/ não importou - o pacote pode ter sido corrompido por
    uma atualização ruim. Não pode depender de NADA em accessng (é
    exatamente o que está quebrado) - lê boot_state.json na mão e
    restaura os .bak de cada arquivo trocado (mesma lista de
    accessng.ota._PACKAGE_ROLLBACK_CANDIDATES, duplicada aqui pelo
    mesmo motivo). Nunca propaga exceção - se nem isso resolver, o
    watchdog (se armou) força um reset em ~8s e o próximo boot tenta de
    novo."""
    try:
        with open("boot_state.json") as f:
            state = json.load(f)
    except Exception:
        return
    if not state.get("accessng_pending_update"):
        return
    candidates = (
        "/accessng/__init__.py", "/accessng/config.py", "/accessng/wifi.py",
        "/accessng/recovery.py", "/accessng/provisioning.py", "/accessng/ota.py",
        "/accessng/watchdog.py",
        "/umqtt/__init__.py", "/umqtt/simple.py", "/umqtt/robust.py",
        "/sh1106.py", "/ssd1306.py",
    )
    restored = False
    for path in candidates:
        bak = path + ".bak"
        try:
            os.stat(bak)
        except OSError:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            os.rename(bak, path)
            restored = True
        except OSError:
            pass
    if not restored:
        return
    state["accessng_pending_update"] = False
    try:
        with open("boot_state.json.tmp", "w") as f:
            json.dump(state, f)
        os.rename("boot_state.json.tmp", "boot_state.json")
    except Exception:
        pass
    print("[Boot] Pacote accessng/ restaurado - reiniciando")
    machine.reset()


try:
    from device_defaults import DEFAULTS, SENSITIVE_KEYS
    from accessng import config, wifi, recovery, ota, watchdog
except Exception as e:
    print("[Boot] Falha ao importar accessng/device_defaults:", e)
    _package_self_repair()
    # Sem update de pacote pendente pra restaurar (ou a restauração
    # falhou) - não há como prosseguir de forma útil. Se o watchdog
    # armou, ele força um reset em ~8s e o próximo boot tenta de novo;
    # senão, propaga o erro original (mesmo comportamento de sempre de
    # um import não tratado em boot.py).
    raise

# Reaproveita o watchdog já armado acima - ver o comentário em
# accessng/watchdog.py sobre por que não dá pra criar um segundo.
watchdog.arm(existing=_early_wdt)

# 1) Marca o boot ANTES de qualquer coisa arriscada: se algo adiante
#    travar, o próximo boot já enxerga este boot como "não confirmado
#    saudável" (boot_count incrementa em todo boot, físico ou soft).
state = config.load_state()
state["boot_count"] = state.get("boot_count", 0) + 1
state["last_boot_ok"] = False
config.save_state(state)

cfg_file, cfg_ok = config.load()
mac_suffix = wifi.mac_suffix()

if not cfg_ok:
    recovery.enter(DEVICE_TYPE, mac_suffix, DEFAULTS, SENSITIVE_KEYS,
                    "config ausente/invalida")
elif recovery.is_crash_looping(state):
    # Pacote accessng/ primeiro: se a atualização pendente for a dele,
    # restaurar main.py sozinho (rollback_if_pending) não resolveria -
    # o problema não está lá.
    if ota.rollback_package_if_pending(state):
        config.save_state(state)
        machine.reset()
    elif ota.rollback_if_pending(state):
        config.save_state(state)
        machine.reset()
    else:
        recovery.enter(DEVICE_TYPE, mac_suffix, DEFAULTS, SENSITIVE_KEYS,
                        "boot loop")
else:
    ssid = config.get(cfg_file, DEFAULTS, "WIFI_SSID")
    senha = config.get(cfg_file, DEFAULTS, "WIFI_PASS")
    if not wifi.connect_bounded(ssid, senha, attempts=3):
        recovery.enter(DEVICE_TYPE, mac_suffix, DEFAULTS, SENSITIVE_KEYS,
                        "falha de wifi")

# Nenhuma condição acima disparou recovery.enter() (que nunca retorna) -
# segue normalmente para main.py.
