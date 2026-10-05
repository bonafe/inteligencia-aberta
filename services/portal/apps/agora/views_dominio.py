"""Leitura de dados do Inteligência Aberta para os componentes `ia-*` do Ultima Agora.

Só leitura, sempre com o usuário da sessão e isolada por organização (`orgs_do_usuario`): ter acesso a um
workspace **não** dá acesso aos objetos que ele referencia (R-CONN-7, R-IA-8). Um objeto de outra
organização é 404, exatamente como se não existisse.

Os componentes recebem DTOs pequenos (id, rótulo, tipo, nível), e não o modelo Django (R-IA-6). O barramento
do Agora só carrega referências a esses objetos; o detalhe é buscado aqui, a cada vez, com a permissão de
quem está olhando.
"""

from django.db.models import Q, TextField
from django.db.models.functions import Cast
from django.http import Http404, JsonResponse
from django.views import View

from apps.accounts.views import orgs_do_usuario
from apps.artifacts.models import Artifact, ArtifactLineage

from . import acesso
from .models import Workspace

NIVEIS = {"publico": 0, "interno": 1, "restrito": 2, "confidencial": 3}
CHAVES_DE_ROTULO = ("nome", "razao_social", "nome_fantasia", "titulo", "numero", "logradouro", "url")
LIMITE_PADRAO, LIMITE_MAXIMO = 20, 50


def rotulo(artefato) -> str:
    conteudo = artefato.content if isinstance(artefato.content, dict) else {}
    for chave in CHAVES_DE_ROTULO:
        if conteudo.get(chave):
            return str(conteudo[chave])[:120]
    return artefato.get_artifact_type_display()


def _piso_do_workspace(request):
    """O nível do workspace informado em `?workspace=`, se o usuário tiver acesso a ele (senão, ignora)."""
    identificador = request.GET.get("workspace")
    if not identificador:
        return None
    try:
        workspace = Workspace.objects.select_related("organization").filter(pk=identificador).first()
    except (ValueError, TypeError):
        return None
    if workspace and acesso.papel_efetivo(request.user, workspace):
        return workspace.classification
    return None


def serializar(artefato, piso=None) -> dict:
    dto = {
        "id": str(artefato.pk), "kind": artefato.artifact_type, "label": rotulo(artefato),
        "classification": artefato.classification_level, "info_type": artefato.info_type,
    }
    #R-CLS-3: referenciar algo mais restrito do que o workspace é permitido, mas sinalizado (nada é copiado)
    if piso is not None:
        dto["above_workspace"] = NIVEIS.get(artefato.classification_level, 3) > NIVEIS.get(piso, 3)
    return dto


def _do_usuario(request, artifact_id):
    artefato = Artifact.objects.filter(pk=artifact_id, tenant__in=orgs_do_usuario(request.user)).first()
    if artefato is None:
        raise Http404("Objeto não encontrado")
    return artefato


class ArtefatosView(View):
    """GET ?q=&limite=&workspace= — busca textual simples nos artefatos da organização do usuário."""

    def get(self, request):
        try:
            limite = max(1, min(int(request.GET.get("limite", LIMITE_PADRAO)), LIMITE_MAXIMO))
        except ValueError:
            limite = LIMITE_PADRAO
        consulta = request.GET.get("q", "").strip()
        queryset = Artifact.objects.filter(tenant__in=orgs_do_usuario(request.user))
        #?tipo=documento (ou várias, separadas por vírgula): só os tipos pedidos; tipo inexistente não devolve nada (e não erro)
        tipos = [t for t in request.GET.get("tipo", "").split(",") if t.strip()]
        if tipos:
            queryset = queryset.filter(artifact_type__in=[t.strip() for t in tipos])
        if consulta:
            queryset = queryset.annotate(texto=Cast("content", TextField())).filter(Q(texto__icontains=consulta))
        piso = _piso_do_workspace(request)
        return JsonResponse({"results": [serializar(a, piso) for a in queryset.order_by("-classified_at")[:limite]]})


class ArtefatoView(View):
    def get(self, request, artifact_id):
        artefato = _do_usuario(request, artifact_id)
        dto = serializar(artefato, _piso_do_workspace(request))
        #O que as fontes DIZEM, separado do que o sistema sabe (R-IA-9): sempre com produtor e confiança
        alegacoes = artefato.alegacoes.order_by("-criada_em")[:20]
        dto["claims"] = [
            {
                "predicate": c.predicado, "object": c.objeto_ref or c.objeto_literal, "producer": c.produtor,
                "confidence": c.extractor_confidence, "state": c.estado, "classification": c.classification_level,
            }
            for c in alegacoes
        ]
        dto["sources"] = [fonte for fonte in (artefato.sources or []) if isinstance(fonte, dict)][:10]
        return JsonResponse(dto)


class RelacoesView(View):
    """O vizinho imediato do artefato como `Graph` do Agora ({nodes:[{id,label}], edges:[{from,to,label}]}).

    Arestas: linhagem (pai → filho, rotulada pela transformação) e referências em `content` a outros
    artefatos da mesma organização (`vinculos`, `enderecos`).
    """

    def get(self, request, artifact_id):
        centro = _do_usuario(request, artifact_id)
        orgs = orgs_do_usuario(request.user)
        nos = {centro.pk: centro}
        arestas = []

        for linha in ArtifactLineage.objects.filter(Q(parent=centro) | Q(child=centro)).select_related("parent", "child"):
            for ponta in (linha.parent, linha.child):
                if ponta.tenant_id in {o.pk for o in orgs}:
                    nos.setdefault(ponta.pk, ponta)
            if linha.parent_id in nos and linha.child_id in nos:
                arestas.append({"from": str(linha.parent_id), "to": str(linha.child_id), "label": linha.transformation})

        conteudo = centro.content if isinstance(centro.content, dict) else {}
        referencias = [r for chave in ("vinculos", "enderecos") for r in (conteudo.get(chave) or []) if isinstance(r, str)]
        if referencias:
            for alvo in Artifact.objects.filter(pk__in=_uuids(referencias), tenant__in=orgs):
                nos.setdefault(alvo.pk, alvo)
                arestas.append({"from": str(centro.pk), "to": str(alvo.pk), "label": "vínculo"})

        return JsonResponse({
            "nodes": [{"id": str(a.pk), "label": rotulo(a), "kind": a.artifact_type, "classification": a.classification_level} for a in nos.values()],
            "edges": arestas,
        })


def _uuids(textos):
    import uuid

    validos = []
    for texto in textos:
        try:
            validos.append(uuid.UUID(texto))
        except ValueError:
            continue
    return validos
