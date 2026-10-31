"""Executa o parser aprovado do tipo de página numa captura e registra o resultado validado.

É o passo que dispensa o LLM: o parser foi montado e conferido uma vez, na aprovação do
`SchemaPagina`, e aqui só roda — lxml sobre o HTML original, em milissegundos. Cada execução
vira uma `ExtracaoPagina` e um evento `extracao.parser_pagina`:

- `valido` → evento `ok`;
- `invalido` → evento `falhou` com os problemas: o parser rodou, mas a página não fecha mais
  (rótulo sumiu, conferência não bate). É assim que a mudança de layout aparece, em vez de
  dado errado em silêncio — o defeito do reuso removido em `c6d8d1c`;
- `erro` → evento `falhou` com a exceção.

Sem schema aprovado para o tipo, não faz nada. **Nunca levanta**: roda dentro da extração
de toda captura, e um parser quebrado não pode derrubar a captura.
"""

import logging
from time import perf_counter

from apps.events.emit import emit

from .models import ExtracaoPagina, SchemaPagina

logger = logging.getLogger(__name__)


def schema_aprovado(document_text) -> SchemaPagina | None:
    if not document_text.url_pattern_cache_id:
        return None
    return (SchemaPagina.objects
            .filter(padrao_id=document_text.url_pattern_cache_id, status=SchemaPagina.Status.APROVADO)
            .first())


def extrair_com_parser(document_text, html: str, *, origem: str = ExtracaoPagina.Origem.CAPTURA,
                       user_id=None) -> ExtracaoPagina | None:
    """A extração registrada, ou None quando não há parser aprovado (ou nem deu para registrar)."""
    from .extractors import parser_pagina

    try:
        schema = schema_aprovado(document_text)
        if schema is None or not schema.parser:
            return None
        artefato = document_text.document
        t0 = perf_counter()
        try:
            resultado = parser_pagina.executar(schema.parser, schema.schema, html)
            status = (ExtracaoPagina.Status.VALIDO if resultado["validacao"]["ok"]
                      else ExtracaoPagina.Status.INVALIDO)
            dados = {"campos": resultado["campos"], "listas": resultado["listas"]}
            problemas, invariantes = resultado["validacao"]["problemas"], resultado["invariantes"]
        except Exception as exc:
            logger.exception("[%s] parser do schema %s levantou", artefato.id, schema.id)
            status, dados, problemas, invariantes = ExtracaoPagina.Status.ERRO, None, [f"{type(exc).__name__}: {exc}"], []
        duration_ms = int((perf_counter() - t0) * 1000)

        extracao = ExtracaoPagina.objects.create(
            tenant_id=artefato.tenant_id, document_text=document_text, schema=schema, status=status,
            origem=origem, dados=dados, problemas=problemas, invariantes=invariantes,
            classification_level=artefato.classification_level, duration_ms=duration_ms,
        )
        itens = sum(len(l["itens"]) for l in (dados or {}).get("listas", {}).values())
        payload = {"extracao_id": str(extracao.id), "schema_id": str(schema.id), "versao": schema.versao,
                   "origem": origem, "itens": itens, "problemas": problemas[:10]}
        if status == ExtracaoPagina.Status.VALIDO:
            emit("extracao.parser_pagina", "ok", subject_type="artifact", subject_id=artefato.id,
                 tenant_id=artefato.tenant_id, user_id=user_id, duration_ms=duration_ms, payload=payload,
                 message=f"parser v{schema.versao} validou ({itens} itens), sem LLM")
        else:
            emit("extracao.parser_pagina", "falhou", subject_type="artifact", subject_id=artefato.id,
                 tenant_id=artefato.tenant_id, user_id=user_id, duration_ms=duration_ms, payload=payload,
                 message=(f"parser v{schema.versao} não validou: " if status == ExtracaoPagina.Status.INVALIDO
                          else f"parser v{schema.versao} levantou: ") + "; ".join(problemas[:3]))
        return extracao
    except Exception:
        logger.exception("[%s] extração pelo parser de página não registrada", document_text.document_id)
        return None
