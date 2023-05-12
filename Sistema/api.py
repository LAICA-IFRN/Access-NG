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
        ip = request.form['ip']
        chave = request.form['chave']
    else:
        tag = request.args.get('tag')
        ip = request.args.get('ip')
        chave = request.args.get('chave')
    return jsonify('Allow : {}'.format(Tartaro().autenticarTAG(tag=tag,senha=chave,ip=ip)))

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=9001, debug=True)