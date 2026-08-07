# umqtt.robust - vendorizado no Access-NG a partir da biblioteca pública
# micropython-lib (MIT license, projeto MicroPython). Ver o mesmo aviso
# de simple.py: diffe contra o upstream antes de instalar em produção.
#
# Nota do Access-NG: os firmwares CaronteESP32C3.py/CerberosESP32.py
# preferem umqtt.simple (propaga OSError de verdade, deixando o `except
# OSError` do main() fazer a recuperação completa); Cerberos_
# BitDogLab_MQTT.py prefere robust (validado em campo pra essa placa). O
# reconnect() automático aqui NÃO reinscreve em tópicos - main.py precisa
# sempre re-chamar subscribe() após qualquer reconexão bem-sucedida.

import time

from . import simple


class MQTTClient(simple.MQTTClient):
    DEBUG = False

    def delay(self, i):
        time.sleep(i)

    def log(self, in_reconnect, e):
        if self.DEBUG:
            if in_reconnect:
                print("mqtt reconnect: %r" % e)
            else:
                print("mqtt error: %r" % e)

    def reconnect(self):
        i = 0
        while True:
            try:
                return super().connect(False)
            except OSError as e:
                self.log(True, e)
                i += 1
                self.delay(min(i, 10))

    def publish(self, topic, msg, retain=False, qos=0):
        while True:
            try:
                return super().publish(topic, msg, retain, qos)
            except OSError as e:
                self.log(False, e)
                self.reconnect()

    def wait_msg(self):
        while True:
            try:
                return super().wait_msg()
            except OSError as e:
                self.log(False, e)
                self.reconnect()

    def check_msg(self):
        while True:
            try:
                return super().check_msg()
            except OSError as e:
                self.log(False, e)
                self.reconnect()
