# Dados puros do Cerberos ESP32 (enxuto) - sem imports de machine/network/
# etc. Usado tanto por boot_esp32.py (portal de provisionamento) quanto por
# main_esp32.py (cfg()/fallback), sem que nenhum dos dois precise importar
# o outro.

DEFAULTS = {
    "WIFI_SSID"          : "wIFRN-IoT",
    "WIFI_PASS"          : "deviceiotifrn",
    "MQTT_BROKER"        : "broker.exemplo.com",
    "MQTT_PORT"          : 1883,
    "MQTT_USER"          : "",
    "MQTT_PASS"          : "",
    "MQTT_TLS"           : False,
    "DEVICE_KEY"         : "chave-do-dispositivo",
    "HEARTBEAT_INTERVAL" : 25,
    "LED_LINK_PIN"       : 12,
    "LED_STATUS_PIN"     : 13,
    "RELAY_PIN"          : 15,
    "RELAY_ACTIVE_MS"    : 2000,
    "INPUT_ENABLED"      : False,
    "INPUT_PINS"         : [26, 34],
    "INPUT_DEBOUNCE_MS"  : 200,
    "OTA_ENABLED"        : True,
    "OTA_CHECK_INTERVAL" : 3600,
}

# Nunca reportados por valor via MQTT nem pré-preenchidos no formulário de
# provisionamento (só é possível sobrescrever, não ler).
SENSITIVE_KEYS = ("WIFI_PASS", "DEVICE_KEY", "MQTT_PASS")
