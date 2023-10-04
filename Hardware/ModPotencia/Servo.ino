/* Sweep
 by BARRAGAN <http://barraganstudio.com>
 This example code is in the public domain.

 modified 8 Nov 2013
 by Scott Fitzgerald
 https://www.arduino.cc/en/Tutorial/LibraryExamples/Sweep
*/

#include <Servo.h>

Servo myservo;  // create servo object to control a servo
// twelve servo objects can be created on most boards

int pos = 0;    // variable to store the servo position
int but;

void setup() {
  pinMode(8, INPUT_PULLUP);
  myservo.attach(9);  // attaches the servo on pin 9 to the servo object
  Serial.begin(115200);
  Serial.println("Inicializando");
  fechar();
}

void loop() {
but = digitalRead(8);
delay(100);
if (but == 0) {
  abrir();
  delay(10000);
  fechar();
}

}

void abrir() {
    myservo.write(90);              // tell servo to go to position in variable 'pos'
}

void fechar() {
    myservo.write(0);              // tell servo to go to position in vari
}
