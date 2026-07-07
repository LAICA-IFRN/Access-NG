from Tartaro import *
from flask import (Flask, render_template, jsonify, request,
                   session, redirect, url_for, flash, abort, send_file)
from flask_bootstrap import Bootstrap
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from collections import defaultdict
from sqlalchemy import or_
import datetime
import threading
import time
import os
import json
from mqtt_service import get_service as _mqtt

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get('SECRET_KEY', 'tartaro-dev-key-change-in-prod')
Bootstrap(app)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

OFFLINE_THRESHOLD = 30  # seconds without contact → device is offline

# Raiz do repositório (um nível acima de Sistema/), usada para servir os
# arquivos de OTA dos dispositivos direto do servidor — evita depender do
# raw.githubusercontent.com, que a rede da IFRN não entrega de forma
# confiável para arquivos maiores.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OTA_ALLOWED_FILES = {
    'Hardware/Fechadura/version.json',
    'Hardware/Fechadura/version_esp32.json',
    'Hardware/Fechadura/Cerberos_BitDogLab_MQTT.py',
    'Hardware/Fechadura/CerberosESP32.py',
    'Hardware/Autenticador/version.json',
    'Hardware/Autenticador/CaronteESP32C3.py',
}


def _serialize_payload():
    """Retorna o payload da requisição para fins de log."""
    payload = None
    if request.method in ('POST', 'PUT', 'PATCH'):
        try:
            payload = request.get_json(silent=True)
        except Exception:
            payload = None
    if payload is None:
        if request.form:
            payload = request.form.to_dict()
        elif request.args:
            payload = request.args.to_dict()
    return payload


def _current_session_usuario():
    user_id = session.get('admin_id') or session.get('user_id')
    if not user_id:
        return None
    try:
        return db.query(Usuario).filter(Usuario.id == user_id).first()
    except Exception:
        db.rollback()
        return None


def _ambiente_from_mac(mac):
    if not mac:
        return None
    try:
        device = db.query(Cerberos).filter(Cerberos.mac.ilike(mac)).first()
        if device is None:
            device = db.query(Caronte).filter(Caronte.mac.ilike(mac)).first()
        return device.ambiente if device is not None else None
    except Exception:
        db.rollback()
        return None


def _create_log_entry(status_code=None, message=None):
    payload = _serialize_payload()
    mac = None
    tag = None
    if isinstance(payload, dict):
        mac = payload.get('mac')
        tag = payload.get('tag')
    usuario = _current_session_usuario()
    ambiente = _ambiente_from_mac(mac)
    try:
        log = AccessLog(
            timestamp=datetime.datetime.utcnow(),
            path=request.path,
            method=request.method,
            ip=request.remote_addr,
            mac=mac,
            tag=tag,
            event_type='api_request',
            ambiente_id=ambiente.id if ambiente is not None else None,
            ambiente_nome=ambiente.nome if ambiente is not None else None,
            usuario_id=usuario.id if usuario is not None else None,
            usuario_nome=usuario.nome if usuario is not None else None,
            status_code=status_code,
            payload=json.dumps(payload, default=str) if payload is not None else None,
            message=(str(message)[:2000] if message is not None else None)
        )
        db.add(log)
        db.commit()
        return log
    except Exception as e:
        print(f"[Log] Falha ao criar log: {e}")
        db.rollback()
        return None


def _create_audit_log(event_type, result, message=None, mac=None, tag=None,
                      ambiente=None, usuario=None, payload=None):
    try:
        log = AccessLog(
            timestamp=datetime.datetime.utcnow(),
            path=request.path,
            method=request.method,
            ip=request.remote_addr,
            mac=mac,
            tag=tag,
            event_type=event_type,
            result=result,
            ambiente_id=ambiente.id if ambiente is not None else None,
            ambiente_nome=ambiente.nome if ambiente is not None else None,
            usuario_id=usuario.id if usuario is not None else None,
            usuario_nome=usuario.nome if usuario is not None else None,
            payload=json.dumps(payload, default=str) if payload is not None else None,
            message=(str(message)[:2000] if message is not None else None)
        )
        db.add(log)
        db.commit()
        return log
    except Exception as e:
        print(f"[Audit] Falha ao criar log: {e}")
        db.rollback()
        return None


def _create_device_event_log(event_type, mac, ambiente=None, message=None):
    """Loga eventos de dispositivo sem necessitar de contexto de requisicao HTTP."""
    try:
        log = AccessLog(
            timestamp=datetime.datetime.utcnow(),
            path='(sistema)',
            method='SYSTEM',
            mac=mac,
            event_type=event_type,
            result='sucesso' if event_type == 'device_coldstart' else 'falha',
            ambiente_id=ambiente.id if ambiente is not None else None,
            ambiente_nome=ambiente.nome if ambiente is not None else None,
            message=(str(message)[:2000] if message is not None else None)
        )
        db.add(log)
        db.commit()
    except Exception as e:
        print(f"[DeviceLog] Falha ao criar log: {e}")
        db.rollback()


@app.before_request
def log_request():
    request._t0 = time.time()
    if request.path.startswith('/static'):
        return
    log = _create_log_entry()
    if log is not None:
        request.api_log_id = log.id


@app.after_request
def log_response(response):
    if hasattr(request, 'api_log_id'):
        try:
            log = db.get(AccessLog, request.api_log_id)
            if log:
                log.status_code = response.status_code
                log.message = response.get_data(as_text=True)[:2000]
                if hasattr(request, '_t0'):
                    log.duration_ms = round((time.time() - request._t0) * 1000)
                db.commit()
        except Exception as e:
            print(f"[Log] Falha ao atualizar log: {e}")
            db.rollback()
    return response


# ── Auth decorators ──────────────────────────────────────────────────────────

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.remove()


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = session.get('admin_id')
        if not uid:
            return redirect(url_for('admin_login'))
        usuario = db.query(Usuario).filter(Usuario.id == uid).first()
        if usuario is None or not (usuario.admin or usuario.papeis):
            session.pop('admin_id', None)
            return redirect(url_for('admin_login'))
        if not usuario.admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def caronte_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('caronte_login'))
        return f(*args, **kwargs)
    return decorated


# ── Papéis por Tartaro ───────────────────────────────────────────────────────

def _papel_em(usuario, ambiente_id):
    """Papel ('gerente'/'colaborador'/'leitor') do usuário num ambiente, ou None."""
    if not usuario or ambiente_id is None:
        return None
    pa = db.query(PapelAmbiente).filter(
        PapelAmbiente.usuario_id == usuario.id,
        PapelAmbiente.ambiente_id == ambiente_id,
    ).first()
    return pa.papel if pa else None


def _ambientes_com_papel(usuario, papeis):
    """IDs de ambiente onde o usuário tem um dos papéis dados.

    Retorna None (sentinela "todos os ambientes") se o usuário for admin geral.
    """
    if not usuario:
        return []
    if usuario.admin:
        return None
    rows = db.query(PapelAmbiente).filter(
        PapelAmbiente.usuario_id == usuario.id,
        PapelAmbiente.papel.in_(papeis),
    ).all()
    return [r.ambiente_id for r in rows]


def pode_gerenciar_dispositivos(usuario, ambiente_id):
    return bool(usuario) and (usuario.admin or _papel_em(usuario, ambiente_id) == 'gerente')


def pode_criar_usuarios(usuario, ambiente_id):
    return bool(usuario) and (usuario.admin or _papel_em(usuario, ambiente_id) in ('gerente', 'colaborador'))


def pode_editar_usuarios(usuario, ambiente_id):
    return bool(usuario) and (usuario.admin or _papel_em(usuario, ambiente_id) == 'gerente')


def pode_ler_logs(usuario, ambiente_id):
    return bool(usuario) and (usuario.admin or _papel_em(usuario, ambiente_id) in ('gerente', 'leitor'))


def painel_required(f):
    """Libera acesso ao painel para admin geral OU qualquer usuário com algum papel."""
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = session.get('admin_id')
        if not uid:
            return redirect(url_for('admin_login'))
        usuario = db.query(Usuario).filter(Usuario.id == uid).first()
        if not usuario or not (usuario.admin or usuario.papeis):
            session.pop('admin_id', None)
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def _inject_current_usuario():
    usuario = _current_session_usuario() if session.get('admin_id') else None
    nav = {'dispositivos': False, 'usuarios': False, 'logs': False, 'meu_tartaro_id': None}
    if usuario:
        if usuario.admin:
            nav = {'dispositivos': True, 'usuarios': True, 'logs': True, 'meu_tartaro_id': None}
        else:
            papeis = {p.papel for p in usuario.papeis}
            meu_tartaro_id = next(
                (p.ambiente_id for p in usuario.papeis if p.papel in ('gerente', 'leitor')), None
            )
            nav = {
                'dispositivos': 'gerente' in papeis,
                'usuarios': bool(papeis & {'gerente', 'colaborador'}),
                'logs': bool(papeis & {'gerente', 'leitor'}),
                'meu_tartaro_id': meu_tartaro_id,
            }
    return {'current_usuario': usuario, 'nav_visivel': nav}


# ── Device helpers ───────────────────────────────────────────────────────────

def _touch_device(mac: str, versao: str = None, ip: str = None, uptime: str = None):
    now = datetime.datetime.utcnow()
    updated = False
    for model in (Cerberos, Caronte):
        device = db.query(model).filter(model.mac.ilike(mac)).first()
        if device:
            device.last_seen = now
            device.status = 'online'
            if versao:
                device.versao_firmware = versao
            if ip:
                device.ip = ip
            if uptime:
                device.uptime = uptime
            updated = True
    if updated:
        db.commit()


def _offline_monitor():
    while True:
        time.sleep(15)
        threshold = datetime.datetime.utcnow() - datetime.timedelta(seconds=OFFLINE_THRESHOLD)
        try:
            offline_events = []
            for model in (Cerberos, Caronte):
                stale = db.query(model).filter(
                    model.status == 'online',
                    model.last_seen != None,
                    model.last_seen < threshold
                ).all()
                for d in stale:
                    offline_events.append((d.mac, getattr(d, 'nome', d.mac), d.ambiente))
                    d.status = 'offline'
            db.commit()
            for mac, label, ambiente in offline_events:
                _create_device_event_log(
                    event_type='device_offline',
                    mac=mac,
                    ambiente=ambiente,
                    message=f'Dispositivo offline (sem contato por {OFFLINE_THRESHOLD}s): {label}'
                )
        except Exception:
            db.rollback()
        finally:
            db.remove()


threading.Thread(target=_offline_monitor, daemon=True).start()
_mqtt().start()


def ensure_default_admin():
    """Cria um usuário admin padrão se nenhum admin existir."""
    try:
        admin_count = db.query(Usuario).filter(Usuario.admin == True).count()
    except Exception:
        admin_count = 0
    if admin_count == 0:
        print('[Admin] Nenhum administrador encontrado. Criando usuário admin padrão.')
        admin = Usuario(nome='Administrador', matricula='admin', pin='0000', admin=True)
        db.add(admin)
        db.commit()
        print('[Admin] Usuário admin criado: matricula="admin", pin="0000"')

ensure_default_admin()


# ── Existing IoT endpoints (backward-compatible) ─────────────────────────────

@app.route('/')
def hello():
    ambientes = db.query(Ambiente).all()
    return render_template("index.html", count=len(ambientes))


@app.route('/caronte/autenticarTag', methods=['POST'])
def autenticar():
    c = request.json
    _touch_device(c['mac'])
    auth = Tartaro().autenticarTAGDetalhado(tag=c['tag'], senha=c['chave'], mac=c['mac'])
    _create_audit_log(
        event_type='tentativa_tag',
        result='sucesso' if auth['allow'] else 'falha',
        message=auth.get('motivo') or 'Acesso autorizado por tag',
        mac=c.get('mac'),
        tag=c.get('tag'),
        ambiente=auth.get('ambiente'),
        usuario=auth.get('usuario'),
        payload={'tag': c.get('tag'), 'mac': c.get('mac')}
    )
    if auth['allow'] and auth.get('ambiente'):
        for _cb in auth['ambiente'].cerberoses:
            _mqtt().unlock_cerberos(_cb)
    return jsonify({'Allow': auth['allow']})


@app.route('/service/enviroments/enviroments/access/', methods=['POST'])
def jobs():
    mac = request.json['mac']
    _touch_device(mac)
    return jsonify({'Allow': Tartaro().verificarAcionamento(mac=mac)})


@app.route('/service/microcontrollers/microcontrollers/esp8266/is-alive/', methods=['POST'])
def is_alive_legacy():
    mac = request.json['mac']
    _touch_device(mac)
    return jsonify({'received': mac})


# ── OTA (firmware servido pelo próprio servidor) ─────────────────────────────

@app.route('/ota/<path:filepath>')
def ota_file(filepath):
    """Serve os .py e version.json dos dispositivos para OTA. Whitelist
    explícita — nunca lê arquivo fora dessa lista, sem exceção de path."""
    if filepath not in _OTA_ALLOWED_FILES:
        abort(404)
    full_path = os.path.join(_REPO_ROOT, filepath)
    mimetype = 'application/json' if filepath.endswith('.json') else 'text/plain'
    return send_file(full_path, mimetype=mimetype)


# ── New device endpoints ─────────────────────────────────────────────────────

@app.route('/device/coldstart', methods=['POST'])
def coldstart():
    content = request.json or {}
    mac = content.get('mac')
    if not mac:
        return jsonify({'error': 'mac required'}), 400
    now = datetime.datetime.utcnow()
    device = db.query(Cerberos).filter(Cerberos.mac.ilike(mac)).first()
    device_type = 'cerberos'
    if device is None:
        device = db.query(Caronte).filter(Caronte.mac.ilike(mac)).first()
        device_type = 'caronte'
    if device is None:
        _create_audit_log(
            event_type='device_coldstart',
            result='desconhecido',
            message=f'MAC não cadastrado: {mac}',
            mac=mac,
            payload={'mac': mac, 'chave': content.get('chave')}
        )
        return jsonify({'status': 'unknown', 'mac': mac}), 404
    if device.chave != content.get('chave'):
        _create_audit_log(
            event_type='device_coldstart',
            result='negado',
            message=f'Chave inválida para {device_type} {getattr(device, "nome", mac)} ({mac})',
            mac=mac,
            ambiente=device.ambiente,
            payload={'mac': mac, 'chave': content.get('chave')}
        )
        return jsonify({'status': 'denied', 'mac': mac}), 403
    device.coldstart_at = now
    device.last_seen = now
    device.status = 'online'
    if content.get('versao'):
        device.versao_firmware = content['versao']
    db.commit()
    device_label = getattr(device, 'nome', mac)
    _create_audit_log(
        event_type='device_coldstart',
        result='sucesso',
        message=f'{device_type} iniciado: {device_label} ({mac})',
        mac=mac,
        ambiente=device.ambiente
    )
    return jsonify({'status': 'ok', 'device': device_type, 'mac': mac,
                    'ambiente_id': device.ambiente_id})


@app.route('/device/heartbeat', methods=['POST'])
def heartbeat():
    content = request.json or {}
    mac = content.get('mac')
    if not mac:
        return jsonify({'error': 'mac required'}), 400
    _touch_device(mac, versao=content.get('versao'), ip=content.get('ip'), uptime=content.get('uptime'))
    return jsonify({'received': mac})


@app.route('/device/command', methods=['POST'])
def device_command():
    content = request.json or {}
    mac = content.get('mac')
    if not mac:
        return jsonify({'error': 'mac required'}), 400

    cerberos = db.query(Cerberos).filter(Cerberos.mac.ilike(mac)).first()
    if cerberos is None:
        _create_audit_log(
            event_type='device_command',
            result='desconhecido',
            message=f'Cerberos não cadastrado tentou buscar comando: {mac}',
            mac=mac,
            payload={'mac': mac}
        )
        return jsonify({'error': 'unknown cerberos', 'mac': mac}), 404

    _touch_device(mac)
    try:
        wait = float(content.get('wait', 20))
    except (TypeError, ValueError):
        wait = 20
    wait = max(0, min(wait, 25))

    if Tartaro().verificarAcionamento(mac=cerberos.mac, timeout=wait):
        _create_audit_log(
            event_type='comando_abertura',
            result='sucesso',
            message='Comando de abertura entregue ao Cerberos',
            mac=cerberos.mac,
            ambiente=cerberos.ambiente
        )
        return jsonify({'command': 'unlock'})
    return jsonify({'command': None})


@app.route('/api/dashboard', methods=['GET'])
def api_dashboard():
    now = datetime.datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    access_types = ['tentativa_tag', 'tentativa_web', 'comando_abertura', 'entrada_fisica']

    cerberoses = db.query(Cerberos).all()
    carontes   = db.query(Caronte).all()
    all_devices = list(cerberoses) + list(carontes)

    online  = sum(1 for d in all_devices if d.status == 'online')
    offline = sum(1 for d in all_devices if d.status == 'offline')
    unknown = len(all_devices) - online - offline

    accesses_today = db.query(AccessLog).filter(
        AccessLog.event_type.in_(access_types),
        AccessLog.timestamp >= today_start
    ).count()
    sucesso_today = db.query(AccessLog).filter(
        AccessLog.event_type.in_(access_types),
        AccessLog.result == 'sucesso',
        AccessLog.timestamp >= today_start
    ).count()
    last_access = db.query(AccessLog).filter(
        AccessLog.event_type.in_(access_types)
    ).order_by(AccessLog.timestamp.desc()).first()

    recent_events = db.query(AccessLog).filter(
        AccessLog.event_type.in_(access_types)
    ).order_by(AccessLog.timestamp.desc()).limit(12).all()

    device_events = db.query(AccessLog).filter(
        AccessLog.event_type.in_(['device_coldstart', 'device_offline'])
    ).order_by(AccessLog.timestamp.desc()).limit(8).all()

    ambientes = db.query(Ambiente).all()
    tartaros = []
    for amb in ambientes:
        entry = {'id': amb.id, 'nome': amb.nome, 'local': amb.local,
                 'cerberoses': [], 'carontes': []}
        for c in amb.cerberoses:
            entry['cerberoses'].append({
                'id': c.id, 'nome': c.nome, 'mac': c.mac,
                'status': c.status or 'unknown',
                'last_seen': c.last_seen.isoformat() if c.last_seen else None,
                'coldstart_at': c.coldstart_at.isoformat() if c.coldstart_at else None,
            })
        for c in amb.carontes:
            entry['carontes'].append({
                'id': c.id, 'mac': c.mac,
                'status': c.status or 'unknown',
                'last_seen': c.last_seen.isoformat() if c.last_seen else None,
                'coldstart_at': c.coldstart_at.isoformat() if c.coldstart_at else None,
            })
        tartaros.append(entry)

    def _ev(e):
        return {
            'timestamp': e.timestamp.isoformat(),
            'event_type': e.event_type,
            'result': e.result,
            'usuario_nome': e.usuario_nome,
            'ambiente_nome': e.ambiente_nome,
            'mac': e.mac,
            'tag': e.tag,
            'message': e.message,
        }

    return jsonify({
        'server_time': now.isoformat(),
        'devices': {
            'total': len(all_devices),
            'online': online,
            'offline': offline,
            'unknown': unknown,
        },
        'accesses': {
            'today': accesses_today,
            'sucesso': sucesso_today,
            'falha': accesses_today - sucesso_today,
            'last_at': last_access.timestamp.isoformat() if last_access else None,
        },
        'tartaros': tartaros,
        'recent_events': [_ev(e) for e in recent_events],
        'device_events': [_ev(e) for e in device_events],
    })


@app.route('/api/status', methods=['GET'])
def api_status():
    ambientes = db.query(Ambiente).all()
    result = []
    for amb in ambientes:
        tartaro = {
            'id': amb.id, 'nome': amb.nome, 'local': amb.local,
            'cerberoses': [], 'carontes': [],
        }
        for c in amb.cerberoses:
            tartaro['cerberoses'].append({
                'id': c.id, 'nome': c.nome, 'mac': c.mac,
                'status': c.status or 'unknown',
                'last_seen': c.last_seen.isoformat() if c.last_seen else None,
                'coldstart_at': c.coldstart_at.isoformat() if c.coldstart_at else None,
            })
        for c in amb.carontes:
            tartaro['carontes'].append({
                'id': c.id, 'mac': c.mac,
                'status': c.status or 'unknown',
                'last_seen': c.last_seen.isoformat() if c.last_seen else None,
                'coldstart_at': c.coldstart_at.isoformat() if c.coldstart_at else None,
            })
        result.append(tartaro)
    return jsonify(result)


# ── Web Caronte ──────────────────────────────────────────────────────────────

@app.route('/caronte')
def caronte_login():
    if 'user_id' in session:
        return render_template('caronte/home.html', user_nome=session.get('user_nome'))
    return render_template('caronte/login.html')


@app.route('/caronte/login', methods=['POST'])
def caronte_login_post():
    matricula = request.form.get('matricula', '').strip()
    pin = request.form.get('pin', '').strip()
    usuario = db.query(Usuario).filter(
        Usuario.matricula == matricula,
        Usuario.pin == pin
    ).first()
    if not usuario:
        _create_audit_log(
            event_type='login_caronte',
            result='falha',
            message='Matricula ou PIN incorretos',
            payload={'matricula': matricula}
        )
        flash('Matrícula ou PIN incorretos.', 'danger')
        return redirect(url_for('caronte_login'))
    _create_audit_log(
        event_type='login_caronte',
        result='sucesso',
        message='Login no portal Caronte',
        usuario=usuario,
        payload={'matricula': matricula}
    )
    session['user_id'] = usuario.id
    session['user_nome'] = usuario.nome
    return redirect(url_for('caronte_portal'))


@app.route('/caronte/portal')
@caronte_required
def caronte_portal():
    return render_template('caronte/portal.html', user_nome=session.get('user_nome'))


@app.route('/caronte/ambientes-proximos')
@caronte_required
def caronte_ambientes_proximos():
    try:
        lat = float(request.args['lat'])
        lon = float(request.args['lon'])
    except (KeyError, ValueError):
        return jsonify({'error': 'lat e lon obrigatórios'}), 400

    tartaro = Tartaro()
    proximos = tartaro.ambientesProximos(lat, lon)
    result = []
    for a in proximos:
        online = [c for c in a.cerberoses if c.status == 'online']
        result.append({
            'id': a.id,
            'nome': a.nome,
            'local': a.local,
            'available': len(online) > 0,
            'cerberoses_online': len(online),
            'cerberoses_total': len(a.cerberoses),
        })
    return jsonify(result)


@app.route('/caronte/solicitar', methods=['POST'])
@caronte_required
def caronte_solicitar():
    content = request.json or {}
    ambiente_id = content.get('ambiente_id')
    lat = content.get('lat')
    lon = content.get('lon')

    if not all([ambiente_id, lat is not None, lon is not None]):
        return jsonify({'error': 'ambiente_id, lat e lon obrigatórios'}), 400

    usuario = db.query(Usuario).filter(Usuario.id == session['user_id']).first()
    if not usuario:
        _create_audit_log(
            event_type='tentativa_web',
            result='falha',
            message='Sessao invalida',
            payload=content
        )
        return jsonify({'error': 'sessão inválida'}), 401

    # Validate geolocation again server-side
    ambiente = db.query(Ambiente).filter(Ambiente.id == ambiente_id).first()
    if not ambiente:
        _create_audit_log(
            event_type='tentativa_web',
            result='falha',
            message='Ambiente nao encontrado',
            usuario=usuario,
            payload=content
        )
        return jsonify({'allow': False, 'motivo': 'Ambiente não encontrado'}), 404

    if not any(c.status == 'online' for c in ambiente.cerberoses):
        _create_audit_log(
            event_type='tentativa_web',
            result='falha',
            message='Nenhum Cerberos online neste ambiente',
            ambiente=ambiente,
            usuario=usuario,
            payload=content
        )
        return jsonify({'allow': False, 'motivo': 'Nenhuma fechadura online neste ambiente'})

    if ambiente.latitude is not None and ambiente.longitude is not None:
        from Tartaro import _distancia_metros
        raio = ambiente.raio_metros or 50
        dist = _distancia_metros(lat, lon, ambiente.latitude, ambiente.longitude)
        if dist > raio:
            _create_audit_log(
                event_type='tentativa_web',
                result='falha',
                message=f'Fora do raio ({dist:.0f}m > {raio}m)',
                ambiente=ambiente,
                usuario=usuario,
                payload=content
            )
            return jsonify({'allow': False, 'motivo': f'Fora do raio ({dist:.0f}m > {raio}m)'})

    ok = Tartaro().autenticarWeb(
        matricula=usuario.matricula,
        pin=usuario.pin,
        ambiente_id=ambiente_id
    )
    if ok:
        _create_audit_log(
            event_type='tentativa_web',
            result='sucesso',
            message='Acesso autorizado pelo portal',
            ambiente=ambiente,
            usuario=usuario,
            payload=content
        )
        for _cb in ambiente.cerberoses:
            _mqtt().unlock_cerberos(_cb)
        return jsonify({'allow': True})
    _create_audit_log(
        event_type='tentativa_web',
        result='falha',
        message='Sem permissao para este ambiente',
        ambiente=ambiente,
        usuario=usuario,
        payload=content
    )
    return jsonify({'allow': False, 'motivo': 'Sem permissão para este ambiente'})


@app.route('/caronte/meus-logs')
@caronte_required
def caronte_meus_logs():
    user_id = session['user_id']
    access_event_types = ['tentativa_web', 'tentativa_tag', 'login_caronte', 'logout_caronte']
    logs = (
        db.query(AccessLog)
        .filter(
            AccessLog.usuario_id == user_id,
            AccessLog.event_type.in_(access_event_types)
        )
        .order_by(AccessLog.timestamp.desc())
        .limit(100)
        .all()
    )
    event_labels = {
        'tentativa_web': 'Acesso pelo portal',
        'tentativa_tag': 'Acesso por tag RFID',
        'login_caronte': 'Login no sistema',
        'logout_caronte': 'Logout do sistema',
    }
    return render_template(
        'caronte/meus_logs.html',
        user_nome=session.get('user_nome'),
        logs=logs,
        event_labels=event_labels
    )


@app.route('/caronte/perfil', methods=['GET', 'POST'])
@caronte_required
def caronte_perfil():
    usuario = db.query(Usuario).filter(Usuario.id == session['user_id']).first()
    if usuario is None:
        abort(404)
    if request.method == 'POST':
        f = request.form
        pin = f.get('pin', '').strip()
        if pin:
            if not (pin.isdigit() and len(pin) == 4):
                flash('PIN deve ter 4 dígitos.', 'danger')
                return redirect(url_for('caronte_perfil'))
            usuario.pin = pin
        _upsert_tag(usuario, f.get('tag', ''))
        db.commit()
        _create_audit_log(
            event_type='perfil_atualizado',
            result='sucesso',
            message='Usuário atualizou TAG/PIN no portal',
            usuario=usuario,
        )
        flash('Perfil atualizado.', 'success')
        return redirect(url_for('caronte_perfil'))
    return render_template('caronte/perfil.html', user_nome=session.get('user_nome'), usuario=usuario)


@app.route('/caronte/logout')
def caronte_logout():
    usuario = None
    if session.get('user_id'):
        usuario = db.query(Usuario).filter(Usuario.id == session.get('user_id')).first()
    _create_audit_log(
        event_type='logout_caronte',
        result='sucesso',
        message='Logout do portal Caronte',
        usuario=usuario
    )
    session.pop('user_id', None)
    session.pop('user_nome', None)
    return redirect(url_for('caronte_login'))


# ── Admin panel ──────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        matricula = request.form.get('matricula', '').strip()
        senha = request.form.get('senha', '').strip()
        usuario = db.query(Usuario).filter(Usuario.matricula == matricula).first()
        if usuario and not (usuario.admin or usuario.papeis):
            usuario = None

        autenticado = False
        if usuario:
            if usuario.senha:
                autenticado = check_password_hash(usuario.senha, senha)
            else:
                # Fallback para PIN enquanto a senha ainda não foi definida
                autenticado = (usuario.pin == senha)

        if not autenticado:
            _create_audit_log(
                event_type='login_admin',
                result='falha',
                message='Credenciais invalidas ou sem permissao de admin',
                payload={'matricula': matricula}
            )
            flash('Credenciais inválidas ou sem permissão de admin.', 'danger')
            return redirect(url_for('admin_login'))
        if not usuario.senha:
            flash('Você entrou com o PIN (fallback). Defina uma senha de admin no seu perfil.', 'warning')
        _create_audit_log(
            event_type='login_admin',
            result='sucesso',
            message='Login administrativo',
            usuario=usuario,
            payload={'matricula': matricula}
        )
        session['admin_id'] = usuario.id
        return redirect(url_for('admin_index'))
    return render_template('admin/login.html')


@app.route('/admin/logout')
def admin_logout():
    usuario = None
    if session.get('admin_id'):
        usuario = db.query(Usuario).filter(Usuario.id == session.get('admin_id')).first()
    _create_audit_log(
        event_type='logout_admin',
        result='sucesso',
        message='Logout administrativo',
        usuario=usuario
    )
    session.pop('admin_id', None)
    return redirect(url_for('admin_login'))


def _parse_date(value, default):
    """Converte string ISO 'YYYY-MM-DD' em date; cai no default em qualquer erro."""
    if not value:
        return default
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return default


def _latencia_series(ambiente_ids):
    """Série horária de latência média (ms) das últimas 24h, e a média geral.

    ambiente_ids=None => todos os Tartaros.
    """
    now = datetime.datetime.utcnow()
    since = now - datetime.timedelta(hours=24)

    log_q = db.query(AccessLog)
    if ambiente_ids is not None:
        log_q = log_q.filter(AccessLog.ambiente_id.in_(ambiente_ids))
    rows = log_q.filter(
        AccessLog.event_type == 'api_request',
        AccessLog.duration_ms.isnot(None),
        AccessLog.timestamp >= since,
    ).with_entities(AccessLog.timestamp, AccessLog.duration_ms).all()

    buckets = defaultdict(list)
    for ts, dur in rows:
        buckets[ts.replace(minute=0, second=0, microsecond=0)].append(dur)

    start_hour = since.replace(minute=0, second=0, microsecond=0)
    serie = []
    todas_amostras = []
    for i in range(25):
        hora = start_hour + datetime.timedelta(hours=i)
        if hora > now:
            break
        valores = buckets.get(hora, [])
        todas_amostras.extend(valores)
        media = round(sum(valores) / len(valores)) if valores else None
        serie.append({'hora': hora.strftime('%H:%M'), 'media_ms': media})

    media_geral = round(sum(todas_amostras) / len(todas_amostras)) if todas_amostras else None
    return serie, media_geral


def _aberturas_series(ambiente_ids, desde, ate):
    """Contagem diária de aberturas bem-sucedidas entre desde/ate (inclusive).

    ambiente_ids=None => todos os Tartaros.
    """
    access_types = ['tentativa_tag', 'tentativa_web', 'comando_abertura', 'entrada_fisica']
    if ate < desde:
        desde, ate = ate, desde
    if (ate - desde).days > 365:
        desde = ate - datetime.timedelta(days=365)

    log_q = db.query(AccessLog)
    if ambiente_ids is not None:
        log_q = log_q.filter(AccessLog.ambiente_id.in_(ambiente_ids))
    timestamps = [ts for (ts,) in log_q.filter(
        AccessLog.event_type.in_(access_types),
        AccessLog.result == 'sucesso',
        AccessLog.timestamp >= datetime.datetime.combine(desde, datetime.time.min),
        AccessLog.timestamp <= datetime.datetime.combine(ate, datetime.time.max),
    ).with_entities(AccessLog.timestamp).all()]

    por_dia = defaultdict(int)
    for ts in timestamps:
        por_dia[ts.date()] += 1
    dias = [desde + datetime.timedelta(days=i) for i in range((ate - desde).days + 1)]
    return [{'dia': d.strftime('%d/%m'), 'total': por_dia.get(d, 0)} for d in dias]


def _intervalos_online(mac, desde, ate):
    """Intervalos (inicio, fim) em que o dispositivo esteve online, derivados dos
    contatos em AccessLog (qualquer linha com esse mac), usando o mesmo limiar do
    monitor de offline: sem contato por OFFLINE_THRESHOLD segundos = offline.
    """
    delta = datetime.timedelta(seconds=OFFLINE_THRESHOLD)
    contatos = [ts for (ts,) in db.query(AccessLog).filter(
        AccessLog.mac.ilike(mac),
        AccessLog.event_type != 'device_offline',
        AccessLog.timestamp >= desde - delta,
        AccessLog.timestamp <= ate,
    ).with_entities(AccessLog.timestamp).order_by(AccessLog.timestamp).all()]

    intervalos = []
    for ts in contatos:
        fim = ts + delta
        if intervalos and ts <= intervalos[-1][1]:
            intervalos[-1] = (intervalos[-1][0], max(intervalos[-1][1], fim))
        else:
            intervalos.append((ts, fim))
    return intervalos


def _online_pct(intervalos, ini, fim):
    """% do intervalo [ini, fim] coberto pelos intervalos online (já ordenados)."""
    total = (fim - ini).total_seconds()
    if total <= 0:
        return 0.0
    coberto = sum(
        (min(b, fim) - max(a, ini)).total_seconds()
        for a, b in intervalos if b > ini and a < fim
    )
    return round(min(coberto, total) / total * 100, 1)


def _sla_series(mac, desde, ate, unidade):
    """Série de % online por hora ou por dia entre desde/ate.

    Buckets alinhados à hora cheia (unidade='hora') ou à meia-noite
    (unidade='dia'), mesma convenção de _latencia_series/_aberturas_series —
    sem isso, o rótulo do bucket (ex. "09/06") podia não corresponder ao dia
    em que o contato de fato ocorreu.
    """
    intervalos = _intervalos_online(mac, desde, ate)
    if unidade == 'hora':
        passo = datetime.timedelta(hours=1)
        cursor = desde.replace(minute=0, second=0, microsecond=0)
        fmt = '%d/%m %Hh'
    else:
        passo = datetime.timedelta(days=1)
        cursor = datetime.datetime.combine(desde.date(), datetime.time.min)
        fmt = '%d/%m'

    serie = []
    while cursor < ate:
        prox = cursor + passo
        serie.append({
            'periodo': cursor.strftime(fmt),
            'pct': _online_pct(intervalos, max(cursor, desde), min(prox, ate)),
        })
        cursor = prox
    return serie


def _build_dashboard_analytics(ambiente_ids):
    """Estatísticas da home do painel. ambiente_ids=None => todos os Tartaros."""
    now = datetime.datetime.utcnow()
    hoje = now.date()

    cerb_q = db.query(Cerberos)
    car_q = db.query(Caronte)
    if ambiente_ids is not None:
        cerb_q = cerb_q.filter(Cerberos.ambiente_id.in_(ambiente_ids))
        car_q = car_q.filter(Caronte.ambiente_id.in_(ambiente_ids))

    devices = cerb_q.all() + car_q.all()
    online = sum(1 for d in devices if d.status == 'online')
    offline = sum(1 for d in devices if d.status == 'offline')
    unknown = len(devices) - online - offline

    latencia_serie, latencia_media_ms = _latencia_series(ambiente_ids)
    aberturas_por_dia = _aberturas_series(ambiente_ids, hoje - datetime.timedelta(days=13), hoje)

    return {
        'devices': {'online': online, 'offline': offline, 'unknown': unknown, 'total': len(devices)},
        'latencia_media_ms': latencia_media_ms,
        'latencia_serie': latencia_serie,
        'aberturas_por_dia': aberturas_por_dia,
    }


@app.route('/admin/')
@painel_required
def admin_index():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente', 'colaborador', 'leitor'))
    analytics_ids = None if usuario.admin else _ambientes_com_papel(usuario, ('gerente', 'leitor'))
    pode_ver_analytics = usuario.admin or bool(analytics_ids)

    if ambiente_ids is None:  # admin geral
        stats = {
            'ambientes': db.query(Ambiente).count(),
            'cerberoses': db.query(Cerberos).count(),
            'carontes': db.query(Caronte).count(),
            'usuarios': db.query(Usuario).count(),
            'logs': db.query(AccessLog).count(),
        }
    else:
        stats = {
            'cerberoses': db.query(Cerberos).filter(Cerberos.ambiente_id.in_(ambiente_ids)).count(),
            'carontes': db.query(Caronte).filter(Caronte.ambiente_id.in_(ambiente_ids)).count(),
            'usuarios': db.query(Usuario).join(Usuario.ambientes).filter(Ambiente.id.in_(ambiente_ids)).distinct().count(),
            'logs': db.query(AccessLog).filter(AccessLog.ambiente_id.in_(ambiente_ids)).count(),
        }

    analytics = None
    recent_device_events = []
    recent_access_events = []
    if pode_ver_analytics:
        analytics = _build_dashboard_analytics(analytics_ids)
        scoped_log_q = db.query(AccessLog) if analytics_ids is None else (
            db.query(AccessLog).filter(AccessLog.ambiente_id.in_(analytics_ids))
        )
        recent_device_events = (
            scoped_log_q
            .filter(AccessLog.event_type.in_(['device_coldstart', 'device_offline']))
            .order_by(AccessLog.timestamp.desc())
            .limit(15)
            .all()
        )
        recent_access_events = (
            scoped_log_q
            .filter(AccessLog.event_type.in_([
                'tentativa_tag', 'tentativa_web', 'comando_abertura', 'entrada_fisica'
            ]))
            .order_by(AccessLog.timestamp.desc())
            .limit(10)
            .all()
        )

    return render_template(
        'admin/index.html',
        stats=stats,
        analytics=analytics,
        recent_device_events=recent_device_events,
        recent_access_events=recent_access_events
    )


# Ambientes ──────────────────────────────────

@app.route('/admin/ambientes')
@admin_required
def admin_ambientes():
    ambientes = db.query(Ambiente).all()
    return render_template('admin/ambientes.html', ambientes=ambientes)


@app.route('/admin/ambientes/<int:id>')
@painel_required
def admin_ambiente_ver(id):
    usuario = _current_session_usuario()
    ambiente = db.query(Ambiente).filter(Ambiente.id == id).first()
    if ambiente is None:
        abort(404)
    papel_ids = _ambientes_com_papel(usuario, ('gerente', 'leitor'))
    if papel_ids is not None and id not in papel_ids:
        abort(403)

    hoje = datetime.datetime.utcnow().date()
    desde = _parse_date(request.args.get('desde'), hoje - datetime.timedelta(days=13))
    ate = _parse_date(request.args.get('ate'), hoje)
    aberturas_por_dia = _aberturas_series([id], desde, ate)

    now = datetime.datetime.utcnow()
    desde_24h = now - datetime.timedelta(hours=24)
    dispositivos = (
        [{'tipo': 'cerberos', 'obj': c,
          'sla_24h': _online_pct(_intervalos_online(c.mac, desde_24h, now), desde_24h, now)}
         for c in ambiente.cerberoses] +
        [{'tipo': 'caronte', 'obj': c,
          'sla_24h': _online_pct(_intervalos_online(c.mac, desde_24h, now), desde_24h, now)}
         for c in ambiente.carontes]
    )

    return render_template(
        'admin/ambiente_ver.html',
        ambiente=ambiente,
        aberturas_por_dia=aberturas_por_dia,
        desde=desde.isoformat(),
        ate=ate.isoformat(),
        dispositivos=dispositivos,
    )


@app.route('/admin/ambientes/novo', methods=['GET', 'POST'])
@admin_required
def admin_ambiente_novo():
    if request.method == 'POST':
        f = request.form
        amb = Ambiente(
            nome=f['nome'],
            local=f['local'],
            latitude=float(f['latitude']) if f.get('latitude') else None,
            longitude=float(f['longitude']) if f.get('longitude') else None,
            raio_metros=int(f['raio_metros']) if f.get('raio_metros') else 50,
        )
        db.add(amb)
        db.commit()
        flash('Ambiente criado.', 'success')
        return redirect(url_for('admin_ambientes'))
    return render_template('admin/ambiente_form.html', ambiente=None)


@app.route('/admin/ambientes/<int:id>/editar', methods=['GET', 'POST'])
@admin_required
def admin_ambiente_editar(id):
    amb = db.query(Ambiente).filter(Ambiente.id == id).first()
    if amb is None:
        abort(404)
    if request.method == 'POST':
        f = request.form
        amb.nome = f['nome']
        amb.local = f['local']
        amb.latitude = float(f['latitude']) if f.get('latitude') else None
        amb.longitude = float(f['longitude']) if f.get('longitude') else None
        amb.raio_metros = int(f['raio_metros']) if f.get('raio_metros') else 50
        db.commit()
        flash('Ambiente atualizado.', 'success')
        return redirect(url_for('admin_ambientes'))
    return render_template('admin/ambiente_form.html', ambiente=amb)


@app.route('/admin/ambientes/<int:id>/excluir', methods=['POST'])
@admin_required
def admin_ambiente_excluir(id):
    amb = db.query(Ambiente).filter(Ambiente.id == id).first()
    if amb is None:
        abort(404)
    db.delete(amb)
    db.commit()
    flash('Ambiente removido.', 'success')
    return redirect(url_for('admin_ambientes'))


# Cerberoses ─────────────────────────────────

@app.route('/admin/cerberoses')
@painel_required
def admin_cerberoses():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    q = db.query(Cerberos)
    if ambiente_ids is not None:
        q = q.filter(Cerberos.ambiente_id.in_(ambiente_ids))
    return render_template('admin/cerberoses.html', cerberoses=q.all())


@app.route('/admin/cerberoses/verificar-atualizacao', methods=['POST'])
@painel_required
def admin_cerberoses_verificar_atualizacao():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    q = db.query(Cerberos)
    if ambiente_ids is not None:
        q = q.filter(Cerberos.ambiente_id.in_(ambiente_ids))
    n = sum(_mqtt().notify_check_update(c, 'cerberos') for c in q.all())
    flash(f'Verificação de atualização enviada para {n} cerberos(es).', 'success')
    return redirect(url_for('admin_cerberoses'))


@app.route('/admin/cerberoses/<int:id>')
@painel_required
def admin_cerberos_ver(id):
    usuario = _current_session_usuario()
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    papel_ids = _ambientes_com_papel(usuario, ('gerente', 'leitor'))
    if papel_ids is not None and c.ambiente_id not in papel_ids:
        abort(403)

    now = datetime.datetime.utcnow()
    sla_24h = _online_pct(
        _intervalos_online(c.mac, now - datetime.timedelta(hours=24), now),
        now - datetime.timedelta(hours=24), now,
    )
    unidade = request.args.get('unidade', 'hora')
    if unidade not in ('hora', 'dia'):
        unidade = 'hora'
    default_qtd = 24 if unidade == 'hora' else 14
    try:
        quantidade = int(request.args.get('quantidade', default_qtd))
    except ValueError:
        quantidade = default_qtd
    quantidade = max(1, min(quantidade, 168 if unidade == 'hora' else 90))
    desde = now - (datetime.timedelta(hours=quantidade) if unidade == 'hora'
                   else datetime.timedelta(days=quantidade))
    sla_serie = _sla_series(c.mac, desde, now, unidade)

    return render_template(
        'admin/cerberos_ver.html', c=c, sla_24h=sla_24h, sla_serie=sla_serie,
        unidade=unidade, quantidade=quantidade,
        pode_editar=pode_gerenciar_dispositivos(usuario, c.ambiente_id),
    )


@app.route('/admin/cerberoses/novo', methods=['GET', 'POST'])
@painel_required
def admin_cerberos_novo():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    if ambiente_ids is not None and not ambiente_ids:
        abort(403)
    ambientes = db.query(Ambiente).all()
    if ambiente_ids is not None:
        ambientes = [a for a in ambientes if a.id in ambiente_ids]
    brokers   = db.query(BrokerMQTT).filter(BrokerMQTT.ativo == True).all()
    if request.method == 'POST':
        f = request.form
        ambiente_id = int(f['ambiente_id'])
        if ambiente_ids is not None and ambiente_id not in ambiente_ids:
            abort(403)
        broker_id = int(f['broker_id']) if f.get('broker_id') else None
        c = Cerberos(nome=f['nome'], mac=f['mac'], chave=f['chave'],
                     ambiente_id=ambiente_id,
                     protocolo=f.get('protocolo', 'rest'),
                     broker_id=broker_id)
        db.add(c)
        db.commit()
        flash('Cerberos criado.', 'success')
        return redirect(url_for('admin_cerberoses'))
    return render_template('admin/cerberos_form.html', cerberos=None,
                           ambientes=ambientes, brokers=brokers)


@app.route('/admin/cerberoses/<int:id>/editar', methods=['GET', 'POST'])
@painel_required
def admin_cerberos_editar(id):
    usuario = _current_session_usuario()
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    ambientes = db.query(Ambiente).all()
    if ambiente_ids is not None:
        ambientes = [a for a in ambientes if a.id in ambiente_ids]
    brokers   = db.query(BrokerMQTT).filter(BrokerMQTT.ativo == True).all()
    if request.method == 'POST':
        f = request.form
        ambiente_id = int(f['ambiente_id'])
        if ambiente_ids is not None and ambiente_id not in ambiente_ids:
            abort(403)
        c.nome       = f['nome']
        c.mac        = f['mac']
        c.chave      = f['chave']
        c.ambiente_id = ambiente_id
        c.protocolo  = f.get('protocolo', 'rest')
        c.broker_id  = int(f['broker_id']) if f.get('broker_id') else None
        db.commit()
        flash('Cerberos atualizado.', 'success')
        return redirect(url_for('admin_cerberoses'))
    return render_template('admin/cerberos_form.html', cerberos=c,
                           ambientes=ambientes, brokers=brokers)


@app.route('/admin/cerberoses/<int:id>/abrir', methods=['POST'])
@painel_required
def admin_cerberos_abrir(id):
    usuario = _current_session_usuario()
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    Tartaro().acionarCerberos(c.mac)
    _mqtt().unlock_cerberos(c)
    _create_audit_log(
        event_type='comando_abertura',
        result='sucesso',
        message=f'Comando manual de abertura enviado para {c.nome}',
        mac=c.mac,
        ambiente=c.ambiente,
        usuario=usuario
    )
    flash(f'Comando de abertura enviado para {c.nome}.', 'success')
    return redirect(url_for('admin_cerberoses'))


@app.route('/admin/cerberoses/<int:id>/verificar-atualizacao', methods=['POST'])
@painel_required
def admin_cerberos_verificar_atualizacao(id):
    usuario = _current_session_usuario()
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    ok = _mqtt().notify_check_update(c, 'cerberos')
    flash(f'Verificação de atualização enviada para {c.nome}.' if ok else
          f'{c.nome} sem broker MQTT conectado — não foi possível notificar.',
          'success' if ok else 'warning')
    return redirect(request.referrer or url_for('admin_cerberoses'))


@app.route('/admin/cerberoses/<int:id>/reiniciar', methods=['POST'])
@painel_required
def admin_cerberos_reiniciar(id):
    usuario = _current_session_usuario()
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    ok = _mqtt().reboot_device(c, 'cerberos')
    _create_audit_log(
        event_type='comando_reiniciar',
        result='sucesso' if ok else 'falha',
        message=f'Comando de reinício enviado para {c.nome}' if ok else
                f'Falha ao enviar comando de reinício para {c.nome} — sem broker MQTT conectado',
        mac=c.mac,
        ambiente=c.ambiente,
        usuario=usuario
    )
    flash(f'Comando de reinício enviado para {c.nome}.' if ok else
          f'{c.nome} sem broker MQTT conectado — não foi possível reiniciar.',
          'success' if ok else 'warning')
    return redirect(request.referrer or url_for('admin_cerberoses'))


@app.route('/admin/cerberoses/<int:id>/excluir', methods=['POST'])
@painel_required
def admin_cerberos_excluir(id):
    usuario = _current_session_usuario()
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    db.delete(c)
    db.commit()
    flash('Cerberos removido.', 'success')
    return redirect(url_for('admin_cerberoses'))


# Carontes ───────────────────────────────────

@app.route('/admin/carontes')
@painel_required
def admin_carontes():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    q = db.query(Caronte)
    if ambiente_ids is not None:
        q = q.filter(Caronte.ambiente_id.in_(ambiente_ids))
    return render_template('admin/carontes.html', carontes=q.all())


@app.route('/admin/carontes/verificar-atualizacao', methods=['POST'])
@painel_required
def admin_carontes_verificar_atualizacao():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    q = db.query(Caronte)
    if ambiente_ids is not None:
        q = q.filter(Caronte.ambiente_id.in_(ambiente_ids))
    n = sum(_mqtt().notify_check_update(c, 'caronte') for c in q.all())
    flash(f'Verificação de atualização enviada para {n} caronte(s).', 'success')
    return redirect(url_for('admin_carontes'))


@app.route('/admin/carontes/<int:id>')
@painel_required
def admin_caronte_ver(id):
    usuario = _current_session_usuario()
    c = db.query(Caronte).filter(Caronte.id == id).first()
    if c is None:
        abort(404)
    papel_ids = _ambientes_com_papel(usuario, ('gerente', 'leitor'))
    if papel_ids is not None and c.ambiente_id not in papel_ids:
        abort(403)

    now = datetime.datetime.utcnow()
    sla_24h = _online_pct(
        _intervalos_online(c.mac, now - datetime.timedelta(hours=24), now),
        now - datetime.timedelta(hours=24), now,
    )
    unidade = request.args.get('unidade', 'hora')
    if unidade not in ('hora', 'dia'):
        unidade = 'hora'
    default_qtd = 24 if unidade == 'hora' else 14
    try:
        quantidade = int(request.args.get('quantidade', default_qtd))
    except ValueError:
        quantidade = default_qtd
    quantidade = max(1, min(quantidade, 168 if unidade == 'hora' else 90))
    desde = now - (datetime.timedelta(hours=quantidade) if unidade == 'hora'
                   else datetime.timedelta(days=quantidade))
    sla_serie = _sla_series(c.mac, desde, now, unidade)

    return render_template(
        'admin/caronte_ver.html', c=c, sla_24h=sla_24h, sla_serie=sla_serie,
        unidade=unidade, quantidade=quantidade,
        pode_editar=pode_gerenciar_dispositivos(usuario, c.ambiente_id),
    )


@app.route('/admin/carontes/novo', methods=['GET', 'POST'])
@painel_required
def admin_caronte_novo():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    if ambiente_ids is not None and not ambiente_ids:
        abort(403)
    ambientes = db.query(Ambiente).all()
    if ambiente_ids is not None:
        ambientes = [a for a in ambientes if a.id in ambiente_ids]
    brokers   = db.query(BrokerMQTT).filter(BrokerMQTT.ativo == True).all()
    if request.method == 'POST':
        f = request.form
        ambiente_id = int(f['ambiente_id'])
        if ambiente_ids is not None and ambiente_id not in ambiente_ids:
            abort(403)
        broker_id = int(f['broker_id']) if f.get('broker_id') else None
        c = Caronte(mac=f['mac'], chave=f['chave'], ambiente_id=ambiente_id,
                    protocolo=f.get('protocolo', 'rest'),
                    broker_id=broker_id)
        db.add(c)
        db.commit()
        flash('Caronte criado.', 'success')
        return redirect(url_for('admin_carontes'))
    return render_template('admin/caronte_form.html', caronte=None,
                           ambientes=ambientes, brokers=brokers)


@app.route('/admin/carontes/<int:id>/editar', methods=['GET', 'POST'])
@painel_required
def admin_caronte_editar(id):
    usuario = _current_session_usuario()
    c = db.query(Caronte).filter(Caronte.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    ambientes = db.query(Ambiente).all()
    if ambiente_ids is not None:
        ambientes = [a for a in ambientes if a.id in ambiente_ids]
    brokers   = db.query(BrokerMQTT).filter(BrokerMQTT.ativo == True).all()
    if request.method == 'POST':
        f = request.form
        ambiente_id = int(f['ambiente_id'])
        if ambiente_ids is not None and ambiente_id not in ambiente_ids:
            abort(403)
        c.mac        = f['mac']
        c.chave      = f['chave']
        c.ambiente_id = ambiente_id
        c.protocolo  = f.get('protocolo', 'rest')
        c.broker_id  = int(f['broker_id']) if f.get('broker_id') else None
        db.commit()
        flash('Caronte atualizado.', 'success')
        return redirect(url_for('admin_carontes'))
    return render_template('admin/caronte_form.html', caronte=c,
                           ambientes=ambientes, brokers=brokers)


@app.route('/admin/carontes/<int:id>/verificar-atualizacao', methods=['POST'])
@painel_required
def admin_caronte_verificar_atualizacao(id):
    usuario = _current_session_usuario()
    c = db.query(Caronte).filter(Caronte.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    ok = _mqtt().notify_check_update(c, 'caronte')
    flash('Verificação de atualização enviada.' if ok else
          'Dispositivo sem broker MQTT conectado — não foi possível notificar.',
          'success' if ok else 'warning')
    return redirect(request.referrer or url_for('admin_carontes'))


@app.route('/admin/carontes/<int:id>/reiniciar', methods=['POST'])
@painel_required
def admin_caronte_reiniciar(id):
    usuario = _current_session_usuario()
    c = db.query(Caronte).filter(Caronte.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    ok = _mqtt().reboot_device(c, 'caronte')
    _create_audit_log(
        event_type='comando_reiniciar',
        result='sucesso' if ok else 'falha',
        message=f'Comando de reinício enviado para {c.mac}' if ok else
                f'Falha ao enviar comando de reinício para {c.mac} — sem broker MQTT conectado',
        mac=c.mac,
        ambiente=c.ambiente,
        usuario=usuario
    )
    flash('Comando de reinício enviado.' if ok else
          'Dispositivo sem broker MQTT conectado — não foi possível reiniciar.',
          'success' if ok else 'warning')
    return redirect(request.referrer or url_for('admin_carontes'))


@app.route('/admin/carontes/<int:id>/excluir', methods=['POST'])
@painel_required
def admin_caronte_excluir(id):
    usuario = _current_session_usuario()
    c = db.query(Caronte).filter(Caronte.id == id).first()
    if c is None:
        abort(404)
    if not pode_gerenciar_dispositivos(usuario, c.ambiente_id):
        abort(403)
    db.delete(c)
    db.commit()
    flash('Caronte removido.', 'success')
    return redirect(url_for('admin_carontes'))


# Brokers MQTT ──────────────────────────────

@app.route('/admin/brokers')
@admin_required
def admin_brokers():
    brokers = db.query(BrokerMQTT).all()
    return render_template('admin/brokers.html', brokers=brokers)


@app.route('/admin/brokers/novo', methods=['GET', 'POST'])
@admin_required
def admin_broker_novo():
    if request.method == 'POST':
        f = request.form
        b = BrokerMQTT(
            nome=f['nome'],
            host=f['host'],
            porta=int(f.get('porta') or 1883),
            usuario=f.get('usuario') or None,
            senha=f.get('senha') or None,
            tls='tls' in f,
            ativo='ativo' in f,
        )
        db.add(b)
        db.commit()
        _mqtt().refresh_broker(b.id)
        flash('Broker MQTT criado.', 'success')
        return redirect(url_for('admin_brokers'))
    return render_template('admin/broker_form.html', broker=None)


@app.route('/admin/brokers/<int:id>/editar', methods=['GET', 'POST'])
@admin_required
def admin_broker_editar(id):
    b = db.query(BrokerMQTT).filter(BrokerMQTT.id == id).first()
    if b is None:
        abort(404)
    if request.method == 'POST':
        f = request.form
        b.nome    = f['nome']
        b.host    = f['host']
        b.porta   = int(f.get('porta') or 1883)
        b.usuario = f.get('usuario') or None
        if f.get('senha'):
            b.senha = f['senha']
        b.tls   = 'tls' in f
        b.ativo = 'ativo' in f
        db.commit()
        if b.ativo:
            _mqtt().refresh_broker(b.id)
        else:
            _mqtt().stop_broker(b.id)
        flash('Broker MQTT atualizado.', 'success')
        return redirect(url_for('admin_brokers'))
    return render_template('admin/broker_form.html', broker=b)


@app.route('/admin/brokers/<int:id>/excluir', methods=['POST'])
@admin_required
def admin_broker_excluir(id):
    b = db.query(BrokerMQTT).filter(BrokerMQTT.id == id).first()
    if b is None:
        abort(404)
    _mqtt().stop_broker(b.id)
    db.delete(b)
    db.commit()
    flash('Broker MQTT removido.', 'success')
    return redirect(url_for('admin_brokers'))


# Usuários ───────────────────────────────────

@app.route('/admin/logs')
@painel_required
def admin_logs():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente', 'leitor'))
    search = request.args.get('search', '').strip()
    event_type = request.args.get('event_type', '').strip()
    result = request.args.get('result', '').strip()
    ambiente_id = request.args.get('ambiente_id', '').strip()
    if ambiente_ids is not None and ambiente_id:
        try:
            if int(ambiente_id) not in ambiente_ids:
                ambiente_id = ''
        except ValueError:
            ambiente_id = ''
    query = _logs_query(search, event_type, result, ambiente_id, ambiente_ids).order_by(AccessLog.timestamp.desc())
    total = query.count()
    logs = query.limit(200).all()
    ambientes = db.query(Ambiente).order_by(Ambiente.nome).all()
    if ambiente_ids is not None:
        ambientes = [a for a in ambientes if a.id in ambiente_ids]
    event_types = [
        ('', 'Todos os eventos'),
        ('tentativa_tag', 'Tentativas por tag'),
        ('tentativa_web', 'Tentativas pelo portal'),
        ('comando_abertura', 'Comandos de abertura'),
        ('login_admin', 'Login admin'),
        ('logout_admin', 'Logout admin'),
        ('login_caronte', 'Login Caronte'),
        ('logout_caronte', 'Logout Caronte'),
        ('api_request', 'Requisicoes da API'),
        ('device_coldstart', 'Coldstart de dispositivo'),
        ('device_offline', 'Dispositivo offline'),
        ('mqtt_heartbeat', 'Heartbeat MQTT'),
        ('mqtt_status', 'Status MQTT'),
        ('mqtt_command', 'Comando MQTT'),
        ('entrada_fisica', 'Entrada fisica'),
    ]
    event_label_map = {v: l for v, l in event_types if v}
    return render_template(
        'admin/logs.html',
        logs=logs,
        search=search,
        event_type=event_type,
        result=result,
        ambiente_id=ambiente_id,
        event_types=event_types,
        event_label_map=event_label_map,
        ambientes=ambientes,
        total=total
    )


def _logs_query(search='', event_type='', result='', ambiente_id='', ambiente_ids=None):
    query = db.query(AccessLog)
    if ambiente_ids is not None:
        query = query.filter(AccessLog.ambiente_id.in_(ambiente_ids))
    if event_type:
        query = query.filter(AccessLog.event_type == event_type)
    if result:
        query = query.filter(AccessLog.result == result)
    if ambiente_id:
        try:
            query = query.filter(AccessLog.ambiente_id == int(ambiente_id))
        except (TypeError, ValueError):
            pass
    if search:
        query = query.filter(or_(
            AccessLog.path.contains(search),
            AccessLog.ip.contains(search),
            AccessLog.mac.contains(search),
            AccessLog.tag.contains(search),
            AccessLog.event_type.contains(search),
            AccessLog.result.contains(search),
            AccessLog.ambiente_nome.contains(search),
            AccessLog.usuario_nome.contains(search),
            AccessLog.payload.contains(search),
            AccessLog.message.contains(search),
        ))
    return query


@app.route('/admin/logs/excluir', methods=['POST'])
@admin_required
def admin_logs_excluir():
    ids = []
    for raw_id in request.form.getlist('log_ids'):
        try:
            ids.append(int(raw_id))
        except (TypeError, ValueError):
            pass

    if not ids:
        flash('Selecione ao menos um log para apagar.', 'warning')
        return redirect(url_for(
            'admin_logs',
            search=request.form.get('search', '').strip(),
            event_type=request.form.get('event_type', '').strip(),
            result=request.form.get('result', '').strip(),
            ambiente_id=request.form.get('ambiente_id', '').strip()
        ))

    deleted = db.query(AccessLog).filter(AccessLog.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    flash(f'{deleted} log(s) apagado(s).', 'success')
    return redirect(url_for(
        'admin_logs',
        search=request.form.get('search', '').strip(),
        event_type=request.form.get('event_type', '').strip(),
        result=request.form.get('result', '').strip(),
        ambiente_id=request.form.get('ambiente_id', '').strip()
    ))


@app.route('/admin/logs/limpar', methods=['POST'])
@admin_required
def admin_logs_limpar():
    search = request.form.get('search', '').strip()
    event_type = request.form.get('event_type', '').strip()
    result = request.form.get('result', '').strip()
    ambiente_id = request.form.get('ambiente_id', '').strip()
    query = _logs_query(search, event_type, result, ambiente_id)
    total = query.count()
    query.delete(synchronize_session=False)
    db.commit()
    flash(f'{total} log(s) apagado(s).', 'success')
    return redirect(url_for('admin_logs'))


def _upsert_tag(usuario, numero):
    numero = (numero or '').strip()
    existing = db.query(TAG).filter(TAG.usuario_id == usuario.id).first()
    if numero:
        if existing:
            existing.numero = numero
        else:
            db.add(TAG(numero=numero, usuario_id=usuario.id))
    elif existing:
        db.delete(existing)


@app.route('/admin/usuarios')
@painel_required
def admin_usuarios():
    usuario = _current_session_usuario()
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente', 'colaborador'))
    if ambiente_ids is None:
        usuarios = db.query(Usuario).all()
    elif not ambiente_ids:
        usuarios = []
    else:
        usuarios = (
            db.query(Usuario)
            .join(Usuario.ambientes)
            .filter(Ambiente.id.in_(ambiente_ids))
            .distinct()
            .all()
        )
    ambientes_gerente_ids = set(_ambientes_com_papel(usuario, ('gerente',)) or [])

    def _pode_editar(u):
        if usuario.admin:
            return True
        if u.admin:
            return False
        u_ids = {a.id for a in u.ambientes} | {p.ambiente_id for p in u.papeis}
        return bool(u_ids & ambientes_gerente_ids)

    def _pode_excluir(u):
        if not _pode_editar(u):
            return False
        if usuario.admin:
            return True
        return not any(p.papel == 'gerente' for p in u.papeis)

    pode_editar_map = {u.id: _pode_editar(u) for u in usuarios}
    pode_excluir_map = {u.id: _pode_excluir(u) for u in usuarios}
    return render_template('admin/usuarios.html', usuarios=usuarios,
                           pode_editar_map=pode_editar_map, pode_excluir_map=pode_excluir_map)


@app.route('/admin/usuarios/novo', methods=['GET', 'POST'])
@painel_required
def admin_usuario_novo():
    usuario = _current_session_usuario()
    ambientes = [a for a in db.query(Ambiente).all() if pode_criar_usuarios(usuario, a.id)]
    if not usuario.admin and not ambientes:
        abort(403)
    ambientes_gerente_ids = set(_ambientes_com_papel(usuario, ('gerente',)) or [])
    if usuario.admin:
        ambientes_gerente_ids = {a.id for a in db.query(Ambiente).all()}
    if request.method == 'POST':
        f = request.form
        is_admin = usuario.admin and 'admin' in f
        senha_raw = f.get('senha', '').strip()
        u = Usuario(nome=f['nome'], matricula=f['matricula'],
                    pin=f['pin'][:4], admin=is_admin,
                    senha=generate_password_hash(senha_raw) if senha_raw else None)
        db.add(u)
        db.flush()
        _upsert_tag(u, f.get('tag', ''))
        amb_ids_escopo = {a.id for a in ambientes}
        amb_ids_form = {int(x) for x in request.form.getlist('ambientes')} & amb_ids_escopo
        for amb_id in amb_ids_form:
            amb = db.query(Ambiente).filter(Ambiente.id == amb_id).first()
            u.ambientes.append(amb)
            papel = f.get(f'papel_{amb_id}', '').strip()
            papeis_validos = ('gerente', 'colaborador', 'leitor') if usuario.admin else ('colaborador', 'leitor')
            if papel in papeis_validos and amb_id in ambientes_gerente_ids:
                db.add(PapelAmbiente(usuario_id=u.id, ambiente_id=amb_id, papel=papel))
        db.commit()
        flash('Usuário criado.', 'success')
        return redirect(url_for('admin_usuarios'))
    return render_template('admin/usuario_form.html', usuario=None, ambientes=ambientes,
                           papeis_atuais={}, pode_papel={a.id: a.id in ambientes_gerente_ids for a in ambientes})


@app.route('/admin/usuarios/<int:id>/editar', methods=['GET', 'POST'])
@painel_required
def admin_usuario_editar(id):
    usuario = _current_session_usuario()
    u = db.query(Usuario).filter(Usuario.id == id).first()
    if u is None:
        abort(404)
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    if ambiente_ids is not None:
        u_ambiente_ids = {a.id for a in u.ambientes} | {p.ambiente_id for p in u.papeis}
        if u.admin or not (u_ambiente_ids & set(ambiente_ids)):
            abort(403)
    ambientes = [a for a in db.query(Ambiente).all() if pode_editar_usuarios(usuario, a.id)]
    ambientes_gerente_ids = {a.id for a in ambientes} if not usuario.admin else {a.id for a in db.query(Ambiente).all()}
    papeis_atuais = {p.ambiente_id: p.papel for p in u.papeis}
    if request.method == 'POST':
        f = request.form
        u.nome = f['nome']
        u.matricula = f['matricula']
        if f.get('pin'):
            u.pin = f['pin'][:4]
        if usuario.admin:
            u.admin = 'admin' in f
        if f.get('senha'):
            u.senha = generate_password_hash(f['senha'])
        _upsert_tag(u, f.get('tag', ''))

        amb_ids_escopo = {a.id for a in ambientes}
        amb_ids_form = {int(x) for x in request.form.getlist('ambientes')} & amb_ids_escopo
        fora_do_escopo = [a for a in u.ambientes if a.id not in amb_ids_escopo]
        dentro = db.query(Ambiente).filter(Ambiente.id.in_(amb_ids_form)).all() if amb_ids_form else []
        u.ambientes = fora_do_escopo + dentro

        papeis_validos = ('gerente', 'colaborador', 'leitor') if usuario.admin else ('colaborador', 'leitor')
        for amb_id in amb_ids_escopo:
            papel = f.get(f'papel_{amb_id}', '').strip()
            pa = db.query(PapelAmbiente).filter_by(usuario_id=u.id, ambiente_id=amb_id).first()
            if papel in papeis_validos and amb_id in ambientes_gerente_ids:
                if pa:
                    pa.papel = papel
                else:
                    db.add(PapelAmbiente(usuario_id=u.id, ambiente_id=amb_id, papel=papel))
            elif pa and amb_id in ambientes_gerente_ids:
                db.delete(pa)
        db.commit()
        flash('Usuário atualizado.', 'success')
        return redirect(url_for('admin_usuarios'))
    return render_template('admin/usuario_form.html', usuario=u, ambientes=ambientes,
                           papeis_atuais=papeis_atuais,
                           pode_papel={a.id: a.id in ambientes_gerente_ids for a in ambientes})


@app.route('/admin/usuarios/<int:id>/excluir', methods=['POST'])
@painel_required
def admin_usuario_excluir(id):
    usuario = _current_session_usuario()
    u = db.query(Usuario).filter(Usuario.id == id).first()
    if u is None:
        abort(404)
    ambiente_ids = _ambientes_com_papel(usuario, ('gerente',))
    if ambiente_ids is not None:
        u_ambiente_ids = {a.id for a in u.ambientes} | {p.ambiente_id for p in u.papeis}
        is_gerente_em_algum_lugar = any(p.papel == 'gerente' for p in u.papeis)
        if u.admin or is_gerente_em_algum_lugar or not (u_ambiente_ids & set(ambiente_ids)):
            abort(403)
    db.delete(u)
    db.commit()
    flash('Usuário removido.', 'success')
    return redirect(url_for('admin_usuarios'))


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=9001, debug=True, use_reloader=False)
