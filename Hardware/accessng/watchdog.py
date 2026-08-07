"""Watchdog de hardware - rede de segurança contra travamentos de
verdade (laço infinito, driver de rede preso, socket bloqueado para
sempre), diferente do que boot.py/recovery já cobrem (exceções
capturadas + boot loop). Se o watchdog não for alimentado (feed()) a
tempo, o hardware força um reset sozinho - funciona mesmo se o
interpretador MicroPython estiver travado numa chamada bloqueante, já
que é um temporizador de hardware, independente do laço de eventos do
Python.

Timeout deliberadamente curto (8s): o RP2040 (BitDogLab) limita o
watchdog a ~8.3s no hardware - esse módulo é compartilhado pelos 4
firmwares, então o timeout precisa respeitar o menor denominador comum.
Por isso quem faz operação potencialmente longa (conectar Wi-Fi, baixar
arquivo, aceitar conexão no portal de provisionamento) precisa chamar
feed() periodicamente DURANTE a espera, não só uma vez no início.

_wdt é um singleton de módulo (não uma instância por chamador) de
propósito: boot.py arma uma vez (arm()) e o objeto persiste em
sys.modules['accessng.watchdog'] durante toda a transição para main.py
- MicroPython não reseta o cache de módulos entre boot.py e main.py, só
executa os dois em sequência no mesmo processo.
"""

_wdt = None


def arm(timeout_ms=8000):
    """Arma o watchdog. Uma vez armado, a maioria dos ports do
    MicroPython NÃO permite desarmar - é assim de propósito (senão um
    bug poderia simplesmente desligar a proteção). Chamar mais de uma
    vez é seguro (não rearma, só reporta se já está ativo)."""
    global _wdt
    if _wdt is not None:
        return _wdt
    try:
        import machine
        _wdt = machine.WDT(timeout=timeout_ms)
        print("[WDT] Armado (timeout=%dms)" % timeout_ms)
    except Exception as e:
        print("[WDT] Não foi possível armar:", e)
        _wdt = None
    return _wdt


def feed():
    """No-op silencioso se o watchdog não foi armado (ex.: rodando fora
    do device real, ou arm() falhou) - sempre seguro chamar em qualquer
    lugar sem checar antes se está ativo."""
    if _wdt is not None:
        try:
            _wdt.feed()
        except Exception:
            pass
