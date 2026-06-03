from Tartaro import *
from flask import (Flask, render_template, jsonify, request,
                   session, redirect, url_for, flash)
from flask_bootstrap import Bootstrap
from functools import wraps
import datetime
import threading
import time
import os

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get('SECRET_KEY', 'tartaro-dev-key-change-in-prod')
Bootstrap(app)

OFFLINE_THRESHOLD = 30  # seconds without contact → device is offline


# ── Auth decorators ──────────────────────────────────────────────────────────

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
    now = datetime.datetime.utcnow()
    device = db.query(Cerberos).filter(Cerberos.mac == mac).first()
    if device is None:
        device = db.query(Caronte).filter(Caronte.mac == mac).first()
    if device:
        device.last_seen = now
        device.status = 'online'
        db.commit()


def _offline_monitor():
    while True:
        time.sleep(15)
        threshold = datetime.datetime.utcnow() - datetime.timedelta(seconds=OFFLINE_THRESHOLD)
        try:
            for model in (Cerberos, Caronte):
                stale = db.query(model).filter(
                    model.status == 'online',
                    model.last_seen != None,
                    model.last_seen < threshold
                ).all()
                for d in stale:
                    d.status = 'offline'
            db.commit()
        except Exception:
            db.rollback()


threading.Thread(target=_offline_monitor, daemon=True).start()


# ── Existing IoT endpoints (backward-compatible) ─────────────────────────────

@app.route('/')
def hello():
    ambientes = db.query(Ambiente).all()
    return render_template("index.html", count=len(ambientes))


@app.route('/caronte/autenticarTag', methods=['POST'])
def autenticar():
    c = request.json
    _touch_device(c['mac'])
    acionar = Tartaro().autenticarTAG(tag=c['tag'], senha=c['chave'], mac=c['mac'])
    return jsonify({'Allow': str(acionar)})


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
    device = db.query(Cerberos).filter(Cerberos.mac == mac).first()
    device_type = 'cerberos'
    if device is None:
        device = db.query(Caronte).filter(Caronte.mac == mac).first()
        device_type = 'caronte'
    if device is None:
        return jsonify({'status': 'unknown', 'mac': mac}), 404
    device.coldstart_at = now
    device.last_seen = now
    device.status = 'online'
    db.commit()
    return jsonify({'status': 'ok', 'device': device_type, 'mac': mac})


@app.route('/device/heartbeat', methods=['POST'])
def heartbeat():
    mac = (request.json or {}).get('mac')
    if not mac:
        return jsonify({'error': 'mac required'}), 400
    _touch_device(mac)
    return jsonify({'received': mac})


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
        return redirect(url_for('caronte_portal'))
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
        flash('Matrícula ou PIN incorretos.', 'danger')
        return redirect(url_for('caronte_login'))
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
    result = [{'id': a.id, 'nome': a.nome, 'local': a.local} for a in proximos]
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
        return jsonify({'error': 'sessão inválida'}), 401

    # Validate geolocation again server-side
    ambiente = db.query(Ambiente).filter(Ambiente.id == ambiente_id).first()
    if not ambiente:
        return jsonify({'allow': False, 'motivo': 'Ambiente não encontrado'}), 404

    if ambiente.latitude is not None and ambiente.longitude is not None:
        from Tartaro import _distancia_metros
        raio = ambiente.raio_metros or 50
        dist = _distancia_metros(lat, lon, ambiente.latitude, ambiente.longitude)
        if dist > raio:
            return jsonify({'allow': False, 'motivo': f'Fora do raio ({dist:.0f}m > {raio}m)'})

    ok = Tartaro().autenticarWeb(
        matricula=usuario.matricula,
        pin=usuario.pin,
        ambiente_id=ambiente_id
    )
    if ok:
        return jsonify({'allow': True})
    return jsonify({'allow': False, 'motivo': 'Sem permissão para este ambiente'})


@app.route('/caronte/logout')
def caronte_logout():
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
            flash('Credenciais inválidas ou sem permissão de admin.', 'danger')
            return redirect(url_for('admin_login'))
        session['admin_id'] = usuario.id
        return redirect(url_for('admin_index'))
    return render_template('admin/login.html')


@app.route('/admin/logout')
def admin_logout():
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
    }
    return render_template('admin/index.html', stats=stats)


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
    amb = db.query(Ambiente).filter(Ambiente.id == id).first_or_404()
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
    amb = db.query(Ambiente).filter(Ambiente.id == id).first_or_404()
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
    c = db.query(Cerberos).filter(Cerberos.id == id).first_or_404()
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


@app.route('/admin/cerberoses/<int:id>/excluir', methods=['POST'])
@admin_required
def admin_cerberos_excluir(id):
    c = db.query(Cerberos).filter(Cerberos.id == id).first_or_404()
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
    c = db.query(Caronte).filter(Caronte.id == id).first_or_404()
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
    c = db.query(Caronte).filter(Caronte.id == id).first_or_404()
    db.delete(c)
    db.commit()
    flash('Caronte removido.', 'success')
    return redirect(url_for('admin_carontes'))


# Usuários ───────────────────────────────────

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
    u = db.query(Usuario).filter(Usuario.id == id).first_or_404()
    ambientes = db.query(Ambiente).all()
    if request.method == 'POST':
        f = request.form
        u.nome = f['nome']
        u.matricula = f['matricula']
        if f.get('pin'):
            u.pin = f['pin'][:4]
        u.admin = 'admin' in f
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
    u = db.query(Usuario).filter(Usuario.id == id).first_or_404()
    db.delete(u)
    db.commit()
    flash('Usuário removido.', 'success')
    return redirect(url_for('admin_usuarios'))


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=9001, debug=True)
