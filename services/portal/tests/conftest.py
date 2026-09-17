"""Fixtures compartilhadas da suíte.

`apps.events.context` guarda correlação/tenant em contextvars — deliberado,
pra instrumentar o pipeline sem mudar assinatura de função (ver o docstring
do módulo). Efeito colateral em teste: contextvars são globais por processo,
não por teste, então um teste que chama `set_tenant_id`/`set_correlation_id`
e não limpa depois vaza esse valor para o próximo teste que rodar no mesmo
processo — se esse próximo teste emitir um evento sem tenant explícito,
`emit()` herda um tenant de uma organização já desfeita (rollback da
transação do teste anterior) e a gravação vira FK inválida.
"""

import pytest


@pytest.fixture(autouse=True)
def _limpar_contexto_de_eventos():
    from apps.events.context import limpar

    limpar()
    yield
    limpar()
