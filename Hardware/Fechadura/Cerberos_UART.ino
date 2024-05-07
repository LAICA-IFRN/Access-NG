/**
   PostHTTPClient.ino

    Created on: 21.11.2016

*/

//#include <ESP8266WiFi.h>
#include <MFRC522.h> //biblioteca responsável pela comunicação com o módulo RFID-RC522
#include <SPI.h> 

#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <WiFi.h>


#define TX_PIN 17
#define RX_PIN 16

#define SERVER_IP "192.168.0.100:9001"

#ifndef STASSID
#define STASSID "Laica_Serido"
#define STAPSK "L41C4_S3R1D0"
#endif

//Relé
int acionamento = 15;

//Botão
int BUTTON_PIN = 13;
int buttonValue;

void setup() {
  pinMode(BUTTON_PIN, INPUT);
  digitalWrite(acionamento, LOW);

  buttonValue = digitalRead(BUTTON_PIN);

  Serial.begin(115200);
  Serial2.begin(115200, SERIAL_8N1, RX_PIN, TX_PIN);

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

void array_to_string(byte array[], unsigned int len, char buffer[])
{
    for (unsigned int i = 0; i < len; i++)
    {
        byte nib1 = (array[i] >> 4) & 0x0F;
        byte nib2 = (array[i] >> 0) & 0x0F;
        buffer[i*2+0] = nib1  < 0xA ? '0' + nib1  : 'A' + nib1  - 0xA;
        buffer[i*2+1] = nib2  < 0xA ? '0' + nib2  : 'A' + nib2  - 0xA;
    }
    buffer[len*2] = '\0';
}

String tag;

void loop() {
  // wait for WiFi connection
  if ((WiFi.status() == WL_CONNECTED)) {

    if (buttonValue != digitalRead(BUTTON_PIN))
    {
      buttonValue = digitalRead(BUTTON_PIN);
      acionarRele();
    }

    if (Serial2.available()) {
      // Lê a mensagem recebida do primeiro esp e armazena na variável "tag". 
      tag = Serial2.readString();
    }

    WiFiClient client;
    HTTPClient http;

    //Comunica com a api para permitir ou não o acesso.
    Serial.print("Verificando se posso abrir:\n");
    // configure traged server and url
    http.begin(client, "http://" SERVER_IP "/caronte/autenticarTag");  // HTTP
    http.addHeader("Content-Type", "application/json");

    //Serial.print("Endereço MAC: ");
    //Serial.println(WiFi.macAddress());
  
    //Serial.print("[HTTP] POST...\n");
    // start connection and send HTTP header and body
    //lightIntensity = analogRead(LDR_PIN);
    String body = "{\"tag\":\""+ tag.substring(0, 8) + "\", \"mac\": \"24:6F:28:17:CA:90\", \"chave\": \"123\"}";
    Serial.println(body);
    int httpCode = http.POST(body);
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
           if (error) { //No caso de erro, retorna a mensagem ERROR para o primeiro esp.
            Serial.print(F("deserializeJson() failed: "));
            Serial.println(error.f_str());
            Serial2.print("ERROR");
            return;
          }
        //String response = http.getString();
        const char* response = doc["Allow"];
        //Serial.println(payload);
        Serial.println(response);
        if ((strcmp(response, "True") == 0)) { //Se for encontrado a tag/uid do cartão e a api permitir a entrada, é retornado para o primeiro esp a mensagem "ACCEPT".
          tag = "";
          Serial2.print("ACCEPT");
          acionarRele();
        }else if(strcmp(response, "False") == 0 && tag != "") { //Se retornar falso e a tag não for vazia, ele retorna "DENIED" para o primeiro esp.
          tag = "";
          Serial2.print("DENIED");
          delay(5000);
        }
      }

    } else {
      Serial.printf("[HTTP] POST... failed, error: %s\n", http.errorToString(httpCode).c_str());
    }

    http.end();
    delay(1000);
  }
}

void acionarRele(){
  digitalWrite(acionamento, HIGH);
  delay(5000);
  digitalWrite(acionamento, LOW);
  //delay(2000);
}

void coldStart(){
  
    WiFiClient client;
    HTTPClient http;

    Serial.print("[HTTP] begin...\n");
    // configure traged server and url
    http.begin(client, "http://" SERVER_IP "/access-control/gateway/devices/microcontrollers/cold-start");  // HTTP
    http.addHeader("Content-Type", "application/json");

    Serial.print("[HTTP] POST...\n");
    // start connection and send HTTP header and body
    //lightIntensity = analogRead(LDR_PIN);
    String body = "{\"id\": \"5\"}";
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