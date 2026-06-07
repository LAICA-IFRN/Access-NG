from Tartaro import *
from flask import (Flask, render_template, jsonify, request,
                   session, redirect, url_for, flash, abort)
from flask_bootstrap import Bootstrap
from functools import wraps
from sqlalchemy import or_
import datetime
import threading
import time
import os
import json

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get('SECRET_KEY', 'tartaro-dev-key-change-in-prod')
Bootstrap(app)

OFFLINE_THRESHOLD = 30  # seconds without contact → device is offline


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
            timestamp=datetime.datetime.now(datetime.UTC),
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
            timestamp=datetime.datetime.now(datetime.UTC),
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
            timestamp=datetime.datetime.now(datetime.UTC),
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
        admin = db.query(Usuario).filter(Usuario.id == uid, Usuario.admin == True).first()
        if not admin:
            session.pop('admin_id', None)
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


def caronte_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('caronte_login'))
        return f(*args, **kwargs)
    return decorated


# ── Device helpers ───────────────────────────────────────────────────────────

def _touch_device(mac: str):
    now = datetime.datetime.now(datetime.UTC)
    updated = False
    for model in (Cerberos, Caronte):
        device = db.query(model).filter(model.mac.ilike(mac)).first()
        if device:
            device.last_seen = now
            device.status = 'online'
            updated = True
    if updated:
        db.commit()


def _offline_monitor():
    while True:
        time.sleep(15)
        threshold = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=OFFLINE_THRESHOLD)
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
        return jsonify({'status': 'unknown', 'mac': mac}), 404
    device.coldstart_at = now
    device.last_seen = now
    device.status = 'online'
    db.commit()
    device_label = getattr(device, 'nome', mac)
    _create_audit_log(
        event_type='device_coldstart',
        result='sucesso',
        message=f'{device_type} iniciado: {device_label} ({mac})',
        mac=mac,
        ambiente=device.ambiente
    )
    return jsonify({'status': 'ok', 'device': device_type, 'mac': mac})


@app.route('/device/heartbeat', methods=['POST'])
def heartbeat():
    mac = (request.json or {}).get('mac')
    if not mac:
        return jsonify({'error': 'mac required'}), 400
    _touch_device(mac)
    return jsonify({'received': mac})


@app.route('/device/command', methods=['POST'])
def device_command():
    content = request.json or {}
    mac = content.get('mac')
    if not mac:
        return jsonify({'error': 'mac required'}), 400

    cerberos = db.query(Cerberos).filter(Cerberos.mac.ilike(mac)).first()
    if cerberos is None:
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
    now = datetime.datetime.now(datetime.UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    access_types = ['tentativa_tag', 'tentativa_web', 'comando_abertura']

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
        pin = request.form.get('pin', '').strip()
        usuario = db.query(Usuario).filter(
            Usuario.matricula == matricula,
            Usuario.pin == pin,
            Usuario.admin == True
        ).first()
        if not usuario:
            _create_audit_log(
                event_type='login_admin',
                result='falha',
                message='Credenciais invalidas ou sem permissao de admin',
                payload={'matricula': matricula}
            )
            flash('Credenciais inválidas ou sem permissão de admin.', 'danger')
            return redirect(url_for('admin_login'))
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


@app.route('/admin/')
@admin_required
def admin_index():
    stats = {
        'ambientes': db.query(Ambiente).count(),
        'cerberoses': db.query(Cerberos).count(),
        'carontes': db.query(Caronte).count(),
        'usuarios': db.query(Usuario).count(),
        'logs': db.query(AccessLog).count(),
    }
    recent_device_events = (
        db.query(AccessLog)
        .filter(AccessLog.event_type.in_(['device_coldstart', 'device_offline']))
        .order_by(AccessLog.timestamp.desc())
        .limit(15)
        .all()
    )
    recent_access_events = (
        db.query(AccessLog)
        .filter(AccessLog.event_type.in_(['tentativa_tag', 'tentativa_web', 'comando_abertura']))
        .order_by(AccessLog.timestamp.desc())
        .limit(10)
        .all()
    )
    return render_template(
        'admin/index.html',
        stats=stats,
        recent_device_events=recent_device_events,
        recent_access_events=recent_access_events
    )


# Ambientes ──────────────────────────────────

@app.route('/admin/ambientes')
@admin_required
def admin_ambientes():
    ambientes = db.query(Ambiente).all()
    return render_template('admin/ambientes.html', ambientes=ambientes)


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
@admin_required
def admin_cerberoses():
    cerberoses = db.query(Cerberos).all()
    return render_template('admin/cerberoses.html', cerberoses=cerberoses)


@app.route('/admin/cerberoses/novo', methods=['GET', 'POST'])
@admin_required
def admin_cerberos_novo():
    ambientes = db.query(Ambiente).all()
    if request.method == 'POST':
        f = request.form
        c = Cerberos(nome=f['nome'], mac=f['mac'], chave=f['chave'],
                     ambiente_id=int(f['ambiente_id']))
        db.add(c)
        db.commit()
        flash('Cerberos criado.', 'success')
        return redirect(url_for('admin_cerberoses'))
    return render_template('admin/cerberos_form.html', cerberos=None, ambientes=ambientes)


@app.route('/admin/cerberoses/<int:id>/editar', methods=['GET', 'POST'])
@admin_required
def admin_cerberos_editar(id):
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    ambientes = db.query(Ambiente).all()
    if request.method == 'POST':
        f = request.form
        c.nome = f['nome']
        c.mac = f['mac']
        c.chave = f['chave']
        c.ambiente_id = int(f['ambiente_id'])
        db.commit()
        flash('Cerberos atualizado.', 'success')
        return redirect(url_for('admin_cerberoses'))
    return render_template('admin/cerberos_form.html', cerberos=c, ambientes=ambientes)


@app.route('/admin/cerberoses/<int:id>/abrir', methods=['POST'])
@admin_required
def admin_cerberos_abrir(id):
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    Tartaro().acionarCerberos(c.mac)
    usuario = None
    if session.get('admin_id'):
        usuario = db.query(Usuario).filter(Usuario.id == session.get('admin_id')).first()
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


@app.route('/admin/cerberoses/<int:id>/excluir', methods=['POST'])
@admin_required
def admin_cerberos_excluir(id):
    c = db.query(Cerberos).filter(Cerberos.id == id).first()
    if c is None:
        abort(404)
    db.delete(c)
    db.commit()
    flash('Cerberos removido.', 'success')
    return redirect(url_for('admin_cerberoses'))


# Carontes ───────────────────────────────────

@app.route('/admin/carontes')
@admin_required
def admin_carontes():
    carontes = db.query(Caronte).all()
    return render_template('admin/carontes.html', carontes=carontes)


@app.route('/admin/carontes/novo', methods=['GET', 'POST'])
@admin_required
def admin_caronte_novo():
    ambientes = db.query(Ambiente).all()
    if request.method == 'POST':
        f = request.form
        c = Caronte(mac=f['mac'], chave=f['chave'], ambiente_id=int(f['ambiente_id']))
        db.add(c)
        db.commit()
        flash('Caronte criado.', 'success')
        return redirect(url_for('admin_carontes'))
    return render_template('admin/caronte_form.html', caronte=None, ambientes=ambientes)


@app.route('/admin/carontes/<int:id>/editar', methods=['GET', 'POST'])
@admin_required
def admin_caronte_editar(id):
    c = db.query(Caronte).filter(Caronte.id == id).first()
    if c is None:
        abort(404)
    ambientes = db.query(Ambiente).all()
    if request.method == 'POST':
        f = request.form
        c.mac = f['mac']
        c.chave = f['chave']
        c.ambiente_id = int(f['ambiente_id'])
        db.commit()
        flash('Caronte atualizado.', 'success')
        return redirect(url_for('admin_carontes'))
    return render_template('admin/caronte_form.html', caronte=c, ambientes=ambientes)


@app.route('/admin/carontes/<int:id>/excluir', methods=['POST'])
@admin_required
def admin_caronte_excluir(id):
    c = db.query(Caronte).filter(Caronte.id == id).first()
    if c is None:
        abort(404)
    db.delete(c)
    db.commit()
    flash('Caronte removido.', 'success')
    return redirect(url_for('admin_carontes'))


# Usuários ───────────────────────────────────

@app.route('/admin/logs')
@admin_required
def admin_logs():
    search = request.args.get('search', '').strip()
    event_type = request.args.get('event_type', '').strip()
    result = request.args.get('result', '').strip()
    ambiente_id = request.args.get('ambiente_id', '').strip()
    query = _logs_query(search, event_type, result, ambiente_id).order_by(AccessLog.timestamp.desc())
    total = query.count()
    logs = query.limit(200).all()
    ambientes = db.query(Ambiente).order_by(Ambiente.nome).all()
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


def _logs_query(search='', event_type='', result='', ambiente_id=''):
    query = db.query(AccessLog)
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


@app.route('/admin/usuarios')
@admin_required
def admin_usuarios():
    usuarios = db.query(Usuario).all()
    return render_template('admin/usuarios.html', usuarios=usuarios)


@app.route('/admin/usuarios/novo', methods=['GET', 'POST'])
@admin_required
def admin_usuario_novo():
    ambientes = db.query(Ambiente).all()
    if request.method == 'POST':
        f = request.form
        u = Usuario(nome=f['nome'], matricula=f['matricula'],
                    pin=f['pin'][:4], admin='admin' in f)
        db.add(u)
        db.flush()
        tag_numero = f.get('tag', '').strip()
        if tag_numero:
            db.add(TAG(numero=tag_numero, usuario_id=u.id))
        for amb_id in request.form.getlist('ambientes'):
            amb = db.query(Ambiente).filter(Ambiente.id == int(amb_id)).first()
            if amb:
                u.ambientes.append(amb)
        db.commit()
        flash('Usuário criado.', 'success')
        return redirect(url_for('admin_usuarios'))
    return render_template('admin/usuario_form.html', usuario=None, ambientes=ambientes)


@app.route('/admin/usuarios/<int:id>/editar', methods=['GET', 'POST'])
@admin_required
def admin_usuario_editar(id):
    u = db.query(Usuario).filter(Usuario.id == id).first()
    if u is None:
        abort(404)
    ambientes = db.query(Ambiente).all()
    if request.method == 'POST':
        f = request.form
        u.nome = f['nome']
        u.matricula = f['matricula']
        if f.get('pin'):
            u.pin = f['pin'][:4]
        u.admin = 'admin' in f
        tag_numero = f.get('tag', '').strip()
        existing_tag = db.query(TAG).filter(TAG.usuario_id == u.id).first()
        if tag_numero:
            if existing_tag:
                existing_tag.numero = tag_numero
            else:
                db.add(TAG(numero=tag_numero, usuario_id=u.id))
        elif existing_tag:
            db.delete(existing_tag)
        u.ambientes = []
        for amb_id in request.form.getlist('ambientes'):
            amb = db.query(Ambiente).filter(Ambiente.id == int(amb_id)).first()
            if amb:
                u.ambientes.append(amb)
        db.commit()
        flash('Usuário atualizado.', 'success')
        return redirect(url_for('admin_usuarios'))
    return render_template('admin/usuario_form.html', usuario=u, ambientes=ambientes)


@app.route('/admin/usuarios/<int:id>/excluir', methods=['POST'])
@admin_required
def admin_usuario_excluir(id):
    u = db.query(Usuario).filter(Usuario.id == id).first()
    if u is None:
        abort(404)
    db.delete(u)
    db.commit()
    flash('Usuário removido.', 'success')
    return redirect(url_for('admin_usuarios'))


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=9001, debug=True)
