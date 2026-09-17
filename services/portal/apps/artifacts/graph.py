"""Monta o grafo de nós/arestas do "Mapa Vivo" a partir das relações já
existentes no schema — sem depender de ArtifactLineage (que só liga
Artifact→Artifact) nem de um banco de grafo.

Módulo puro: recebe um queryset de Artifact já carregado (com os
select_related/prefetch_related necessários) e devolve dicts serializáveis
em JSON. Nenhuma view, nenhum I/O de rede aqui.
"""
from urllib.parse import urlparse

from django.db.models import Count, Prefetch, QuerySet

from apps.events.context import correlacao_de_artefato
from apps.events.models import PipelineEvent

from .models import Artifact, Comparacao, EstruturacaoLLM

#: Estágios cujo `hostname` (já resolvido pra apelido de `Maquina`, ver
#: apps.events.context.identidade_execucao) atribui "em que máquina" algo
#: aconteceu — captura.recebida é emitido pelo orchestrator (sempre a máquina
#: que hospeda a infra, hoje), extracao.concluida pelo worker que processou
#: (pode ser qualquer máquina do cluster).
_ESTAGIO_CAPTURA = "captura.recebida"
_ESTAGIO_EXTRACAO = "extracao.concluida"


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
        .prefetch_related(
            # select_related("maquina") aninhado: sem isso, cada estruturação/
            # comparação puxaria a Maquina numa query própria (N+1) só pra
            # saber o apelido de quem processou.
            Prefetch("extracted_text__estruturacoes_llm",
                     queryset=EstruturacaoLLM.objects.select_related("maquina")),
            Prefetch("comparacoes", queryset=Comparacao.objects.select_related("maquina")),
        )
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
    maquinas_criadas: set[str] = set()

    def no_maquina(apelido: str, quando) -> str:
        """Cria (se ainda não existe) o nó da máquina e devolve seu id — o
        mesmo nó é reaproveitado por toda captura/execução atribuída a ela,
        do mesmo jeito que um domínio agrupa suas capturas."""
        maquina_id = f"maquina:{apelido}"
        if maquina_id not in maquinas_criadas:
            maquinas_criadas.add(maquina_id)
            nodes.append({
                "id": maquina_id,
                "tipo": "maquina",
                "status": "ok",
                "label": apelido,
                "meta": {"apelido": apelido},
                "created_at": quando.isoformat(),
                "updated_at": quando.isoformat(),
            })
        return maquina_id

    # Uma captura específica pode não ter favicon próprio (download falhou no
    # orchestrator naquele momento — timeout, favicon ausente, content-type
    # inesperado) mesmo quando outras capturas do MESMO domínio têm. Sem este
    # mapa, cada nó dependia só do seu próprio favicon, e o mapa ficava com
    # imagem "às vezes sim, às vezes não" pro mesmo site. `artifacts_para_mapa`
    # ordena por -created_at, então o primeiro favicon nao-vazio encontrado
    # por domínio já é o mais recente disponível.
    artifacts = list(artifacts)

    # Correlação de cada artefato (a mesma usada pelo log de eventos) — prefere
    # a que o orchestrator gerou no clique (`content["correlation_id"]`, já em
    # mãos, sem consulta nova); cai pro uuid5 determinístico só se faltar.
    # Não usa `tasks.correlacao_do_artefato` aqui de propósito: aquela função
    # faz uma query por artefato, e aqui já temos `content` carregado — bater
    # o banco de novo pra cada um seria N+1.
    correlacao_por_artifact: dict[str, str] = {}
    for artifact in artifacts:
        declarada = (artifact.content or {}).get("correlation_id")
        correlacao_por_artifact[str(artifact.id)] = (
            str(declarada) if declarada else str(correlacao_de_artefato(artifact.id))
        )

    # Uma query só pra descobrir em que máquina cada captura/extração
    # aconteceu — `PipelineEvent.hostname` já é o apelido da Maquina desde
    # apps.events.context.identidade_execucao (ADR-006).
    apelido_por_estagio: dict[tuple[str, str], str] = {}
    if correlacao_por_artifact:
        eventos = (
            PipelineEvent.objects.filter(
                correlation_id__in=set(correlacao_por_artifact.values()),
                stage__in=(_ESTAGIO_CAPTURA, _ESTAGIO_EXTRACAO),
            )
            .order_by("sequence")
            .values("correlation_id", "stage", "hostname")
        )
        for evento in eventos:
            apelido_por_estagio[(str(evento["correlation_id"]), evento["stage"])] = evento["hostname"]

    favicon_por_dominio: dict[str, str] = {}
    for artifact in artifacts:
        content = artifact.content or {}
        favicon = content.get("favicon_data_uri", "")
        if not favicon:
            continue
        dominio = dominio_de(content.get("url", ""))
        favicon_por_dominio.setdefault(dominio, favicon)

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
        dominio = dominio_de(url)
        favicon = content.get("favicon_data_uri", "") or favicon_por_dominio.get(dominio, "")
        dominio_id = f"dominio:{dominio}"
        if dominio_id not in dominios_criados:
            dominios_criados.add(dominio_id)
            nodes.append({
                "id": dominio_id,
                "tipo": "dominio",
                "status": "ok",
                "label": dominio,
                "meta": {"dominio": dominio, "favicon": favicon_por_dominio.get(dominio, "")},
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

        apelido_captura = apelido_por_estagio.get((correlacao_por_artifact[artifact_id], _ESTAGIO_CAPTURA))
        if apelido_captura:
            maquina_captura_id = no_maquina(apelido_captura, artifact.created_at)
            edges.append({"origem": maquina_captura_id, "destino": artifact_id, "tipo": "captura_em"})

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

        apelido_extracao = apelido_por_estagio.get((correlacao_por_artifact[artifact_id], _ESTAGIO_EXTRACAO))
        if apelido_extracao:
            maquina_extracao_id = no_maquina(apelido_extracao, doc_text.created_at)
            edges.append({"origem": maquina_extracao_id, "destino": doc_text_id, "tipo": "execucao_em"})

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
            # Diferente de captura/extração (via PipelineEvent), aqui a máquina
            # já é uma FK direta — é o roteador de LLM quem decide, registrado
            # no momento da chamada (ver llm_common.gerar_texto).
            if estruturacao.maquina_id:
                maquina_llm_id = no_maquina(estruturacao.maquina.apelido, estruturacao.updated_at)
                edges.append({"origem": maquina_llm_id, "destino": estruturacao_id, "tipo": "execucao_em"})

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
            if comparacao.maquina_id:
                maquina_comp_id = no_maquina(comparacao.maquina.apelido, comparacao.created_at)
                edges.append({"origem": maquina_comp_id, "destino": comparacao_id, "tipo": "execucao_em"})
            edges.append({"origem": artifact_id, "destino": comparacao_id, "tipo": "comparacao"})

            for referencia in comparacao.referencias or []:
                if referencia.get("tipo") == "estruturacao_llm":
                    origem_id = str(referencia.get("id"))
                    if origem_id in estruturacao_por_id:
                        edges.append({"origem": comparacao_id, "destino": origem_id, "tipo": "referencia"})

    return {"nodes": nodes, "edges": edges}
