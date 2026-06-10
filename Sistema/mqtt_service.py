"""
Serviço MQTT de background — Access-NG / Tartaro

Estrutura de tópicos (prefixo: access-ng/):
  coldstart/{mac}                   → dispositivo → servidor (boot)
  heartbeat/{mac}                   → dispositivo → servidor (periódico)
  {amb_id}/caronte/{mac}/tag        → Caronte RFID → servidor (autenticar TAG)
  {amb_id}/caronte/{mac}/result     → servidor → Caronte (resultado da auth)
  {amb_id}/cerberos/{mac}/command   → servidor → Cerberos (comando de abertura)
  {amb_id}/cerberos/{mac}/status    → Cerberos → servidor (atualização de status)

O MAC nos tópicos usa '-' no lugar de ':' para compatibilidade com brokers
que tratam ':' como separador especial. O servidor aceita ambos ao receber.
"""
import datetime
import json
import threading

PREFIX = 'access-ng'

try:
    import paho.mqtt.client as mqtt
    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False

_instance = None
_instance_lock = threading.Lock()


class MqttService:

    def __init__(self):
        self._clients = {}   # broker_id → mqtt.Client
        self._lock = threading.RLock()

    # ── API pública ──────────────────────────────────────────────────────────

    def start(self):
        """Conecta a todos os brokers ativos (não-bloqueante)."""
        if not _PAHO_AVAILABLE:
            print('[MQTT] paho-mqtt não instalado — suporte MQTT desabilitado')
            return
        threading.Thread(target=self._connect_all, daemon=True, name='mqtt-init').start()

    def refresh_broker(self, broker_id: int):
        """Reconecta um broker específico (chamar após criar/editar no admin)."""
        self._disconnect_broker(broker_id)
        from Model import BrokerMQTT, db
        try:
            b = db.query(BrokerMQTT).filter(
                BrokerMQTT.id == broker_id, BrokerMQTT.ativo == True
            ).first()
            if b:
                self._connect_broker(b)
        finally:
            db.remove()

    def stop_broker(self, broker_id: int):
        """Desconecta um broker (chamar ao desativar/excluir)."""
        self._disconnect_broker(broker_id)

    def unlock_cerberos(self, cerberos):
        """Publica comando de abertura para um Cerberos via MQTT."""
        if not _PAHO_AVAILABLE or not cerberos.broker_id:
            return
        with self._lock:
            client = self._clients.get(cerberos.broker_id)
        if not client:
            print(f'[MQTT] Broker {cerberos.broker_id} não conectado para {cerberos.mac}')
            return
        mac_safe = cerberos.mac.replace(':', '-')
        topic = f'{PREFIX}/{cerberos.ambiente_id}/cerberos/{mac_safe}/command'
        client.publish(topic, json.dumps({'command': 'unlock'}), qos=1)
        self._log_mqtt_command(cerberos, topic)
        print(f'[MQTT] Unlock → {cerberos.nome} ({cerberos.mac})')

    def is_connected(self, broker_id: int) -> bool:
        with self._lock:
            client = self._clients.get(broker_id)
        return client is not None and client.is_connected()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _connect_all(self):
        from Model import BrokerMQTT, db
        try:
            brokers = db.query(BrokerMQTT).filter(BrokerMQTT.ativo == True).all()
            for b in brokers:
                self._connect_broker(b)
        except Exception as e:
            print(f'[MQTT] Erro ao inicializar brokers: {e}')
        finally:
            db.remove()

    def _connect_broker(self, broker):
        cid = f'access-ng-srv-{broker.id}'
        client = mqtt.Client(client_id=cid, userdata={'broker_id': broker.id})
        if broker.usuario:
            client.username_pw_set(broker.usuario, broker.senha or '')
        if broker.tls:
            client.tls_set()
        client.on_connect    = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message    = self._on_message
        try:
            client.connect_async(broker.host, broker.porta, keepalive=60)
            client.loop_start()
            with self._lock:
                self._clients[broker.id] = client
            print(f'[MQTT] Conectando ao broker "{broker.nome}" ({broker.host}:{broker.porta})')
        except Exception as e:
            print(f'[MQTT] Falha ao conectar broker "{broker.nome}": {e}')

    def _disconnect_broker(self, broker_id: int):
        with self._lock:
            client = self._clients.pop(broker_id, None)
        if client:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass

    # ── Callbacks paho ────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        bid = userdata['broker_id']
        if rc == 0:
            print(f'[MQTT] Broker {bid} conectado — inscrevendo tópicos')
            client.subscribe(f'{PREFIX}/coldstart/+')
            client.subscribe(f'{PREFIX}/heartbeat/+')
            client.subscribe(f'{PREFIX}/+/caronte/+/tag')
            client.subscribe(f'{PREFIX}/+/cerberos/+/status')
        else:
            codes = {1: 'versão inaceitável', 2: 'id rejeitado', 3: 'servidor indisponível',
                     4: 'credenciais inválidas', 5: 'não autorizado'}
            print(f'[MQTT] Broker {bid} recusou conexão: {codes.get(rc, rc)}')

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            print(f'[MQTT] Broker {userdata["broker_id"]} desconectado inesperadamente (rc={rc})')

    def _on_message(self, client, userdata, msg):
        topic  = msg.topic
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
        except Exception:
            payload = {}
        parts = topic.split('/')
        try:
            # access-ng/coldstart/{mac}
            if len(parts) == 3 and parts[1] == 'coldstart':
                self._handle_coldstart(parts[2].replace('-', ':'), client, userdata)
            # access-ng/heartbeat/{mac}
            elif len(parts) == 3 and parts[1] == 'heartbeat':
                self._handle_heartbeat(parts[2].replace('-', ':'))
            # access-ng/{amb_id}/caronte/{mac}/tag
            elif len(parts) == 5 and parts[2] == 'caronte' and parts[4] == 'tag':
                self._handle_tag(parts[3].replace('-', ':'), parts[1], payload, client)
            # access-ng/{amb_id}/cerberos/{mac}/status
            elif len(parts) == 5 and parts[2] == 'cerberos' and parts[4] == 'status':
                self._handle_device_status(parts[3].replace('-', ':'),
                                           payload.get('status', 'online'))
        except Exception as e:
            print(f'[MQTT] Erro no handler "{topic}": {e}')

    # ── Handlers de mensagem ──────────────────────────────────────────────────

    def _log_mqtt_command(self, cerberos, topic):
        from Model import AccessLog, db
        try:
            db.add(AccessLog(
                timestamp=datetime.datetime.utcnow(),
                path=topic,
                method='MQTT',
                mac=cerberos.mac,
                event_type='mqtt_command',
                result='sucesso',
                ambiente_id=cerberos.ambiente_id,
                ambiente_nome=cerberos.ambiente.nome if cerberos.ambiente else None,
                payload=json.dumps({'command': 'unlock'}),
                message=f'Comando MQTT enviado para {cerberos.nome} ({cerberos.mac})'
            ))
            db.commit()
        except Exception as e:
            print(f'[MQTT] Erro ao logar comando {cerberos.mac}: {e}')
            db.rollback()

    def _handle_coldstart(self, mac, client, userdata):
        from Model import Cerberos, Caronte, AccessLog, db
        try:
            now = datetime.datetime.utcnow()
            device, dtype = None, None
            for Model, label in ((Cerberos, 'cerberos'), (Caronte, 'caronte')):
                device = db.query(Model).filter(Model.mac.ilike(mac)).first()
                if device:
                    dtype = label
                    break
            if device is None:
                db.add(AccessLog(
                    timestamp=now, path='mqtt:coldstart', method='MQTT', mac=mac,
                    event_type='device_coldstart', result='desconhecido',
                    message=f'MAC não cadastrado (MQTT coldstart): {mac}'
                ))
                db.commit()
                print(f'[MQTT] Coldstart desconhecido: {mac}')
                return
            device.coldstart_at = now
            device.last_seen    = now
            device.status       = 'online'
            label_name = getattr(device, 'nome', mac)
            db.add(AccessLog(
                timestamp=now, path='mqtt:coldstart', method='MQTT', mac=mac,
                event_type='device_coldstart', result='sucesso',
                ambiente_id=device.ambiente_id,
                ambiente_nome=device.ambiente.nome if device.ambiente else None,
                message=f'{dtype} iniciado via MQTT: {label_name} ({mac})'
            ))
            db.commit()
            print(f'[MQTT] Coldstart {dtype} {label_name} ({mac})')
        except Exception as e:
            print(f'[MQTT] Erro coldstart {mac}: {e}')
            db.rollback()
        finally:
            db.remove()

    def _handle_heartbeat(self, mac):
        from Model import Cerberos, Caronte, AccessLog, db
        try:
            now = datetime.datetime.utcnow()
            updated = False
            found_device = None
            for Model in (Cerberos, Caronte):
                device = db.query(Model).filter(Model.mac.ilike(mac)).first()
                if device:
                    device.last_seen = now
                    device.status    = 'online'
                    updated = True
                    found_device = device
            if updated:
                db.add(AccessLog(
                    timestamp=now,
                    path='mqtt:heartbeat',
                    method='MQTT',
                    mac=mac,
                    event_type='mqtt_heartbeat',
                    result='sucesso',
                    ambiente_id=found_device.ambiente_id,
                    ambiente_nome=found_device.ambiente.nome if found_device.ambiente else None,
                    message=f'Heartbeat MQTT recebido de {mac}'
                ))
                db.commit()
            else:
                db.add(AccessLog(
                    timestamp=now,
                    path='mqtt:heartbeat',
                    method='MQTT',
                    mac=mac,
                    event_type='mqtt_heartbeat',
                    result='desconhecido',
                    message=f'Heartbeat MQTT de MAC não cadastrado: {mac}'
                ))
                db.commit()
        except Exception as e:
            print(f'[MQTT] Erro heartbeat {mac}: {e}')
            db.rollback()
        finally:
            db.remove()

    def _handle_device_status(self, mac, status):
        from Model import Cerberos, Caronte, AccessLog, db
        try:
            now = datetime.datetime.utcnow()
            found_device = None
            for Model in (Cerberos, Caronte):
                device = db.query(Model).filter(Model.mac.ilike(mac)).first()
                if device:
                    device.status    = status
                    device.last_seen = now
                    found_device = device
                    break
            db.add(AccessLog(
                timestamp=now,
                path='mqtt:status',
                method='MQTT',
                mac=mac,
                event_type='mqtt_status',
                result='sucesso' if found_device else 'desconhecido',
                ambiente_id=found_device.ambiente_id if found_device else None,
                ambiente_nome=found_device.ambiente.nome if found_device and found_device.ambiente else None,
                payload=json.dumps({'status': status}),
                message=(
                    f'Status MQTT recebido de {mac}: {status}'
                    if found_device else
                    f'Status MQTT de MAC não cadastrado: {mac} ({status})'
                )
            ))
            db.commit()
        except Exception as e:
            print(f'[MQTT] Erro status {mac}: {e}')
            db.rollback()
        finally:
            db.remove()

    def _handle_tag(self, mac, amb_id_str, payload, client):
        from Model import Caronte, AccessLog, db
        from Tartaro import Tartaro as TartaroClass
        try:
            tag   = payload.get('tag')
            chave = payload.get('chave')
            if not tag or not chave:
                db.add(AccessLog(
                    timestamp=datetime.datetime.utcnow(),
                    path='mqtt:tag',
                    method='MQTT',
                    mac=mac,
                    tag=tag,
                    event_type='tentativa_tag',
                    result='falha',
                    payload=json.dumps(payload),
                    message='Payload MQTT de TAG sem tag ou chave'
                ))
                db.commit()
                return
            caronte = db.query(Caronte).filter(Caronte.mac.ilike(mac)).first()
            if caronte is None:
                db.add(AccessLog(
                    timestamp=datetime.datetime.utcnow(),
                    path='mqtt:tag',
                    method='MQTT',
                    mac=mac,
                    tag=tag,
                    event_type='tentativa_tag',
                    result='desconhecido',
                    payload=json.dumps(payload),
                    message=f'TAG MQTT de Caronte não cadastrado: {mac}'
                ))
                db.commit()
                print(f'[MQTT] TAG de Caronte desconhecido: {mac}')
                return
            auth = TartaroClass().autenticarTAGDetalhado(tag=tag, senha=chave, mac=mac)
            mac_safe     = mac.replace(':', '-')
            result_topic = f'{PREFIX}/{caronte.ambiente_id}/caronte/{mac_safe}/result'
            client.publish(result_topic,
                           json.dumps({'allow': auth['allow'], 'motivo': auth.get('motivo')}),
                           qos=1)
            db.add(AccessLog(
                timestamp=datetime.datetime.utcnow(),
                path='mqtt:tag', method='MQTT', mac=mac, tag=tag,
                event_type='tentativa_tag',
                result='sucesso' if auth['allow'] else 'falha',
                ambiente_id=caronte.ambiente_id,
                ambiente_nome=caronte.ambiente.nome if caronte.ambiente else None,
                usuario_id=auth['usuario'].id if auth.get('usuario') else None,
                usuario_nome=auth['usuario'].nome if auth.get('usuario') else None,
                payload=json.dumps(payload),
                message=auth.get('motivo') or 'Acesso autorizado por tag (MQTT)'
            ))
            db.commit()
            print(f'[MQTT] Auth TAG {mac}: {"OK" if auth["allow"] else "NEGADO"}')
            if auth['allow'] and auth.get('ambiente'):
                for cerberos in auth['ambiente'].cerberoses:
                    self.unlock_cerberos(cerberos)
        except Exception as e:
            print(f'[MQTT] Erro auth TAG {mac}: {e}')
            db.rollback()
        finally:
            db.remove()


def get_service() -> MqttService:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = MqttService()
    return _instance
