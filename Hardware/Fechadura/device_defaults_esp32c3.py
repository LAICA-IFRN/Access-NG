# Dados puros do Cerberos ESP32-C3 (FECHO) - sem imports de machine/
# network/etc. Usado tanto por boot_esp32c3.py (portal de
# provisionamento) quanto por main_esp32c3.py (cfg()/fallback), sem que
# nenhum dos dois precise importar o outro.

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
    "LED_VM_PIN"         : 1,
    "LED_VD1_PIN"        : 4,
    "LED_VD2_PIN"        : 3,
    "LED_VD3_PIN"        : 2,
    "RELAY_PIN"          : 6,
    "RELAY_ACTIVE_MS"    : 2000,
    "RELAY_COOLDOWN_MS"  : 3000,
    "UART_ENABLED"       : False,
    "UART_ID"            : 1,
    "UART_TX_PIN"        : 21,
    "UART_RX_PIN"        : 20,
    "UART_BAUDRATE"      : 9600,
    "OLED_ENABLED"       : True,
    "OLED_SCL_PIN"       : 7,
    "OLED_SDA_PIN"       : 8,
    "OLED_WIDTH"         : 128,
    "OLED_HEIGHT"        : 64,
    "OLED_ADDR"          : 0x3C,
    "OTA_ENABLED"        : True,
    "OTA_CHECK_INTERVAL" : 3600,
}

# Nunca reportados por valor via MQTT nem pré-preenchidos no formulário de
# provisionamento (só é possível sobrescrever, não ler).
SENSITIVE_KEYS = ("WIFI_PASS", "DEVICE_KEY", "MQTT_PASS")
