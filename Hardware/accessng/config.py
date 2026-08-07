"""Leitura/escrita de config.json e boot_state.json - compartilhado pelos
4 firmwares MQTT (Caronte, FECHO, Cerberos enxuto, BitDogLab).

Ao contrário do cfg()/_DEFAULTS/_cfg_file que cada firmware tinha
individualmente, aqui os defaults são sempre passados por parâmetro -
este módulo não conhece o schema de nenhum dispositivo específico.
"""

import json
import os


def load(path="config.json"):
    """Retorna (cfg_dict, ok). ok=False sempre que o arquivo não existe OU
    não parseia como um objeto JSON - usado por boot.py para decidir
    recovery, ANTES de qualquer fallback para defaults (fallback por chave
    é responsabilidade de get(), não de load())."""
    try:
        with open(path) as f:
            data = json.load(f)
        return (data, True) if isinstance(data, dict) else ({}, False)
    except Exception:
        return {}, False


def get(cfg_file, defaults, key):
    """Valor de uma chave em cfg_file, com fallback para defaults[key] e
    coerção pro tipo do default (exceto listas, que passam direto)."""
    default = defaults[key]
    value = cfg_file.get(key, default)
    if isinstance(default, list):
        return value
    return type(default)(value)


def save(cfg_dict, path="config.json"):
    _atomic_write(path, json.dumps(cfg_dict))


_STATE_PATH = "boot_state.json"
_STATE_DEFAULT = {
    "boot_count": 0,
    "last_boot_ok": False,
    "current_version": None,
    "previous_version": None,
    "pending_update": False,
}


def load_state(path=_STATE_PATH):
    try:
        with open(path) as f:
            state = json.load(f)
        merged = dict(_STATE_DEFAULT)
        merged.update(state)
        return merged
    except Exception:
        return dict(_STATE_DEFAULT)


def save_state(state, path=_STATE_PATH):
    _atomic_write(path, json.dumps(state))


def _atomic_write(path, text):
    """Grava em <path>.tmp e renomeia por cima - evita um config.json/
    boot_state.json truncado se a energia cair no meio da escrita."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.rename(tmp, path)
