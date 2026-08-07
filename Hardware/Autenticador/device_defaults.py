# Dados puros do Caronte ESP32-C3 - sem imports de machine/network/etc.
# Usado tanto por boot.py (portal de provisionamento) quanto por main.py
# (cfg()/fallback), sem que nenhum dos dois precise importar o outro.

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
    "WG_D0_PIN"          : 5,
    "WG_D1_PIN"          : 7,
    "BUZZER_PIN"         : 6,
    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "WG_TIMEOUT_MS"      : 25,
    "AUTH_TIMEOUT_S"     : 5,
    "UART_ENABLED"       : False,
    "UART_ID"            : 1,
    "UART_TX_PIN"        : 21,
    "UART_RX_PIN"        : 20,
    "UART_BAUDRATE"      : 9600,
    "UART_KEEPALIVE_S"   : 5,
    "OTA_ENABLED"        : True,
    "OTA_CHECK_INTERVAL" : 3600,
}

# Nunca reportados por valor via MQTT nem pré-preenchidos no formulário de
# provisionamento (só é possível sobrescrever, não ler).
SENSITIVE_KEYS = ("WIFI_PASS", "DEVICE_KEY", "MQTT_PASS")
