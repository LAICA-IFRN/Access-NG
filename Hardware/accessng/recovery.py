"""Decide se o dispositivo está saudável e aciona o modo de recuperação
(AP + portal de provisionamento) quando não está.

is_crash_looping() generaliza o guard que cada firmware tinha embutido em
_ota_boot_guard() - lá, só disparava quando havia um update OTA pendente
(ota_pending.txt). Aqui, qualquer sequência de boots sem confirmar saúde
conta, não importa a causa (config ruim, bug antigo, biblioteca ausente,
update ruim).
"""


def is_crash_looping(state, threshold=3):
    return state.get("boot_count", 0) >= threshold and not state.get("last_boot_ok", False)


def enter(device_type, mac_suffix, defaults, sensitive_keys, reason):
    """Ponto de entrada único do modo recovery. Nunca retorna em operação
    normal: fica servindo o AP+portal até um POST válido gravar
    config.json e reiniciar. Import de provisioning é lazy de propósito -
    só é puxado quando realmente entra em recovery."""
    print("[Recovery] Entrando em modo provisionamento (%s)" % reason)
    from accessng import provisioning
    provisioning.start(device_type, mac_suffix, defaults, sensitive_keys)


# Extensão futura (NÃO implementada nesta fase): um segundo gatilho por
# botão físico poderia somar-se aqui - ver roteiro futuro no plano de
# redesign do OTA (botão já reservado no FECHO, GPIO5, sem lógica ainda).
