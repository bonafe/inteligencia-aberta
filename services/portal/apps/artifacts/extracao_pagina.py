"""Executa os parsers aprovados da família de uma captura e registra o resultado validado.

É o passo que dispensa o LLM: cada parser foi montado e conferido uma vez, na aprovação do
`SchemaPagina`, e aqui só roda — lxml sobre o HTML original, em milissegundos.

A família é domínio + caminho. Num internet banking, a fatura do cartão e o extrato da conta
têm o mesmo endereço; os dois schemas aprovados rodam, e **a validação decide** qual é a
página: o da fatura procura "Vencimento" e confere a soma com o total, o que no extrato não
fecha. Vale o que validar (o aprovado mais recente, se mais de um); se nenhum validar, fica
registrado o que chegou mais perto, com os problemas. Cada execução vira uma `ExtracaoPagina`
(com os candidatos tentados) e um evento `extracao.parser_pagina`:

- `valido` → evento `ok`;
- `invalido` → evento `falhou` com os problemas: a página mudou ou é de um tipo ainda sem
  schema — dito na hora, em vez de dado errado em silêncio (o defeito do reuso removido em
  `c6d8d1c`);
- `erro` → evento `falhou` com a exceção.

Sem schema aprovado na família, não faz nada. **Nunca levanta**: roda dentro da extração de
toda captura, e um parser quebrado não pode derrubar a captura.
"""

import logging
from time import perf_counter

from apps.events.emit import emit

from .models import ExtracaoPagina, SchemaPagina

logger = logging.getLogger(__name__)


def familia(document_text) -> tuple[str, str] | None:
    padrao = document_text.url_pattern_cache
    return (padrao.domain, padrao.path_pattern) if padrao else None


def schemas_aprovados(document_text):
    """Os schemas aprovados da família da captura, o aprovado mais recente primeiro."""
    chave = familia(document_text) if document_text.url_pattern_cache_id else None
    if chave is None:
        return SchemaPagina.objects.none()
    return (SchemaPagina.da_familia(document_text.document.tenant_id, *chave)
            .filter(status=SchemaPagina.Status.APROVADO).exclude(parser=None)
            .order_by("-aprovado_em", "-versao"))


def _rodar(schema, html):
    from .extractors import parser_pagina

    try:
        resultado = parser_pagina.executar(schema.parser, schema.schema, html)
    except Exception as exc:
        logger.exception("parser do schema %s levantou", schema.id)
        return {"status": ExtracaoPagina.Status.ERRO, "dados": None,
                "problemas": [f"{type(exc).__name__}: {exc}"], "invariantes": []}
    ok = resultado["validacao"]["ok"]
    return {"status": ExtracaoPagina.Status.VALIDO if ok else ExtracaoPagina.Status.INVALIDO,
            "dados": {"campos": resultado["campos"], "listas": resultado["listas"]},
            "problemas": resultado["validacao"]["problemas"], "invariantes": resultado["invariantes"]}


def _escolher(tentativas):
    """O que validou (a ordem já é do aprovado mais recente) ou, se nenhum, o de menos problemas."""
    validos = [t for t in tentativas if t[1]["status"] == ExtracaoPagina.Status.VALIDO]
    if validos:
        return validos[0]
    return min(tentativas, key=lambda t: (t[1]["status"] == ExtracaoPagina.Status.ERRO, len(t[1]["problemas"])))


def extrair_com_parser(document_text, html: str, *, origem: str = ExtracaoPagina.Origem.CAPTURA,
                       user_id=None) -> ExtracaoPagina | None:
    """A extração registrada, ou None quando a família não tem parser aprovado (ou nem deu para registrar)."""
    try:
        schemas = list(schemas_aprovados(document_text))
        if not schemas:
            return None
        artefato = document_text.document
        t0 = perf_counter()
        tentativas = [(s, _rodar(s, html)) for s in schemas]
        duration_ms = int((perf_counter() - t0) * 1000)
        schema, r = _escolher(tentativas)
        candidatos = [{"schema_id": str(s.id), "versao": s.versao, "descricao": (s.schema or {}).get("descricao", ""),
                       "status": t["status"], "problemas": len(t["problemas"])} for s, t in tentativas]

        extracao = ExtracaoPagina.objects.create(
            tenant_id=artefato.tenant_id, document_text=document_text, schema=schema, status=r["status"],
            origem=origem, dados=r["dados"], problemas=r["problemas"], invariantes=r["invariantes"],
            candidatos=candidatos, classification_level=artefato.classification_level, duration_ms=duration_ms,
        )
        itens = sum(len(l["itens"]) for l in (r["dados"] or {}).get("listas", {}).values())
        validaram = [c["versao"] for c in candidatos if c["status"] == ExtracaoPagina.Status.VALIDO]
        payload = {"extracao_id": str(extracao.id), "schema_id": str(schema.id), "versao": schema.versao,
                   "origem": origem, "itens": itens, "problemas": r["problemas"][:10],
                   "tentados": len(candidatos), "validaram": validaram}
        if r["status"] == ExtracaoPagina.Status.VALIDO:
            mais = f"; também validaram {validaram[1:]}" if len(validaram) > 1 else ""
            emit("extracao.parser_pagina", "ok", subject_type="artifact", subject_id=artefato.id,
                 tenant_id=artefato.tenant_id, user_id=user_id, duration_ms=duration_ms, payload=payload,
                 message=f"parser v{schema.versao} validou ({itens} itens), sem LLM{mais}")
        else:
            prefixo = (f"nenhum dos {len(candidatos)} parser(es) validou; o mais perto, v{schema.versao}: "
                       if len(candidatos) > 1 else f"parser v{schema.versao} não validou: ")
            if r["status"] == ExtracaoPagina.Status.ERRO and len(candidatos) == 1:
                prefixo = f"parser v{schema.versao} levantou: "
            emit("extracao.parser_pagina", "falhou", subject_type="artifact", subject_id=artefato.id,
                 tenant_id=artefato.tenant_id, user_id=user_id, duration_ms=duration_ms, payload=payload,
                 message=prefixo + "; ".join(r["problemas"][:3]))
        return extracao
    except Exception:
        logger.exception("[%s] extração pelo parser de página não registrada", document_text.document_id)
        return None
