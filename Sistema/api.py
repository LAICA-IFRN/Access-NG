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

@app.route('/caronte/autenticarTag', methods = ['POST'])
def autenticar():
    content = request.json
    tag = content['tag']
    mac = content['mac']
    chave =content['chave']
    acionar = Tartaro().autenticarTAG(tag=tag,senha=chave,mac=mac)
    return jsonify('Allow : {}'.format(acionar))

@app.route('/service/enviroments/enviroments/access/', methods=['POST'])
def jobs():
    retorno = {}
    content = request.json
    mac = content['mac']
    retorno['Allow'] = Tartaro().verificarAcionamento(mac=mac)
    return jsonify(retorno)

@app.route('/service/microcontrollers/microcontrollers/esp8266/is-alive/', methods=['POST'])
def compatibility():
    content = request.json
    mac = content['mac']
    return jsonify('Received : {}'.format(mac))


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=9001, debug=True)