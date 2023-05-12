from Model import *

class Tartaro():

    def autenticarTAG(self, tag:str, senha:str, ip:str):
        caronte:Caronte = self.autenticarCaronte(senha,ip)
        return caronte.receberTAG(TAG(numero=tag)) if caronte is not None else False

    def autenticarCaronte(self, senha:str, ip:str) -> Caronte:
        return db.query(Caronte).filter(Caronte.ip == ip and Caronte.senha == senha).first()
    
        