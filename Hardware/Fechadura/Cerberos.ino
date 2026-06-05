/**
   PostHTTPClient.ino

    Created on: 21.11.2016

*/

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ArduinoJson.h>

/* this can be run with an emulated server on host:
        cd esp8266-core-root-dir
        cd tests/host
        make ../../libraries/ESP8266WebServer/examples/PostServer/PostServer
        bin/PostServer/PostServer
   then put your PC's IP address in SERVER_IP below, port 9080 (instead of default 80):
*/
//#define SERVER_IP "10.0.1.7:9080" // PC address with emulation on host
#define SERVER_IP "laica.ifrn.edu.br"

#ifndef STASSID
#define STASSID "wIFRN-IoT"
#define STAPSK "deviceiotifrn"
#endif

const char* COLDSTART_ENDPOINT = "/device/coldstart";
const char* HEARTBEAT_ENDPOINT = "/device/heartbeat";
const char* COMMAND_ENDPOINT = "/device/command";
const unsigned long HEARTBEAT_INTERVAL_MS = 30000;
const unsigned long COMMAND_POLL_WAIT_SECONDS = 20;
const uint16_t API_TIMEOUT_MS = 10000;
const uint16_t COMMAND_TIMEOUT_MS = 30000;

int acionamento = 13;
unsigned long lastHeartbeat = 0;

void setup() {

  pinMode(acionamento, OUTPUT);     // Initialize the LED_BUILTIN pin as an output
  digitalWrite(acionamento, HIGH);

  Serial.begin(115200);

  Serial.println();
  Serial.println();
  Serial.println();

  WiFi.begin(STASSID, STAPSK);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("");
  Serial.print("Connected! IP address: ");
  Serial.println(WiFi.localIP());
  Serial.print("MAC address: ");
  Serial.println(WiFi.macAddress());
  coldStart();
}

void loop() {
  // wait for WiFi connection
  if ((WiFi.status() == WL_CONNECTED)) {
    unsigned long now = millis();
    if (now - lastHeartbeat > HEARTBEAT_INTERVAL_MS) {
      heartbeat();
      lastHeartbeat = now;
    }

    pollCommand();
  } else {
    reconnectWiFi();
  }
}

String deviceBody() {
  return "{\"mac\":\"" + WiFi.macAddress() + "\"}";
}

int postJson(const char* endpoint, const String& body, uint16_t timeoutMs, String& payload) {
  WiFiClient client;
  HTTPClient http;
  String url = String("http://") + SERVER_IP + endpoint;

  http.setTimeout(timeoutMs);
  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");

  Serial.print("[HTTP] POST ");
  Serial.println(endpoint);
  Serial.println(body);

  int httpCode = http.POST(body);
  if (httpCode > 0) {
    payload = http.getString();
    Serial.printf("[HTTP] code: %d\n", httpCode);
    Serial.println(payload);
  } else {
    Serial.printf("[HTTP] failed: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
  return httpCode;
}

void reconnectWiFi() {
  Serial.println("[WiFi] Reconectando...");
  WiFi.disconnect();
  WiFi.begin(STASSID, STAPSK);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WiFi] Reconectado. IP: ");
    Serial.println(WiFi.localIP());
    coldStart();
  }
}

void acionarRele(){
  digitalWrite(acionamento, LOW);   // Turn the LED on by making the voltage LOW
  delay(500);                      // Wait for a second
  digitalWrite(acionamento, HIGH);  // Turn the LED off by making the voltage HIGH
  //delay(2000);                      // Wait for two seconds
}

void coldStart(){
  String payload;
  postJson(COLDSTART_ENDPOINT, deviceBody(), API_TIMEOUT_MS, payload);
}

void heartbeat(){
  String payload;
  postJson(HEARTBEAT_ENDPOINT, deviceBody(), API_TIMEOUT_MS, payload);
}

void pollCommand(){
  Serial.println("[Command] Aguardando comando do servidor...");

  String body = "{\"mac\":\"" + WiFi.macAddress() + "\",\"wait\":" + String(COMMAND_POLL_WAIT_SECONDS) + "}";
  String payload;
  int httpCode = postJson(COMMAND_ENDPOINT, body, COMMAND_TIMEOUT_MS, payload);

  if (httpCode != HTTP_CODE_OK) {
    return;
  }

  StaticJsonDocument<200> doc;
  DeserializationError error = deserializeJson(doc, payload.c_str());
  if (error) {
    Serial.print(F("[Command] deserializeJson() failed: "));
    Serial.println(error.f_str());
    return;
  }

  const char* command = doc["command"] | "";
  if (strcmp(command, "unlock") == 0 || strcmp(command, "open") == 0 || strcmp(command, "abrir") == 0) {
    Serial.println("[Command] Comando de abertura recebido!");
    acionarRele();
  } else {
    Serial.println("[Command] Nenhum comando pendente");
  }
}
