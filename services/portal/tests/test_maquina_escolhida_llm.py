"""Visualizador: a pessoa fixa a máquina do cluster (como no chat do Agora) para estruturar e para o juiz."""
import json
from unittest import mock

import pytest
from django.test import Client

from apps.artifacts.models import Artifact, Comparacao, DocumentText, EstruturacaoLLM
from tests.test_agora_llm import MODELO, _cliente, _maquina, _org

pytestmark = pytest.mark.django_db


@pytest.fixture
def mundo():
    org_a, dono_a = _org("a")
    org_b, dono_b = _org("b")
    artifact = Artifact.objects.create(
        artifact_type=Artifact.Type.DOCUMENT, content={"url": "https://exemplo.org/p"},
        classification_level=Artifact.ClassificationLevel.PUBLIC, tenant=org_a, info_type=Artifact.InfoType.FACT,
    )
    DocumentText.objects.create(document=artifact, text="t", dom_representation="<x/>", page_type="artigo")
    return org_a, dono_a, org_b, dono_b, artifact


def _estruturar(cliente, artifact, **extra):
    corpo = {"provider": "ollama", "model_name": MODELO, **extra}
    with mock.patch("apps.artifacts.tasks.estruturar_llm_manual.delay") as delay:
        delay.return_value.id = "tarefa"
        resp = cliente.post(f"/artifacts/{artifact.id}/estruturar-llm/", json.dumps(corpo), content_type="application/json")
    return resp, delay


def test_estruturar_repassa_a_maquina_fixada_a_task(mundo):
    org_a, dono_a, _, _, artifact = mundo
    maquina = _maquina(org_a, dono_a, "notebook")
    resp, delay = _estruturar(_cliente(dono_a), artifact, maquina=str(maquina.id))
    assert resp.status_code == 200
    assert delay.call_args.args[1] == str(maquina.id)


def test_estruturar_sem_maquina_deixa_o_roteador_escolher(mundo):
    org_a, dono_a, _, _, artifact = mundo
    _maquina(org_a, dono_a, "notebook")
    resp, delay = _estruturar(_cliente(dono_a), artifact)
    assert resp.status_code == 200
    assert delay.call_args.args[1] is None


def test_maquina_de_outra_organizacao_ou_sem_o_modelo_e_recusada(mundo):
    org_a, dono_a, org_b, dono_b, artifact = mundo
    alheia = _maquina(org_b, dono_b, "alheia")
    sem_modelo = _maquina(org_a, dono_a, "outra", modelo="llama3:8b")
    for maquina in (alheia, sem_modelo):
        resp, delay = _estruturar(_cliente(dono_a), artifact, maquina=str(maquina.id))
        assert resp.status_code == 400
        delay.assert_not_called()
    assert not EstruturacaoLLM.objects.exists()


def test_maquina_invalida_ou_com_claude_e_400(mundo):
    org_a, dono_a, _, _, artifact = mundo
    maquina = _maquina(org_a, dono_a, "notebook")
    assert _estruturar(_cliente(dono_a), artifact, maquina="nao-e-uuid")[0].status_code == 400
    resp, _ = _estruturar(_cliente(dono_a), artifact, provider="anthropic", model_name="claude", maquina=str(maquina.id))
    assert resp.status_code == 400


def test_comparar_repassa_a_maquina_do_juiz(mundo):
    org_a, dono_a, _, _, artifact = mundo
    maquina = _maquina(org_a, dono_a, "notebook")
    dt = artifact.extracted_text
    execs = [EstruturacaoLLM.objects.create(document_text=dt, tenant=org_a, provider="ollama", model_name=MODELO,
                                            status=EstruturacaoLLM.Status.CONCLUIDO, structured_data={"a": i}) for i in range(2)]
    corpo = {"referencias": [{"tipo": "estruturacao_llm", "id": str(e.id)} for e in execs],
             "modelo_juiz": {"provider": "ollama", "model_name": MODELO, "maquina": str(maquina.id)}}
    with mock.patch("apps.artifacts.tasks.comparar_llm.delay") as delay:
        delay.return_value.id = "tarefa"
        resp = _cliente(dono_a).post(f"/artifacts/{artifact.id}/comparar/", json.dumps(corpo), content_type="application/json")
    assert resp.status_code == 200
    assert delay.call_args.args[1] == str(maquina.id)
    assert Comparacao.objects.count() == 1


def test_gerar_texto_usa_a_maquina_fixada_e_nao_o_roteador(mundo):
    org_a, dono_a, _, _, _ = mundo
    maquina = _maquina(org_a, dono_a, "notebook", endpoint="http://10.9.9.9:11434")
    from apps.artifacts.extractors.llm_common import gerar_texto

    with mock.patch("apps.artifacts.extractors.ollama_client.gerar", return_value="ok") as gerar, \
         mock.patch("apps.cluster.llm_router.escolher_execucao") as roteador:
        texto, usada = gerar_texto("ollama", MODELO, "s", "p", finalidade="estruturacao_manual", maquina_id=str(maquina.id))
    roteador.assert_not_called()
    assert (texto, usada) == ("ok", str(maquina.id))
    assert gerar.call_args.kwargs["host"] == "http://10.9.9.9:11434"
