from Model import *
import datetime
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


def _acesso_valido(usuario_id: int, ambiente_id: int) -> bool:
    """Sem nenhuma linha em ValidadeAcesso pra esse par, o acesso vale
    desde sempre e para sempre - o padrão de hoje, retrocompatível com
    todo frequentador já cadastrado. Com uma linha, cada ponta
    (valido_desde/valido_ate) só é checada se estiver preenchida.
    Comparação sempre em UTC naive - a conversão pro fuso local do
    Tartaro acontece só ao gravar/exibir no painel admin (ver
    ValidadeAcesso em Model.py), nunca aqui."""
    validade = db.query(ValidadeAcesso).filter(
        ValidadeAcesso.usuario_id == usuario_id,
        ValidadeAcesso.ambiente_id == ambiente_id,
    ).first()
    if validade is None:
        return True
    agora = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if validade.valido_desde is not None and agora < validade.valido_desde:
        return False
    if validade.valido_ate is not None and agora > validade.valido_ate:
        return False
    return True


class Tartaro():
    filaAcionamento = {}

    def autenticarTAG(self, tag: str, senha: str, mac: str):
        return self.autenticarTAGDetalhado(tag=tag, senha=senha, mac=mac)['allow']

    def autenticarTAGDetalhado(self, tag: str, senha: str, mac: str):
        caronte_by_mac = db.query(Caronte).filter(Caronte.mac.ilike(mac)).first()
        result = {
            'allow': False,
            'caronte': None,
            'ambiente': None,
            'usuario': None,
            'motivo': None,
        }
        if caronte_by_mac is None:
            result['motivo'] = f'Caronte nao cadastrado para o MAC {mac}'
            return result
        if caronte_by_mac.chave != senha:
            result['motivo'] = f'Chave invalida para o Caronte {mac}'
            return result
        caronte = caronte_by_mac
        result['caronte'] = caronte
        result['ambiente'] = caronte.ambiente

        expirado = False
        for user in caronte.ambiente.frequentadores:
            match = next((t for t in user.tags if t.numero == tag), None)
            # TAG sem nenhum Ambiente associado vale em qualquer um onde o
            # usuário tem acesso (padrão); com Ambiente(s) associado(s),
            # só vale nesses.
            if match and (not match.ambientes or caronte.ambiente in match.ambientes):
                if not _acesso_valido(user.id, caronte.ambiente.id):
                    expirado = True
                    continue
                result['allow'] = True
                result['usuario'] = user
                break

        if result['allow']:
            for c in caronte.ambiente.cerberoses:
                self.acionarCerberos(c.mac)
        elif expirado:
            result['motivo'] = 'Acesso expirado para este Tartaro'
        else:
            result['motivo'] = 'Tag sem permissao para este ambiente'
        return result

    def autenticarCaronte(self, chave: str, mac: str) -> Caronte:
        return db.query(Caronte).filter(Caronte.mac.ilike(mac), Caronte.chave == chave).first()

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
        """Authenticate a user via browser (web Caronte) and trigger the ambiente's Cerberoses.

        Exige três coisas: o Tartaro ter o Caronte web habilitado, o usuário
        ser frequentador do ambiente (mesmo critério do acesso físico por
        TAG) e ter permissão explícita de uso do Caronte web *nesse*
        ambiente (usuarios_web) - concedida à parte pelo admin/gerente."""
        ambiente = db.query(Ambiente).filter(Ambiente.id == ambiente_id).first()
        if not ambiente or not ambiente.web_habilitado:
            return False
        usuario = db.query(Usuario).filter(
            Usuario.matricula == matricula,
            Usuario.pin == pin
        ).first()
        if not usuario:
            return False
        if (usuario in ambiente.frequentadores and usuario in ambiente.usuarios_web
                and _acesso_valido(usuario.id, ambiente.id)):
            for c in ambiente.cerberoses:
                self.acionarCerberos(c.mac)
            return True
        return False

    def ambientesProximos(self, lat: float, lon: float, usuario: "Usuario" = None) -> list:
        """Return Ambientes with Caronte web habilitado whose geofence contains
        the given coordinates. Se um usuario for informado, restringe ainda
        mais aos ambientes onde ele tem permissão de uso do Caronte web -
        evita mostrar no app um ambiente "disponível" que na hora de abrir
        seria negado por falta de permissão."""
        proximos = []
        for amb in db.query(Ambiente).filter(Ambiente.web_habilitado == True).all():
            if amb.latitude is None or amb.longitude is None:
                continue
            if usuario is not None and usuario not in amb.usuarios_web:
                continue
            if usuario is not None and not _acesso_valido(usuario.id, amb.id):
                continue
            raio = amb.raio_metros or 50
            if _distancia_metros(lat, lon, amb.latitude, amb.longitude) <= raio:
                proximos.append(amb)
        return proximos
