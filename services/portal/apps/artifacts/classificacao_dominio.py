"""Classificação por domínio de origem na captura.

Uma `RegraClassificacaoDominio` diz "tudo capturado de `bancodobrasil.com.br` é
`confidencial`". Ela é aplicada **na criação do artefato** (a API interna que o
orchestrator chama) e obedece a três regras, vindas de `docs/seguranca/classificacao.md`:

- **só sobe o nível**: o resultado é o maior entre o que o usuário escolheu na
  extensão e o da regra; nunca rebaixa. Dado sem classificação vale `restrito`;
- **casa por sufixo de rótulo**: `bancodobrasil.com.br` cobre `www.` e `login.`,
  mas não `meubancodobrasil.com.br` nem `bancodobrasil.com.br.outro.com`;
- **não reclassifica o que já existe**: a reclassificação é um ato explícito.

Funções puras no topo (sem I/O); só `aplicar_regras` toca o banco. Não importa
`models` no nível do módulo, para o model poder usar `normalizar_dominio`.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

#: Ordem dos quatro níveis (os mesmos valores de `Artifact.ClassificationLevel`).
ORDEM = {"publico": 0, "interno": 1, "restrito": 2, "confidencial": 3}
_PADRAO_SEM_CLASSIFICACAO = "restrito"

_ROTULO = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMINIO = re.compile(rf"^{_ROTULO}(?:\.{_ROTULO})+$")


def _para_ascii(host: str) -> str:
    return host.encode("idna").decode("ascii")


def normalizar_dominio(texto: str) -> str:
    """Forma canônica de um domínio digitado pelo usuário. `ValueError` se inválido.

    Aceita `Banco.com.br`, `*.banco.com.br`, `.banco.com.br` e até uma URL colada;
    devolve minúsculas, sem esquema, caminho, porta nem ponto final, em punycode.
    Exige ao menos dois rótulos e um último rótulo com letra — isso recusa IPs,
    que esta regra não cobre.
    """
    bruto = (texto or "").strip().lower()
    if "://" in bruto:
        bruto = urlsplit(bruto).hostname or ""
    bruto = bruto.split("/")[0].split(":")[0]
    bruto = bruto.removeprefix("*.").lstrip(".").rstrip(".")
    try:
        bruto = _para_ascii(bruto)
    except UnicodeError as exc:
        raise ValueError("domínio inválido") from exc
    if not _DOMINIO.match(bruto) or not re.search(r"[a-z]", bruto.rsplit(".", 1)[-1]):
        raise ValueError("domínio inválido (use algo como bancodobrasil.com.br)")
    return bruto


def host_da_url(url: str) -> str | None:
    """Host da URL capturada, em minúsculas e punycode; `None` se não houver."""
    try:
        host = urlsplit(url or "").hostname
        return _para_ascii(host.rstrip(".")) if host else None
    except (ValueError, UnicodeError):
        return None


def sufixos(host: str) -> list[str]:
    """`a.b.c` → `["a.b.c", "b.c", "c"]`: os domínios que podem cobrir o host."""
    rotulos = host.split(".")
    return [".".join(rotulos[i:]) for i in range(len(rotulos))]


@dataclass(frozen=True)
class Resultado:
    nivel: str
    elevado: bool = False
    regra_id: str | None = None
    dominio: str | None = None


def nivel_efetivo(nivel_pedido: str, nivel_regra: str) -> str:
    """O maior dos dois. Nível pedido desconhecido conta como `restrito`."""
    pedido = ORDEM.get(nivel_pedido, ORDEM[_PADRAO_SEM_CLASSIFICACAO])
    return nivel_regra if ORDEM[nivel_regra] > pedido else nivel_pedido


def aplicar_regras(tenant_id, url: str, nivel_pedido: str) -> Resultado:
    """Nível a gravar para uma captura de `url` na organização `tenant_id`.

    Sem regra que case, devolve `nivel_pedido` exatamente como veio (não mexe no
    comportamento anterior). Havendo várias regras, vale a de maior nível.
    """
    from .models import RegraClassificacaoDominio

    host = host_da_url(url)
    if not host:
        return Resultado(nivel_pedido)

    candidatas = list(
        RegraClassificacaoDominio.objects.filter(
            tenant_id=tenant_id, ativa=True, dominio__in=sufixos(host),
        )
    )
    if not candidatas:
        return Resultado(nivel_pedido)

    regra = max(candidatas, key=lambda r: ORDEM.get(r.nivel, -1))
    final = nivel_efetivo(nivel_pedido, regra.nivel)
    if final == nivel_pedido:
        return Resultado(nivel_pedido, regra_id=str(regra.id), dominio=regra.dominio)
    return Resultado(final, elevado=True, regra_id=str(regra.id), dominio=regra.dominio)
