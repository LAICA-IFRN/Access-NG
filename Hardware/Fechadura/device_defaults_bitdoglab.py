# Dados puros do Cerberos+Caronte BitDogLab V6 (Pico W) - sem imports de
# machine/network/etc. Usado tanto por boot_bitdoglab.py (portal de
# provisionamento) quanto por main_bitdoglab.py (cfg()/fallback), sem que
# nenhum dos dois precise importar o outro.

DEFAULTS = {
    "WIFI_SSID"           : "wIFRN-IoT",
    "WIFI_PASS"           : "deviceiotifrn",
    "MQTT_BROKER"         : "broker.exemplo.com",
    "MQTT_PORT"           : 1883,
    "MQTT_USER"           : "",
    "MQTT_PASS"           : "",
    "MQTT_TLS"            : False,
    "DEVICE_KEY"          : "chave-do-dispositivo",
    "HEARTBEAT_INTERVAL"  : 25,
    "BUTTON_PIN"          : 5,
    "BUTTON_DEBOUNCE_MS"  : 50,
    "BUTTON_TAG"          : "btn_local",
    "LED_RED_PIN"         : 13,
    "LED_GREEN_PIN"       : 11,
    "LED_BLUE_PIN"        : 12,
    "RELAY_PIN"           : 15,
    "RELAY_ACTIVE_MS"     : 2000,
    "OLED_ENABLED"        : True,
    "OLED_SCL_PIN"        : 15,
    "OLED_SDA_PIN"        : 14,
    "OLED_WIDTH"          : 128,
    "OLED_HEIGHT"         : 64,
    "OLED_ADDR"           : 0x3C,
    "OTA_ENABLED"         : True,
    "OTA_CHECK_INTERVAL"  : 3600,
}

# Nunca reportados por valor via MQTT nem pré-preenchidos no formulário de
# provisionamento (só é possível sobrescrever, não ler).
SENSITIVE_KEYS = ("WIFI_PASS", "DEVICE_KEY", "MQTT_PASS")
