from flask import Flask, render_template, send_file, Response, abort, jsonify, request, url_for, redirect, logging
from flask_bootstrap import Bootstrap
from werkzeug.middleware.proxy_fix import ProxyFix
import json
import requests
import statistics
import datetime
import pandas
import io
import base64
from Tartaro import *

app = Flask(__name__, template_folder="templates")


app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)
Bootstrap(app)

@app.route('/')
def hello():
    api_url_login = "http://laica.ifrn.edu.br/access-ng/auth/login"
    api_url_log = "http://laica.ifrn.edu.br/access-ng/log/topic/Access/10"
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
    return render_template("index.html", estado=estado.split(" ")[1], hora=horaEstado)

@app.route('/Porta')
def Porta():
    api_url_login = "http://laica.ifrn.edu.br/access-ng/auth/login"
    api_url_log = "http://laica.ifrn.edu.br/access-ng/log/topic/Access/10"
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
    return render_template("index.html", estado=estado.split(" ")[1], hora=horaEstado)

@app.route('/Ambiente')
def ambiente():
    #api_url_login = "http://laica.ifrn.edu.br/access-ng/auth/login"
    api_url_log = "https://laica.ifrn.edu.br/access-ng/log?previous=0&next=1&pageSize=100&deviceMac=A0%3AB7%3A65%3A64%3A68%3AC8&type=INFO&order=desc"
    #todo={"registration": "2568824",  "password": "password"}
    headers =  {"Content-Type":"application/json"}
    #response = requests.post(api_url_login, data=json.dumps(todo), headers=headers)
    APIToken = response.json()['accessToken']
    #headers = {'Authorization': 'Token ' + APIToken}
    response = requests.get(api_url_log, headers=headers)
    resposta = response.json()
    listaTemp = []
    listaHumi = []
    lista = []
    for item in resposta:
        listaTemp.append(float(item['message'].split(',')[0].split('=')[1]))
        listaHumi.append(float(item['message'].split(',')[1].split('=')[1]))
        lista.append(AmbienteTempHumi(item))
    import numpy as np
    np.set_printoptions(formatter={'float': lambda x: "{0:0.3f}".format(x)})
    mediaTemp = np.around(np.nanmean(listaTemp), 2)
    mediaHumi = np.around(np.nanmean(listaHumi), 2)
    import matplotlib.pyplot as plt
    tempBuffer = io.BytesIO()
    humiBuffer = io.BytesIO()
    df = pandas.json_normalize(json.loads(str(lista)))

    plt.clf()
    plt.title("Temperatura")
    axa = plt.gca()
    df.plot(x='timestamp', y='temperature', ax=axa, color='red', marker='o')
    plt.xticks(rotation=25)
    plt.savefig(tempBuffer, format = 'png')
    chartTemp = base64.b64encode(tempBuffer.getvalue()).decode()

    plt.clf()
    plt.title("Umidade")
    axa = plt.gca()
    df.plot(x='timestamp', y='humidity', ax=axa,color='green', marker='v')
    plt.xticks(rotation=25)
    plt.savefig(humiBuffer, format = 'png')
    chartHumi = base64.b64encode(humiBuffer.getvalue()).decode()
    return render_template("ambiente.html", mediaTemp=mediaTemp, mediaHumi=mediaHumi, chartTemp=chartTemp, chartHumi=chartHumi)

@app.route('/caronte/autenticarTag', methods = ['POST'])
def autenticar():
    content = request.json
    tag = content['tag']
    mac = content['mac']
    chave = content['chave']
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

class AmbienteTempHumi:
    def __init__(self, entrada):
        self.temp = float(entrada['message'].split(',')[0].split('=')[1])
        self.humi = float(entrada['message'].split(',')[1].split('=')[1])
        data = datetime.datetime.strptime(entrada['createdAt'],"%Y-%m-%dT%H:%M:%S.%fZ")
        self.timestamp = str(data.__format__('%Y-%m-%d %H:%M:%S'))
    def __str__(self):
        return json.dumps(dict(self), ensure_ascii=False)
    def __iter__(self):
        yield from {
            "temperature": self.temp,
            "humidity": self.humi,
            "timestamp": self.timestamp,
        }.items()
    def __str__(self):
        return json.dumps(dict(self), ensure_ascii=False)

    def __repr__(self):
        return self.__str__()

    def to_json(self):
        return self.__str__()
    
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=3002, debug=True)