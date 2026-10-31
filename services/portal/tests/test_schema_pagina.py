"""SchemaPagina: o LLM declara o schema de um tipo de página; a normalização é determinística,
a geração é aditiva (uma versão por pedido) e segue a régua de classificação."""
import json
from unittest import mock

import pytest

from apps.accounts.models import Membership, Organization, User
from apps.artifacts.extractors import schema_pagina as S
from apps.artifacts.models import Artifact, DocumentText, SchemaPagina, URLPatternCache
from apps.artifacts.tasks import gerar_schema_pagina
from apps.events.models import PipelineEvent

LINHAS = "".join(
    f"<tr><td>{d:02d}/09</td><td>LOJA {d}</td><td>R$</td><td>{d},50</td></tr>" for d in range(1, 9)
)
HTML = f"""<html><body>
<div class="vencimentoFatura"><span>Vencimento</span><br>28/10/2026</div>
<table class="resumo"><tr><td>Total da fatura - R$</td><td>13.405,63</td></tr></table>
<div class="lancamentos"><table>
<tr><td>Data</td><td>Transações</td><td>Moeda</td><td>Valor</td></tr>{LINHAS}
</table></div></body></html>"""

RESPOSTA = {
    "descricao": "Fatura de cartão de crédito",
    "campos": [
        {"nome": "Vencimento", "tipo": "data", "descricao": "data de vencimento", "obrigatorio": True,
         "exemplo": "28/10/2026"},
        {"nome": "total da fatura", "tipo": "valor", "obrigatorio": True, "exemplo": "13.405,63"},
        {"nome": "Saldo Anterior", "tipo": "valor", "obrigatorio": False, "exemplo": None},
    ],
    "listas": [{
        "nome": "lançamentos", "secoes": True, "estrutura": "tr",
        "campos": [
            {"nome": "data", "tipo": "data", "obrigatorio": True, "exemplo": "01/09"},
            {"nome": "descrição", "tipo": "texto", "obrigatorio": True, "exemplo": "LOJA 1"},
            {"nome": "valor", "tipo": "dinheiro", "obrigatorio": True, "exemplo": "1,50"},
        ],
    }],
    "invariantes": [
        {"tipo": "soma", "lista": "lancamentos", "campo": "valor", "mais": ["saldo_anterior"],
         "igual_a": "total_da_fatura"},
        {"tipo": "media", "lista": "lancamentos", "campo": "valor", "igual_a": "total_da_fatura"},
        {"tipo": "soma", "lista": "lancamentos", "campo": "nao_existe", "igual_a": "total_da_fatura"},
    ],
}


# ── normalização ────────────────────────────────────────────────────────────

def test_normalizar_canoniza_nomes_tipos_e_invariantes():
    schema, avisos = S.normalizar_schema(RESPOSTA)
    assert [c["nome"] for c in schema["campos"]] == ["vencimento", "total_da_fatura", "saldo_anterior"]
    assert schema["campos"][2]["exemplo"] == ""                 # campo declarado sem valor na amostra
    lista = schema["listas"][0]
    assert lista["nome"] == "lancamentos" and lista["secoes"] is True and lista["estrutura"] == "tr"
    assert [c["nome"] for c in lista["campos"]] == ["data", "descricao", "valor"]
    assert lista["campos"][2]["tipo"] == "texto"                # "dinheiro" não é tipo conhecido
    assert schema["invariantes"] == [{
        "tipo": "soma", "lista": "lancamentos", "campo": "valor", "mais": ["saldo_anterior"],
        "igual_a": "total_da_fatura", "descricao": "",
    }]
    assert any("dinheiro" in a for a in avisos)
    assert sum("invariante descartado" in a for a in avisos) == 2


def test_normalizar_nomes_repetidos_e_lixo():
    schema, avisos = S.normalizar_schema({
        "campos": [{"nome": "Total"}, {"nome": "total"}, {"nome": ""}, "lixo", {"nome": "x", "exemplo": 12.5}],
        "listas": [{"nome": "vazia", "campos": []}, {"campos": [{"nome": "a"}]}],
    })
    assert [c["nome"] for c in schema["campos"]] == ["total", "total_2", "x"]
    assert schema["campos"][2]["exemplo"] == "12.5"
    assert schema["listas"] == []
    assert any("sem nome" in a for a in avisos) and any("sem campos" in a for a in avisos)


def test_normalizar_resposta_que_nao_e_objeto():
    schema, avisos = S.normalizar_schema(["x"])
    assert S.schema_vazio(schema) and avisos


def test_resumo_estruturas_mostra_seletor_campos_e_linhas():
    import dom2parser

    resumo = S.resumo_estruturas(dom2parser.compress(HTML).parser, HTML)
    assert "seletor:" in resumo and "valor" in resumo and "LOJA 1" in resumo


# ── task ────────────────────────────────────────────────────────────────────

@pytest.fixture
def org(db):
    dono = User.objects.create_user(username="dono", password="x")
    org = Organization.objects.create(name="O", slug="o", org_type="individual", owner=dono)
    Membership.objects.create(user=dono, organization=org, role=Membership.Role.OWNER)
    return org


def _captura(org, nivel="restrito"):
    padrao = URLPatternCache.objects.get_or_create(
        tenant=org, domain="www2.banco.example", path_pattern="/fatura", structure_fingerprint="abc",
        defaults={"page_type": "tabular_generico", "confidence": 0.9},
    )[0]
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level=nivel, content={"url": "https://www2.banco.example/fatura", "mhtml_path": "x"},
    )
    DocumentText.objects.create(document=artefato, text="t", dom_representation="<x/>", title="Fatura",
                                url_pattern_cache=padrao)
    return artefato, padrao


def _schema(org, provider="ollama"):
    artefato, padrao = _captura(org)
    return SchemaPagina.objects.create(
        tenant=org, padrao=padrao, dominio=padrao.domain, caminho=padrao.path_pattern, amostra=artefato, versao=1, classification_level="restrito",
        provider=provider, model_name="qwen2.5:7b",
    )


def _rodar(registro, resposta):
    with mock.patch("apps.artifacts.tasks._ler_mhtml", return_value=b""), \
         mock.patch("apps.artifacts.tasks._html_do_mhtml", return_value=(HTML, "utf-8")), \
         mock.patch.object(S, "gerar_texto", return_value=(resposta, None)) as gerar:
        gerar_schema_pagina(str(registro.id))
    registro.refresh_from_db()
    return gerar


def test_task_grava_rascunho_com_schema_normalizado(org):
    registro = _schema(org)
    gerar = _rodar(registro, json.dumps(RESPOSTA))
    assert registro.status == SchemaPagina.Status.RASCUNHO
    assert registro.schema["campos"][0]["nome"] == "vencimento"
    assert registro.avisos and registro.duration_ms is not None
    prompt = gerar.call_args.args[3]
    assert "ESTRUTURAS REPETIDAS VERIFICADAS" in prompt and "Vencimento" in prompt
    assert gerar.call_args.kwargs["finalidade"] == "schema_pagina"
    assert PipelineEvent.objects.filter(stage="schema_pagina.gerado", status="ok").exists()


def test_task_json_ilegivel_falha(org):
    registro = _schema(org)
    _rodar(registro, "não sei responder")
    assert registro.status == SchemaPagina.Status.FALHOU and registro.error_message
    assert PipelineEvent.objects.filter(stage="schema_pagina.falhou", status="falhou").exists()


def test_task_sem_campos_e_vazio_nao_falhou(org):
    registro = _schema(org)
    _rodar(registro, json.dumps({"descricao": "nada", "campos": [], "listas": []}))
    assert registro.status == SchemaPagina.Status.VAZIO
    assert PipelineEvent.objects.filter(stage="schema_pagina.vazio", status="vazio").exists()


def test_task_nunca_chama_llm_externo_para_amostra_restrita(org):
    registro = _schema(org, provider="anthropic")
    gerar = _rodar(registro, json.dumps(RESPOSTA))
    gerar.assert_not_called()
    assert registro.status == SchemaPagina.Status.FALHOU


def test_task_respeita_cancelamento(org):
    registro = _schema(org)
    registro.status = SchemaPagina.Status.CANCELADO
    registro.save()
    gerar = _rodar(registro, json.dumps(RESPOSTA))
    gerar.assert_not_called()
    assert registro.status == SchemaPagina.Status.CANCELADO


# ── views ───────────────────────────────────────────────────────────────────

@pytest.fixture
def enfileirar():
    with mock.patch("apps.artifacts.tasks.gerar_schema_pagina.delay") as delay:
        delay.return_value.id = "task-1"
        yield delay


def _pedir(client, artefato, provider="ollama"):
    return client.post(f"/artifacts/{artefato.id}/schema-pagina/",
                       json.dumps({"provider": provider, "model_name": "qwen2.5:7b"}),
                       content_type="application/json")


def test_cada_pedido_e_uma_versao_nova_do_tipo_de_pagina(client, org, enfileirar):
    client.force_login(org.owner)
    artefato, padrao = _captura(org)
    outra, _ = _captura(org)                                    # mesma página, outra captura
    assert _pedir(client, artefato).json()["versao"] == 1
    assert _pedir(client, outra).json()["versao"] == 2
    assert enfileirar.call_count == 2
    lista = client.get(f"/artifacts/{artefato.id}/schemas-pagina/").json()
    assert [s["versao"] for s in lista["schemas"]] == [2, 1]
    assert lista["padrao"]["dominio"] == "www2.banco.example"
    assert SchemaPagina.objects.get(versao=1).classification_level == "restrito"


def test_view_recusa_llm_externo_para_restrito(client, org, enfileirar):
    client.force_login(org.owner)
    artefato, _ = _captura(org)
    assert _pedir(client, artefato, provider="anthropic").status_code == 403
    assert not SchemaPagina.objects.exists()


def test_view_sem_tipo_de_pagina_e_400(client, org, enfileirar):
    client.force_login(org.owner)
    artefato = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, tenant=org, info_type=Artifact.InfoType.FACT,
        classification_level="publico", content={"mhtml_path": "x"},
    )
    assert _pedir(client, artefato).status_code == 400


def test_view_isola_organizacao(client, org, enfileirar):
    artefato, _ = _captura(org)
    registro = _schema(org)
    estranho = User.objects.create_user(username="e", password="x")
    org2 = Organization.objects.create(name="P", slug="p", org_type="individual", owner=estranho)
    Membership.objects.create(user=estranho, organization=org2, role=Membership.Role.OWNER)
    client.force_login(estranho)
    assert _pedir(client, artefato).status_code == 404
    assert client.get(f"/artifacts/{artefato.id}/schemas-pagina/").status_code == 404
    assert client.post(f"/artifacts/{artefato.id}/schemas-pagina/{registro.id}/cancelar/").status_code == 404


def test_cancelar(client, org):
    client.force_login(org.owner)
    registro = _schema(org)
    with mock.patch("celery.current_app.control.revoke"):
        resp = client.post(f"/artifacts/{registro.amostra_id}/schemas-pagina/{registro.id}/cancelar/")
    assert resp.status_code == 200
    registro.refresh_from_db()
    assert registro.status == SchemaPagina.Status.CANCELADO
    assert client.post(f"/artifacts/{registro.amostra_id}/schemas-pagina/{registro.id}/cancelar/").status_code == 400
