"""Monta o grafo de nós/arestas do "Mapa Vivo" a partir das relações já
existentes no schema — sem depender de ArtifactLineage (que só liga
Artifact→Artifact) nem de um banco de grafo.

Módulo puro: recebe um queryset de Artifact já carregado (com os
select_related/prefetch_related necessários) e devolve dicts serializáveis
em JSON. Nenhuma view, nenhum I/O de rede aqui.
"""
from urllib.parse import urlparse

from django.db.models import Count, QuerySet

from .models import Artifact


def dominio_de(url: str) -> str:
    netloc = urlparse(url or "").netloc or "desconhecido"
    return netloc[4:] if netloc.startswith("www.") else netloc


def normalizar_status_estruturacao(status_modelo: str) -> str:
    return {
        "pendente": "iniciado",
        "executando": "iniciado",
        "concluido": "ok",
        "vazio": "vazio",
        "falhou": "falhou",
        "cancelado": "ignorado",
    }[status_modelo]


def normalizar_status_comparacao(status_modelo: str) -> str:
    return {
        "pendente": "iniciado",
        "executando": "iniciado",
        "concluido": "ok",
        "falhou": "falhou",
        "cancelado": "ignorado",
    }[status_modelo]


def artifacts_para_mapa(orgs, limite: int = 150, antes=None) -> QuerySet:
    """Queryset de Artifacts-documento prontos para virar nós do mapa, com os
    relacionamentos usados por `montar_grafo` já carregados (evita N+1)."""
    qs = (
        Artifact.objects.filter(artifact_type=Artifact.Type.DOCUMENT, tenant__in=orgs)
        .select_related("extracted_text")
        .prefetch_related("extracted_text__estruturacoes_llm", "comparacoes")
        .annotate(n_fragmentos=Count("extracted_text__fragments", distinct=True))
        .order_by("-created_at")
    )
    if antes:
        qs = qs.filter(created_at__lt=antes)
    return qs[:limite]


NAVEGADOR_ID = "navegador"


def montar_grafo(artifacts) -> dict:
    """Monta {"nodes": [...], "edges": [...]} a partir de um iterável de
    Artifact (tipicamente o resultado de `artifacts_para_mapa`).

    Hierarquia fixa do mapa: "navegador" (a extensão Chrome, origem de toda
    captura) → um nó por domínio → um nó por captura MHTML daquele domínio.
    Sem isso cada captura seria uma ilha; com isso o mapa agrupa visualmente
    o que veio do mesmo site."""
    nodes = []
    edges = []
    tem_navegador = False
    dominios_criados: set[str] = set()

    for artifact in artifacts:
        content = artifact.content or {}
        if not content.get("mhtml_path"):
            continue

        if not tem_navegador:
            nodes.append({
                "id": NAVEGADOR_ID,
                "tipo": "navegador",
                "status": "ok",
                "label": "Navegador",
                "meta": {},
                "created_at": artifact.created_at.isoformat(),
                "updated_at": artifact.created_at.isoformat(),
            })
            tem_navegador = True

        url = content.get("url", "")
        favicon = content.get("favicon_data_uri", "")
        dominio = dominio_de(url)
        dominio_id = f"dominio:{dominio}"
        if dominio_id not in dominios_criados:
            dominios_criados.add(dominio_id)
            # Um domínio pode ter páginas com favicons diferentes — em vez de
            # decidir qual "vence", usamos o da primeira captura desse domínio
            # encontrada (a mais recente, já que `artifacts_para_mapa` ordena
            # por -created_at).
            nodes.append({
                "id": dominio_id,
                "tipo": "dominio",
                "status": "ok",
                "label": dominio,
                "meta": {"dominio": dominio, "favicon": favicon},
                "created_at": artifact.created_at.isoformat(),
                "updated_at": artifact.created_at.isoformat(),
            })
            edges.append({"origem": NAVEGADOR_ID, "destino": dominio_id, "tipo": "agrupamento"})

        artifact_id = str(artifact.id)
        nodes.append({
            "id": artifact_id,
            "tipo": "artifact",
            "status": "ok",
            "label": content.get("title") or url or artifact_id,
            "meta": {
                "artifact_id": artifact_id,
                "url": url,
                "dominio": dominio,
                "favicon": favicon,
                "classification_level": artifact.classification_level,
            },
            "created_at": artifact.created_at.isoformat(),
            "updated_at": artifact.updated_at.isoformat(),
        })
        edges.append({"origem": dominio_id, "destino": artifact_id, "tipo": "captura"})

        doc_text = getattr(artifact, "extracted_text", None)
        if doc_text is None:
            continue

        doc_text_id = str(doc_text.id)
        nodes.append({
            "id": doc_text_id,
            "tipo": "document_text",
            "status": "ok",
            "label": doc_text.title or doc_text.page_type or "Texto extraído",
            "meta": {
                "artifact_id": artifact_id,
                "document_text_id": doc_text_id,
                "page_type": doc_text.page_type,
            },
            "created_at": doc_text.created_at.isoformat(),
            "updated_at": doc_text.updated_at.isoformat(),
        })
        edges.append({"origem": artifact_id, "destino": doc_text_id, "tipo": "extracao"})

        n_fragmentos = getattr(artifact, "n_fragmentos", 0)
        if n_fragmentos:
            frag_id = f"frag:{doc_text_id}"
            nodes.append({
                "id": frag_id,
                "tipo": "fragmentos",
                "status": "ok",
                "label": f"Fragmentos ({n_fragmentos})",
                "meta": {
                    "artifact_id": artifact_id,
                    "document_text_id": doc_text_id,
                    "count": n_fragmentos,
                },
                "created_at": doc_text.created_at.isoformat(),
                "updated_at": doc_text.updated_at.isoformat(),
            })
            edges.append({"origem": doc_text_id, "destino": frag_id, "tipo": "fragmentacao"})

        estruturacao_por_id = {}
        for estruturacao in doc_text.estruturacoes_llm.all():
            estruturacao_id = str(estruturacao.id)
            estruturacao_por_id[estruturacao_id] = estruturacao
            nodes.append({
                "id": estruturacao_id,
                "tipo": "estruturacao_llm",
                "status": normalizar_status_estruturacao(estruturacao.status),
                "label": f"{estruturacao.provider}:{estruturacao.model_name}",
                "meta": {
                    "artifact_id": artifact_id,
                    "document_text_id": doc_text_id,
                    "estruturacao_id": estruturacao_id,
                    "provider": estruturacao.provider,
                    "model_name": estruturacao.model_name,
                    "categoria": estruturacao.categoria,
                },
                "created_at": estruturacao.created_at.isoformat(),
                "updated_at": estruturacao.updated_at.isoformat(),
            })
            edges.append({"origem": doc_text_id, "destino": estruturacao_id, "tipo": "estruturacao"})

        for comparacao in artifact.comparacoes.all():
            comparacao_id = str(comparacao.id)
            nodes.append({
                "id": comparacao_id,
                "tipo": "comparacao",
                "status": normalizar_status_comparacao(comparacao.status),
                "label": f"Comparação — juiz {comparacao.modelo_juiz_provider}:{comparacao.modelo_juiz_model_name}",
                "meta": {
                    "artifact_id": artifact_id,
                    "comparacao_id": comparacao_id,
                },
                "created_at": comparacao.created_at.isoformat(),
                "updated_at": comparacao.created_at.isoformat(),
            })
            edges.append({"origem": artifact_id, "destino": comparacao_id, "tipo": "comparacao"})

            for referencia in comparacao.referencias or []:
                if referencia.get("tipo") == "estruturacao_llm":
                    origem_id = str(referencia.get("id"))
                    if origem_id in estruturacao_por_id:
                        edges.append({"origem": comparacao_id, "destino": origem_id, "tipo": "referencia"})

    return {"nodes": nodes, "edges": edges}
