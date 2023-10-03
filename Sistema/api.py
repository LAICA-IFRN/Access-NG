from Tartaro import *
from flask import Flask, render_template, send_file, Response, abort, jsonify, request, url_for, redirect, logging
from flask_bootstrap import Bootstrap

app = Flask(__name__, template_folder="templates")
Bootstrap(app)

@app.route('/')
def hello():
    ambientes = db.query(Ambiente).all()
    qtd = len(ambientes)
    return render_template("index.html", count=qtd)

@app.route('/caronte/autenticarTag', methods = ['POST', 'GET'])
def autenticar():
    if request.method == 'POST':
        tag = request.form['tag']
        mac = request.form['mac']
        chave = request.form['chave']
    else:
        tag = request.args.get('tag')
        mac = request.args.get('mac')
        chave = request.args.get('chave')
        acionar = Tartaro().autenticarTAG(tag=tag,senha=chave,mac=mac)
        if acionar:
            Tartaro.acionarCerberos(mac)
    return jsonify('Allow : {}'.format(acionar))

@app.route('/cerberos/acionar')
def jobs():
    mac = request.form['mac']
    return jsonify('Allow : {}'.format(Tartaro().verificarAcionamento(mac=mac)))

@app.route('/service/microcontrollers/microcontrollers/esp8266/is-alive/')
def compatibility():
    mac = request.form['mac']
    return jsonify('Allow : {}'.format(mac))


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=9001, debug=True)