from flask import Flask, render_template, send_file, Response, abort, jsonify, request, url_for, redirect, logging
from flask_bootstrap import Bootstrap

app = Flask(__name__, template_folder="templates")
Bootstrap(app)

@app.route('/')
def hello():
    import json
    import requests
    api_url_login = "http://laica.ifrn.edu.br/access-ng/auth/login"
    api_url_log = "http://laica.ifrn.edu.br/access-ng/log"
    todo={"registration": "2568824",  "password": "password"}
    headers =  {"Content-Type":"application/json"}
    response = requests.post(api_url_login, data=json.dumps(todo), headers=headers)
    APIToken = response.json()['accessToken']
    headers = {'Authorization': 'Token ' + APIToken}
    response = requests.get(api_url_log, headers=headers)
    resposta = response.json()
    estado = resposta[-1]['message']
    horaEstado = resposta[-1]['createdAt']
    #print ("A porta está " + estado.split(" ")[1] + " desde às " + horaEstado.split(".")[0] + "GMT")
    estadoAnt = resposta[-2]['message']
    horaEstadoAnt = resposta[-2]['createdAt']
    #print ("A ultima vez que ela ficou " + estadoAnt.split(" ")[1] + " foi às " + horaEstadoAnt.split(".")[0] + "GMT")

    return render_template("index.html", estado=estado.split(" ")[1])

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=9001, debug=True)