from Model import *
import queue

class Tartaro():
    filaAcionamento = {}

    def autenticarTAG(self, tag:str, senha:str, mac:str):
        caronte:Caronte = self.autenticarCaronte(senha,mac)
        autenticado = caronte.receberTAG(TAG(numero=tag)) if caronte is not None else False
        if autenticado:
            for c in caronte.ambiente.cerberoses:
                self.acionarCerberos(c.mac)
        return autenticado

    def autenticarCaronte(self, chave:str, mac:str) -> Caronte:
        return db.query(Caronte).filter(Caronte.mac == mac and Caronte.chave == chave).first()
    
    def acionarCerberos(self, mac:str):
        if mac not in self.filaAcionamento:
            self.filaAcionamento[mac] = queue.Queue()
        return self.filaAcionamento[mac].put(True)
    
    def verificarAcionamento(self, mac:str)-> bool:
        if mac in self.filaAcionamento and not self.filaAcionamento[mac].empty():
            return self.filaAcionamento[mac].get()
        return False

    
        