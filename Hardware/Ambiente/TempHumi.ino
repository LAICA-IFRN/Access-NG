/**
   PostHTTPClient.ino

    Created on: 21.11.2016

*/
#include "DHT.h"
#define DHTPIN 4     // Digital pin connected to the DHT sensor

//#define DHTTYPE DHT11   // DHT 11
#define DHTTYPE DHT22   // DHT 22  (AM2302), AM2321
#include <WiFi.h>
#include <HTTPClient.h>

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

DHT dht(DHTPIN, DHTTYPE);

int limiar = 100;
float temp;
float humi;

float soma = 10000;

void setup() {

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
  dht.begin();
}

void loop() {
  // wait for WiFi connection
  if ((WiFi.status() == WL_CONNECTED)) {

    WiFiClient client;
    HTTPClient http;
    readSensor();
    float laplace = (temp*temp)+(humi*humi);
    Serial.print(laplace);
    Serial.print(" Soma: ");
    Serial.println(soma);
    if (!(((soma + limiar) > laplace ) && ((soma - limiar) < laplace))) {
      Serial.print("Mudanca de estado\n");
      Serial.print("[HTTP] begin...\n");
      // configure traged server and url
      http.begin(client, "http://" SERVER_IP "/access-ng/log/");  // HTTP
      http.addHeader("Content-Type", "application/json");

      Serial.print("[HTTP] POST...\n");
      String body = "{\"deviceMac\": \"02:F1:95:7C:C2:EC\",\"topic\": \"Ambiente\", \"type\": \"INFO\",\"message\": \"Temperatura = ";
      body += String(temp,2);
      body += F(", Umidade = ");
      body += String(humi,2);
      body += F("\"}");
      int httpCode = http.POST(body);

      // httpCode will be negative on error
      if (httpCode > 0) {
        // HTTP header has been send and Server response header has been handled
        Serial.printf("[HTTP] POST... code: %d\n", httpCode);

        // file found at server
        if (httpCode == HTTP_CODE_OK ||  httpCode == HTTP_CODE_CREATED) {
          const String& payload = http.getString();
          Serial.println("received payload:\n<<");
          Serial.println(payload);
          Serial.println(">>");
          soma = laplace;
        }
      
      } else {
        Serial.printf("[HTTP] POST... failed, error: %s\n", http.errorToString(httpCode).c_str());
      }

      http.end();
    }
  }

}

void readSensor() {
  delay(5000);
  // Reading temperature or humidity takes about 250 milliseconds!
  // Sensor readings may also be up to 2 seconds 'old' (its a very slow sensor)
  humi = dht.readHumidity();
  // Read temperature as Celsius (the default)
  temp = dht.readTemperature();
  Serial.print("Lido: Temperatura - ");
  Serial.print(temp);
  Serial.print(" Humidade - ");
  Serial.println(humi);
  // Check if any reads failed and exit early (to try again).
  if (isnan(humi) || isnan(temp)) {
    Serial.println(F("Failed to read from DHT sensor!"));
    return;
  }
}
