# Cerberos ESP32-C3 (FECHO) - supervisor de boot
#
# Mantido pequeno de propósito: decide RECOVERY (AP+portal) vs seguir
# para main.py. Quanto menos lógica aqui, menor o risco de uma
# atualização (ou um bug) deixar o dispositivo irrecuperável - ver
# accessng/ para a lógica de fato (config/wifi/recovery/provisioning/ota).
#
# Instalado no dispositivo como boot.py (o sufixo _esp32c3 é só pra
# conviver com as outras variantes de Cerberos dentro deste diretório).

DEVICE_TYPE = "FECHO"

import machine
from device_defaults import DEFAULTS, SENSITIVE_KEYS
from accessng import config, wifi, recovery, ota, watchdog

# 0) Watchdog de hardware - ver accessng/watchdog.py.
watchdog.arm()

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
    if ota.rollback_if_pending(state):
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
