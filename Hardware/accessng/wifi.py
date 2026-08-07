"""Conexão Wi-Fi compartilhada pelos 4 firmwares MQTT.

try_connect_once()/_reset_radio() são a mesma lógica que já existia em
cada firmware (connect_wifi()/_wifi_reset_radio()), só desglobalizada.
connect_bounded() é novo - usado só por boot.py, que precisa desistir e
cair em modo recovery em vez de tentar para sempre.
"""

import network
import time
import os


def _default_needs_radio_reset():
    """O driver WiFi do ESP32-C3 pode ficar preso num estado interno
    inconsistente depois de uma tentativa de conexão que falha ou expira
    (toda chamada seguinte de connect() passa a levantar
    OSError("Wifi Internal State Error"), mesmo com rede/senha corretas -
    visto em campo). Confirmado que esse workaround só é necessário no
    ESP32-C3 (CaronteESP32C3.py/CerberosESP32C3.py) - ausente no ESP32
    "enxuto" e no RP2040/BitDogLab. Detecção automática via
    os.uname().machine; chamadores podem sobrescrever explicitamente."""
    try:
        return "C3" in os.uname().machine
    except Exception:
        return False


def try_connect_once(ssid, password, timeout_s=15, needs_radio_reset=None):
    if needs_radio_reset is None:
        needs_radio_reset = _default_needs_radio_reset()
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    time.sleep_ms(100)  # assentamento do rádio (evita erro no cold boot do C3)
    if wlan.isconnected():
        print("[WiFi] IP: %s" % wlan.ifconfig()[0])
        return True
    try:
        wlan.connect(ssid, password)
    except OSError as e:
        print("[WiFi] Erro ao conectar: %s" % e)
        if needs_radio_reset:
            _reset_radio(wlan)
        return False

    deadline = time.ticks_add(time.ticks_ms(), int(timeout_s * 1000))
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if wlan.isconnected():
            print("[WiFi] IP: %s" % wlan.ifconfig()[0])
            return True
        time.sleep_ms(500)

    print("[WiFi] Falha")
    if needs_radio_reset:
        _reset_radio(wlan)
    return False


def _reset_radio(wlan):
    try:
        wlan.active(False)
        time.sleep_ms(200)
        wlan.active(True)
        time.sleep_ms(100)
    except OSError as e:
        print("[WiFi] Erro ao reiniciar rádio: %s" % e)


def connect_bounded(ssid, password, attempts=3, timeout_s=15, needs_radio_reset=None):
    """Usado por boot.py: tentativas limitadas (até ~45s com 3 tentativas
    de 15s) para decidir recovery - nunca bloqueia para sempre, ao
    contrário do laço de reconexão operacional em main.py."""
    for _ in range(attempts):
        if try_connect_once(ssid, password, timeout_s, needs_radio_reset):
            return True
    return False


def mac_suffix(n=4):
    import ubinascii
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    return ubinascii.hexlify(wlan.config("mac")).decode().upper()[-n:]
