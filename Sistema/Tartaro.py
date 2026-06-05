from Model import *
import queue
import math


def _distancia_metros(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two coordinates in meters."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class Tartaro():
    filaAcionamento = {}

    def autenticarTAG(self, tag: str, senha: str, mac: str):
        caronte: Caronte = self.autenticarCaronte(senha, mac)
        autenticado = caronte.receberTAG(TAG(numero=tag)) if caronte is not None else False
        if autenticado:
            for c in caronte.ambiente.cerberoses:
                self.acionarCerberos(c.mac)
        return autenticado

    def autenticarCaronte(self, chave: str, mac: str) -> Caronte:
        return db.query(Caronte).filter(Caronte.mac == mac, Caronte.chave == chave).first()

    def acionarCerberos(self, mac: str):
        if mac not in self.filaAcionamento:
            self.filaAcionamento[mac] = queue.Queue()
        return self.filaAcionamento[mac].put(True)

    def verificarAcionamento(self, mac: str, timeout: float = 0) -> bool:
        if mac not in self.filaAcionamento:
            self.filaAcionamento[mac] = queue.Queue()
        try:
            return self.filaAcionamento[mac].get(timeout=timeout)
        except queue.Empty:
            return False

    def autenticarWeb(self, matricula: str, pin: str, ambiente_id: int) -> bool:
        """Authenticate a user via browser (web Caronte) and trigger the ambiente's Cerberoses."""
        ambiente = db.query(Ambiente).filter(Ambiente.id == ambiente_id).first()
        if not ambiente:
            return False
        usuario = db.query(Usuario).filter(
            Usuario.matricula == matricula,
            Usuario.pin == pin
        ).first()
        if not usuario:
            return False
        if usuario in ambiente.frequentadores:
            for c in ambiente.cerberoses:
                self.acionarCerberos(c.mac)
            return True
        return False

    def ambientesProximos(self, lat: float, lon: float) -> list:
        """Return all Ambientes whose geofence contains the given coordinates."""
        proximos = []
        for amb in db.query(Ambiente).all():
            if amb.latitude is None or amb.longitude is None:
                continue
            raio = amb.raio_metros or 50
            if _distancia_metros(lat, lon, amb.latitude, amb.longitude) <= raio:
                proximos.append(amb)
        return proximos
