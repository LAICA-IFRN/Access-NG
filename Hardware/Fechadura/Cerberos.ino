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

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);     // Initialize the LED_BUILTIN pin as an output

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
  coldStart();
}

void loop() {
  // wait for WiFi connection
  if ((WiFi.status() == WL_CONNECTED)) {

    WiFiClient client;
    HTTPClient http;

    Serial.print("Verificando se posso abrir:\n");
    // configure traged server and url
    http.begin(client, "http://" SERVER_IP "/service/enviroments/enviroments/access/");  // HTTP
    http.addHeader("Content-Type", "application/json");

    //Serial.print("[HTTP] POST...\n");
    // start connection and send HTTP header and body
    //lightIntensity = analogRead(LDR_PIN);
    String body = "{\"mac\": \"" + WiFi.macAddress() + "\"}";
    int httpCode = http.POST(body);
    //Serial.println(body);

    // httpCode will be negative on error
    if (httpCode > 0) {
      // HTTP header has been send and Server response header has been handled
      //Serial.printf("[HTTP] POST... code: %d\n", httpCode);

      // file found at server
      if (httpCode == HTTP_CODE_OK ||  httpCode == HTTP_CODE_CREATED) {
        const String& payload = http.getString();
        //Serial.println("received payload:\n<<");
        //Serial.println(payload);
        //Serial.println(">>");
        StaticJsonDocument<200> doc;
        DeserializationError error = deserializeJson(doc, payload.c_str());
           if (error) {
            Serial.print(F("deserializeJson() failed: "));
            Serial.println(error.f_str());
            return;
          }
        bool response = doc["Allow"];
        if (response == true) {
          Serial.println("pode entrar");
          acionarLED();
        }
      }

    } else {
      Serial.printf("[HTTP] POST... failed, error: %s\n", http.errorToString(httpCode).c_str());
    }

    http.end();
    delay(1000);

  }
}

void acionarLED(){
  digitalWrite(LED_BUILTIN, LOW);   // Turn the LED on by making the voltage LOW
  delay(1000);                      // Wait for a second
  digitalWrite(LED_BUILTIN, HIGH);  // Turn the LED off by making the voltage HIGH
  delay(2000);                      // Wait for two seconds
}

void coldStart(){
  
    WiFiClient client;
    HTTPClient http;

    Serial.print("[HTTP] begin...\n");
    // configure traged server and url
    http.begin(client, "http://" SERVER_IP "/service/microcontrollers/microcontrollers/esp8266/is-alive/");  // HTTP
    http.addHeader("Content-Type", "application/json");

    Serial.print("[HTTP] POST...\n");
    // start connection and send HTTP header and body
    //lightIntensity = analogRead(LDR_PIN);
    String body = "{\"mac\": \"" + WiFi.macAddress() + "\"}";
    int httpCode = http.POST(body);
    Serial.println(body);

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
      }

    } else {
      Serial.printf("[HTTP] POST... failed, error: %s\n", http.errorToString(httpCode).c_str());
    }
    http.end();
}

