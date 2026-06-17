#include <MFRC522.h> //biblioteca responsável pela comunicação com o módulo RFID-RC522
#include <SPI.h> //biblioteca para comunicação do barramento SPI

#define SS_PIN    21
#define RST_PIN   22

#define TX_PIN 17
#define RX_PIN 16

//esse objeto 'chave' é utilizado para autenticação
MFRC522::MIFARE_Key key;
//código de status de retorno da autenticação
MFRC522::StatusCode status;

// Definicoes pino modulo RC522
MFRC522 mfrc522(SS_PIN, RST_PIN);

//Led's
int ledVerde = 5;
int ledVermelho = 4;
int ledAmarelo = 2;

void setup() {
  pinMode(ledVermelho, OUTPUT);
  pinMode(ledAmarelo, OUTPUT);
  pinMode(ledVerde, OUTPUT);

  digitalWrite(ledAmarelo, HIGH);

  Serial.begin(115200);
  Serial2.begin(115200, SERIAL_8N1, RX_PIN, TX_PIN);

  Serial.println();
  Serial.println();
  Serial.println();

  SPI.begin();

  mfrc522.PCD_Init();
  
  // Mensagens iniciais no serial monitor
  Serial.println("Aproxime o seu cartao do leitor...");
  Serial.println();

}

//Função que transforma um array em uma string, utilizada para "traduzir" a tag/uid do cartão.
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

void loop() {

  //Verifica se tem uma nova mensagem vindo do segundo esp disponível.
  if (Serial2.available()) {
    String receivedMessage = Serial2.readString();
    
    Serial.println("Mensagem Recebida no Dispositivo 1: " + String(receivedMessage));
    if (receivedMessage == "ACCEPT") { // Quando o retorno for a mensagem "ACCEPT" ele irá ligar o led verde.
      digitalWrite(ledVerde, HIGH);
      digitalWrite(ledAmarelo, LOW);
      delay(5000);
      digitalWrite(ledAmarelo, HIGH);
      digitalWrite(ledVerde, LOW);
    }

    if (receivedMessage == "DENIED") { //Quando o retorno for a mensagem "DENIED" ele irá ligar o led vermelho.
      digitalWrite(ledVermelho, HIGH);
      digitalWrite(ledAmarelo, LOW);
      delay(5000);
      digitalWrite(ledAmarelo, HIGH);
      digitalWrite(ledVermelho, LOW);
    }

    if(receivedMessage == "ERROR") { //Quando o retorno for a mensagem "ERROR" ele irá piscar o led vermelho.
      digitalWrite(ledVermelho, HIGH);
      digitalWrite(ledAmarelo, LOW);
      delay(300);
      digitalWrite(ledVermelho, LOW);
      delay(300);
      digitalWrite(ledVermelho, HIGH);
      delay(300);
      digitalWrite(ledVermelho, LOW);
      delay(300);
      digitalWrite(ledVermelho, HIGH);
      delay(3000);
      digitalWrite(ledAmarelo, HIGH);
      digitalWrite(ledVermelho, LOW);
    }
  }

  // Aguarda a aproximacao do cartao
  if ( ! mfrc522.PICC_IsNewCardPresent() ) 
  {
    return;
  }
  // Seleciona um dos cartoes
  if ( ! mfrc522.PICC_ReadCardSerial()) 
  {
    return;
  }

  //Coloca a tag coletada do cartão no char "str".
  char str[32] = "";
  array_to_string(mfrc522.uid.uidByte, 4, str);

  //Envia para o segundo esp a tag ("str").
  Serial.println(str);
  Serial2.print(String(str));
  delay(1000);
}
