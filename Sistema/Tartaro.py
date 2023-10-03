from Model import *
import queue

class Tartaro():
    filaAcionamento = {}

    def autenticarTAG(self, tag:str, senha:str, mac:str):
        caronte:Caronte = self.autenticarCaronte(senha,mac)
        return caronte.receberTAG(TAG(numero=tag)) if caronte is not None else False

    def autenticarCaronte(self, senha:str, mac:str) -> Caronte:
        return db.query(Caronte).filter(Caronte.ip == mac and Caronte.senha == senha).first()
    
    def acionarCerberos(self, mac:str):
        if mac not in self.filaAcionamento:
            self.filaAcionamento[mac] = queue.Queue()
        return self.filaAcionamento[mac].put(True)
    
    def verificarAcionamento(self, mac:str)-> bool:
        if mac in self.filaAcionamento and not self.filaAcionamento[mac].empty():
            return self.filaAcionamento[mac].get()
        return False

    
        