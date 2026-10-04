"""Pull periódico de estado dos pares próprios — ADR 011, canal de controle.

Cada instância **puxa** o estado dos pares (`GET /federacao/controle/v1/estado/`, assinado) e guarda o
último estado conhecido, que alimenta a tela do cluster e o roteador de LLM. Só pares `proprio` e
`confirmado` são consultados: o inventário não é de quem é só um terceiro.

- **Falha isolada por par:** um par lento ou fora do ar não atrasa nem derruba os outros.
- **Backoff:** depois de falhar, o par é tentado de novo só após `min(300, 30 · 2^falhas)` segundos.
- **Eventos só nas transições** (par ficou inacessível / voltou): um pull que deu certo não vira evento.
- O último estado **não é apagado** quando o par some: a tela mostra "último estado conhecido".
"""

import logging

from django.utils import timezone

from apps.events.emit import emit
from apps.federacao.canal import ParInacessivel, RespostaInvalida, chamar_par

from .models import Maquina
from .projecao import aplicar_estado_remoto

logger = logging.getLogger(__name__)

CAMINHO_ESTADO = "/federacao/controle/v1/estado/"
BACKOFF_BASE_S = 30
BACKOFF_MAXIMO_S = 300


def espera_do_backoff(falhas: int) -> int:
    return 0 if falhas <= 0 else min(BACKOFF_MAXIMO_S, BACKOFF_BASE_S * 2 ** min(falhas, 10))


def _registrar_falha(par: Maquina, motivo: str, agora) -> None:
    primeira = par.pull_falhas == 0
    par.pull_falhas += 1
    par.ultima_tentativa_em = agora
    par.ultimo_pull_erro = motivo[:200]
    par.save(update_fields=["pull_falhas", "ultima_tentativa_em", "ultimo_pull_erro"])
    if primeira:
        emit("cluster.par", "falhou", source="portal", subject_type="maquina", subject_id=par.id,
             tenant_id=par.organizacao_id, message=f"par {par.apelido} ficou inacessível", payload={"motivo": motivo[:200]})


def puxar_par(par: Maquina, *, agora=None, chamar=chamar_par) -> bool:
    """Puxa o estado de um par. `True` se deu certo. Nunca levanta."""
    agora = agora or timezone.now()
    voltou = par.pull_falhas > 0
    try:
        resposta = chamar(par, "GET", CAMINHO_ESTADO)
        if resposta.status != 200 or not resposta.assinada:
            raise ParInacessivel(f"HTTP {resposta.status}")
        aplicar_estado_remoto(par, resposta.json, agora=agora)
    except (ParInacessivel, RespostaInvalida) as exc:
        _registrar_falha(par, f"{type(exc).__name__}: {exc}", agora)
        return False
    except Exception as exc:  # um par com resposta estranha nunca derruba os demais
        logger.exception("falha inesperada ao puxar o par %s", par.id)
        _registrar_falha(par, f"{type(exc).__name__}", agora)
        return False
    if voltou:
        emit("cluster.par", "ok", source="portal", subject_type="maquina", subject_id=par.id,
             tenant_id=par.organizacao_id, message=f"par {par.apelido} voltou")
    return True


def puxar_pares(*, agora=None, chamar=chamar_par) -> dict:
    """Puxa todos os pares próprios confirmados que já passaram do backoff. Devolve um resumo."""
    agora = agora or timezone.now()
    resumo = {"consultados": 0, "ok": 0, "falhas": 0, "adiados": 0}
    pares = Maquina.objects.filter(
        eh_local=False, ativa=True, tipo=Maquina.Tipo.PROPRIO, estado=Maquina.Estado.CONFIRMADO,
    ).select_related("organizacao")
    for par in pares:
        if par.pull_falhas and par.ultima_tentativa_em:
            if (agora - par.ultima_tentativa_em).total_seconds() < espera_do_backoff(par.pull_falhas):
                resumo["adiados"] += 1
                continue
        resumo["consultados"] += 1
        resumo["ok" if puxar_par(par, agora=agora, chamar=chamar) else "falhas"] += 1
    return resumo
