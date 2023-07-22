/**
   PostHTTPClient.ino

    Created on: 21.11.2016

*/

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>

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

const int LDR_PIN = A0; 
int lightIntensity;
const int BUTTON_PIN = 4;
const int GREEN = 12;
const int RED = 15;
bool anterior;



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

  pinMode(BUTTON_PIN, INPUT_PULLUP);  // Initialize button pin with built-in pullup.
  pinMode(GREEN, OUTPUT);
  pinMode(RED, OUTPUT);
  anterior = isOpened();
}

void loop() {
  // wait for WiFi connection
  if ((WiFi.status() == WL_CONNECTED)) {

    WiFiClient client;
    HTTPClient http;
    int btn_Status = HIGH;
    bool atual = isOpened();
    btn_Status = digitalRead (BUTTON_PIN); 
    if (anterior != atual || btn_Status == LOW) {
      Serial.print("Mudanca de estado\n");
      Serial.print("[HTTP] begin...\n");
      // configure traged server and url
      http.begin(client, "http://" SERVER_IP "/access-ng/log/");  // HTTP
      http.addHeader("Content-Type", "application/json");

      Serial.print("[HTTP] POST...\n");
      // start connection and send HTTP header and body
      //lightIntensity = analogRead(LDR_PIN);
      String valor = atual?"Aberta":"Fechada";
      if (btn_Status == LOW){

      }
      String body = "{\"deviceMac\": \"02:F1:95:7C:C2:EC\",\"topic\": \"Access\", \"type\": \"INFO\",\"message\": \"Porta " + valor +" (" + lightIntensity + ")\"}";
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
          anterior = atual;
        }
      
      } else {
        Serial.printf("[HTTP] POST... failed, error: %s\n", http.errorToString(httpCode).c_str());
      }

      http.end();
    }
  }

}

bool isOpened(){
  lightIntensity = analogRead(LDR_PIN);
  if (lightIntensity < 100){
      digitalWrite(GREEN, HIGH);
      digitalWrite(RED, LOW);
      return false;
  }
  digitalWrite(GREEN, LOW);
  digitalWrite(RED, HIGH);
  return true;
}

