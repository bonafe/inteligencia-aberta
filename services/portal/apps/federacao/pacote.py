"""Pacote offline — F1b da federação (ADR 010/011/018): trocar um espaço **por arquivo**.

Um pacote é um zip com:

    manifesto.json           envelope assinado (`pacote.manifesto`): destino, ids dos eventos, blobs
    eventos/00001.json ...   envelopes assinados (`artefato.compartilhado`), em ordem, ligados por `prev`
    blobs/<sha256 b64url>    os MHTML, nomeados pelo próprio hash (RFC 6920)

**Exportar** passa cada artefato pelo motor de regras (sentido *enviar*) e só leva o que
ele permite; **importar** confere tudo (assinaturas, ids, hashes, par confirmado, destino)
**antes de aplicar qualquer coisa** e passa cada objeto pelo motor do receptor (sentido
*receber*) — a interseção da ADR 011. O que o receptor recusa é contado, não explicado.

O que viaja são **resultados** (texto extraído, dados estruturados, alegações); nada é
reprocessado. A localização interna do blob (bucket/caminho) nunca vai no pacote. O
pacote só é lido por nomes esperados (nunca extraído para o disco) e tem limites de tamanho.
"""

import io
import re
import uuid
import zipfile
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.artifacts.models import Artifact, Claim, DocumentText
from apps.events.emit import emit
from config.conteudo_hash import formato_valido, hash_ni

from . import blobs, envelope, espacos, politica, regras
from .chaves import chave_ativa
from .ids import eh_urn_valida, urn_de, uuid_de_urn
from .models import EventoFederado, Space

TIPO_MANIFESTO = "pacote.manifesto"
TIPO_ARTEFATO = "artefato.compartilhado"

LIMITE_BLOB = 200 * 1024 * 1024
LIMITE_TOTAL = 2 * 1024 * 1024 * 1024
LIMITE_MEMBROS = 20000
_NOME_EVENTO = re.compile(r"^eventos/\d{5}\.json$")
_NOME_BLOB = re.compile(r"^blobs/[A-Za-z0-9_-]{43}$")
_CAMPOS_TEXTO = (
    "text", "full_text", "title", "source_url", "page_type", "detection_source", "structured_data",
    "dados_estruturados_dom2parser", "dados_estruturados_extruct", "dados_estruturados_deterministico",
    "dom_representation", "extractor_version", "char_count", "word_count",
)


class ErroPacote(ValueError):
    """O pacote não pode ser exportado/importado. A mensagem é para o operador local."""


@dataclass
class ResumoExportacao:
    enviados: int = 0
    negados: int = 0
    sem_blob: list = field(default_factory=list)
    grandes: list = field(default_factory=list)


@dataclass
class ResumoImportacao:
    importados: int = 0
    ja_existiam: int = 0
    recusados: int = 0           # o motor do receptor negou (sem explicar ao emissor)
    associados: int = 0          # o objeto já existia aqui e só ganhou o espaço
    alegacoes: int = 0
    lacuna: bool = False         # o `prev` do primeiro evento não é conhecido aqui
    erros: list = field(default_factory=list)


def _blob_nome(hash_ni_: str) -> str:
    return "blobs/" + hash_ni_.split(";", 1)[1]


def _sem_floats(obj):
    """Números de ponto flutuante viram texto: o envelope canônico não os carrega (ver `envelope`)."""
    if isinstance(obj, float):
        return repr(obj)
    if isinstance(obj, dict):
        return {str(k): _sem_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sem_floats(v) for v in obj]
    return obj


# ── exportar ─────────────────────────────────────────────────────────────────

def _payload_do_artefato(artefato, hash_blob, tamanho):
    conteudo = {k: v for k, v in (artefato.content or {}).items() if not k.startswith("mhtml_") and k != "federado"}
    texto = DocumentText.objects.filter(document=artefato).first()
    alegacoes = []
    for c in Claim.objects.filter(artefato=artefato, estado=Claim.Estado.ATIVA).prefetch_related("evidencias").order_by("criada_em", "id"):
        alegacoes.append({
            "id": c.urn, "sujeito": c.sujeito_ref, "predicado": c.predicado, "objeto_ref": c.objeto_ref,
            "objeto_literal": c.objeto_literal, "autor": c.autor_ref, "produtor": c.produtor,
            "produtor_versao": c.produtor_versao, "modelo": c.modelo,
            "confianca": None if c.extractor_confidence is None else repr(c.extractor_confidence),
            "evidencias": [
                {"blob_hash": e.blob_hash, "localizador_tipo": e.localizador_tipo,
                 "localizador": e.localizador, "trecho": e.trecho}
                for e in sorted(c.evidencias.all(), key=lambda e: (e.criada_em, str(e.id)))
            ],
        })
    return _sem_floats({
        "objeto": artefato.urn, "tipo": artefato.artifact_type, "nivel": artefato.classification_level,
        "info_type": artefato.info_type, "fontes": artefato.sources, "conteudo": conteudo,
        "criado_em": artefato.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "blob": {"hash": hash_blob, "tamanho": tamanho} if hash_blob else None,
        "texto": {k: getattr(texto, k) for k in _CAMPOS_TEXTO} | {
            "detection_confidence": texto.detection_confidence,
        } if texto else None,
        "alegacoes": alegacoes,
    })


def exportar(*, organizacao, espaco, par, usuario=None) -> tuple[bytes, ResumoExportacao]:
    """Gera o pacote do `espaco` para o `par`, levando só o que o motor permite enviar."""
    from apps.cluster.models import Maquina

    chave = chave_ativa()
    if chave is None:
        raise ErroPacote("esta instância ainda não tem chave (garanta com `manage.py chave_instancia`)")
    if par.eh_local or par.organizacao_id != organizacao.id:
        raise ErroPacote("par inválido para esta organização")
    if par.estado != Maquina.Estado.CONFIRMADO or not par.did:
        raise ErroPacote("o par precisa estar confirmado (impressão digital conferida)")
    explicito = isinstance(espaco, Space)
    if explicito and espaco.organizacao_id != organizacao.id:
        raise ErroPacote("o espaço é de outra organização")

    resumo = ResumoExportacao()
    agora = timezone.now()
    ultimo = (
        EventoFederado.objects.filter(
            organizacao=organizacao, direcao=EventoFederado.Direcao.EMITIDO, autor_did=chave.did, espaco_urn=espaco.urn,
        ).order_by("-criado_em", "-id").values_list("evento_id", flat=True).first()
    )
    eventos, arquivos_blob = [], {}
    prev = ultimo
    for artefato in espacos.artefatos_do_espaco(espaco).order_by("created_at", "id"):
        ctx = politica.contexto_do_artefato(
            artefato, sentido=regras.ENVIAR, par_tipo=par.tipo, par_ref=par.apelido,
            espaco_urn=espaco.urn, espaco_explicito=explicito,
        )
        decisao = politica.decidir(organizacao, ctx, agora=agora)
        politica.registrar_decisao(decisao, ctx, organizacao=organizacao, artefato=artefato)
        if not decisao.permitido:
            resumo.negados += 1
            continue

        hash_blob = tamanho = None
        caminho = (artefato.content or {}).get("mhtml_path")
        if caminho:
            try:
                dados = blobs.ler_blob(artefato.content.get("mhtml_bucket", blobs.BUCKET_MHTML), caminho)
            except Exception as exc:  # S3 fora do ar, objeto ausente...
                resumo.sem_blob.append((artefato.urn, f"{type(exc).__name__}"))
                continue
            hash_blob, tamanho = hash_ni(dados), len(dados)
            if artefato.blob_hash and artefato.blob_hash != hash_blob:
                resumo.sem_blob.append((artefato.urn, "hash divergente do gravado"))
                continue
            if tamanho > LIMITE_BLOB:
                resumo.grandes.append(artefato.urn)
                continue
        env = envelope.criar(
            chave, espaco_urn=espaco.urn, tipo=TIPO_ARTEFATO, prev=prev, agora=agora,
            payload=_payload_do_artefato(artefato, hash_blob, tamanho),
        )
        if len(envelope.canonico(env)) > envelope.TAMANHO_MAXIMO:
            resumo.grandes.append(artefato.urn)
            continue
        eventos.append((env, artefato))
        if hash_blob:
            arquivos_blob[hash_blob] = dados
        prev = env["id"]
        resumo.enviados += 1

    nome_espaco = espaco.nome if explicito else organizacao.name
    manifesto = envelope.criar(
        chave, espaco_urn=espaco.urn, tipo=TIPO_MANIFESTO, prev=None, agora=agora,
        payload={
            "destino": par.did, "espaco_nome": nome_espaco, "primeiro_prev": ultimo,
            "eventos": [e["id"] for e, _ in eventos],
            "blobs": {h: len(d) for h, d in sorted(arquivos_blob.items())},
        },
    )
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifesto.json", envelope.canonico(manifesto))
        for i, (env, _) in enumerate(eventos, 1):
            zf.writestr(f"eventos/{i:05d}.json", envelope.canonico(env))
        for h, d in arquivos_blob.items():
            zf.writestr(_blob_nome(h), d)

    with transaction.atomic():
        for env, artefato in eventos:
            EventoFederado.objects.create(
                organizacao=organizacao, direcao=EventoFederado.Direcao.EMITIDO, evento_id=env["id"],
                tipo=env["type"], espaco_urn=env["space"], autor_did=env["author"], prev=env["prev"],
                objeto_urn=artefato.urn, par=par, envelope_bruto=envelope.canonico(env).decode("utf-8"),
            )
    emit("federacao.pacote_exportado", "ok", source="portal", tenant_id=organizacao.id,
         message=f"pacote exportado: {resumo.enviados} enviado(s), {resumo.negados} negado(s)",
         payload={"par": par.apelido, "espaco_urn": espaco.urn, "enviados": resumo.enviados, "negados": resumo.negados})
    return saida.getvalue(), resumo


# ── importar ─────────────────────────────────────────────────────────────────

def _ler_zip(dados: bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        raise ErroPacote("não é um pacote (zip inválido)") from None
    infos = zf.infolist()
    if len(infos) > LIMITE_MEMBROS:
        raise ErroPacote("pacote com membros demais")
    total = 0
    nomes = set()
    for info in infos:
        n = info.filename
        if not (n == "manifesto.json" or _NOME_EVENTO.match(n) or _NOME_BLOB.match(n)):
            raise ErroPacote("o pacote contém um arquivo inesperado")
        if n in nomes:
            raise ErroPacote("o pacote repete um arquivo")
        nomes.add(n)
        limite = LIMITE_BLOB if n.startswith("blobs/") else envelope.TAMANHO_MAXIMO
        if info.file_size > limite:
            raise ErroPacote("um arquivo do pacote excede o limite")
        total += info.file_size
    if total > LIMITE_TOTAL:
        raise ErroPacote("pacote grande demais")
    if "manifesto.json" not in nomes:
        raise ErroPacote("pacote sem manifesto")
    return zf, sorted(nomes)


def _ler(zf, nome, limite):
    with zf.open(nome) as f:
        dados = f.read(limite + 1)
    if len(dados) > limite:
        raise ErroPacote("um arquivo do pacote excede o limite")
    return dados


def _espaco_destino(organizacao, par, espaco_urn, nome):
    """Espaço local do que chega (P7): o mesmo UUID do espaço de origem, numa organização local."""
    from apps.accounts.models import Membership, Organization
    from apps.cluster.models import Maquina

    existente = Space.objects.filter(pk=uuid_de_urn(espaco_urn)).first()
    if existente:
        if existente.arquivado:
            raise ErroPacote("o espaço de destino está arquivado")
        return existente
    if par.tipo == Maquina.Tipo.PROPRIO:
        destino = organizacao
    else:  # terceiro: os dados nunca se misturam com os do dono
        destino = Organization.objects.create(
            name=f"Federado: {nome}"[:255], slug=f"federado-{uuid.uuid4().hex[:10]}",
            org_type=organizacao.org_type, owner=organizacao.owner,
        )
        Membership.objects.get_or_create(
            user=organizacao.owner, organization=destino, defaults={"role": Membership.Role.OWNER},
        )
    nome_final = (nome or "federado").strip()[: espacos.NOME_MAXIMO] or "federado"
    if Space.objects.filter(organizacao=destino, nome=nome_final).exists():
        nome_final = f"{nome_final[: espacos.NOME_MAXIMO - 10]} ({uuid.uuid4().hex[:6]})"
    return Space.objects.create(id=uuid_de_urn(espaco_urn), nome=nome_final, organizacao=destino)


def importar(*, organizacao, dados: bytes, usuario=None) -> ResumoImportacao:
    """Confere o pacote inteiro e só então aplica, objeto a objeto, passando pelo motor do receptor."""
    from apps.cluster.models import Maquina

    chave = chave_ativa()
    if chave is None:
        raise ErroPacote("esta instância ainda não tem chave")
    zf, nomes = _ler_zip(dados)

    # 1) tudo é conferido antes de aplicar qualquer coisa (falha fechada)
    try:
        manifesto = envelope.verificar(_ler(zf, "manifesto.json", envelope.TAMANHO_MAXIMO))
        if manifesto["type"] != TIPO_MANIFESTO:
            raise ErroPacote("o manifesto não é um manifesto")
        brutos = [(n, _ler(zf, n, envelope.TAMANHO_MAXIMO)) for n in nomes if n.startswith("eventos/")]
        eventos = [(raw, envelope.verificar(raw)) for _, raw in brutos]
    except envelope.ErroEnvelope as exc:
        raise ErroPacote(f"pacote recusado: {exc}") from None
    pm = manifesto["payload"]
    if pm.get("destino") != chave.did:
        raise ErroPacote("este pacote não é para esta instância")
    par = Maquina.objects.filter(
        organizacao=organizacao, did=manifesto["author"], eh_local=False, estado=Maquina.Estado.CONFIRMADO,
    ).first()
    if par is None:
        raise ErroPacote("o emissor não é um par confirmado desta organização")
    if [e["id"] for _, e in eventos] != list(pm.get("eventos", [])):
        raise ErroPacote("os eventos do pacote não conferem com o manifesto")
    anterior = pm.get("primeiro_prev")
    for _, e in eventos:
        if e["author"] != manifesto["author"] or e["space"] != manifesto["space"] or e["type"] != TIPO_ARTEFATO:
            raise ErroPacote("evento de outro autor, espaço ou tipo")
        if e["prev"] != anterior:
            raise ErroPacote("a cadeia de eventos (prev) está quebrada")
        anterior = e["id"]
    blobs_manifesto = pm.get("blobs", {})
    if not isinstance(blobs_manifesto, dict) or not all(formato_valido(h) for h in blobs_manifesto):
        raise ErroPacote("lista de blobs inválida")
    conteudo_blobs = {}
    for h, tamanho in blobs_manifesto.items():
        nome = _blob_nome(h)
        if nome not in nomes:
            raise ErroPacote("falta um blob listado no manifesto")
        bruto = _ler(zf, nome, LIMITE_BLOB)
        if hash_ni(bruto) != h or len(bruto) != tamanho:
            raise ErroPacote("um blob não confere com o hash do manifesto")
        conteudo_blobs[h] = bruto
    if set(nomes) - {"manifesto.json"} != {n for n, _ in brutos} | {_blob_nome(h) for h in blobs_manifesto}:
        raise ErroPacote("o pacote contém arquivos que o manifesto não cita")

    # 2) aplica
    resumo = ResumoImportacao()
    resumo.lacuna = bool(eventos) and pm.get("primeiro_prev") is not None and not EventoFederado.objects.filter(
        organizacao=organizacao, direcao=EventoFederado.Direcao.RECEBIDO, evento_id=pm["primeiro_prev"],
    ).exists()
    espaco_urn = manifesto["space"]
    espaco_local = _espaco_destino(organizacao, par, espaco_urn, str(pm.get("espaco_nome") or "federado"))
    agora = timezone.now()
    for raw, e in eventos:
        if EventoFederado.objects.filter(
            organizacao=organizacao, direcao=EventoFederado.Direcao.RECEBIDO, evento_id=e["id"],
        ).exists():
            resumo.ja_existiam += 1
            continue
        p = e["payload"]
        try:
            objeto = urn_de(uuid_de_urn(p["objeto"]))
            nivel = regras.nivel_efetivo(str(p.get("nivel")))
            ctx = regras.Contexto(
                sentido=regras.RECEBER, par_tipo=par.tipo, par_ref=par.apelido, nivel=nivel,
                tipo_objeto=str(p.get("tipo")), espaco_urn=espaco_urn, objeto_urn=objeto,
            )
            decisao = politica.decidir(organizacao, ctx, agora=agora)
            politica.registrar_decisao(decisao, ctx, organizacao=organizacao)
            with transaction.atomic():
                if not decisao.permitido:
                    resumo.recusados += 1
                    # Registramos o evento como recebido mesmo recusado? Não: recusar é "descartar".
                    continue
                _aplicar(organizacao, par, espaco_local, e, raw, p, nivel, conteudo_blobs, resumo)
        except (KeyError, ValueError, TypeError) as exc:
            resumo.erros.append(f"{e['id'][:20]}: {type(exc).__name__}: {exc}"[:200])
    emit("federacao.pacote_importado", "ok" if not resumo.erros else "falhou", source="portal",
         tenant_id=organizacao.id,
         message=f"pacote importado de {par.apelido}: {resumo.importados} novo(s), {resumo.recusados} recusado(s)",
         payload={"par": par.apelido, "espaco_urn": espaco_urn, "importados": resumo.importados,
                  "recusados": resumo.recusados, "ja_existiam": resumo.ja_existiam, "erros": len(resumo.erros)})
    return resumo


def _aplicar(organizacao, par, espaco_local, e, raw, p, nivel, conteudo_blobs, resumo):
    from apps.artifacts.alegacoes import EvidenciaEntrada, registrar_alegacao

    pk = uuid_de_urn(p["objeto"])
    if p["tipo"] not in {t for t, _ in Artifact.Type.choices}:
        raise ValueError("tipo de objeto desconhecido")
    existente = Artifact.objects.filter(pk=pk).first()
    if existente is not None:
        # Já existe aqui: permanece onde está e só ganha o espaço (P7). Nada é sobrescrito.
        espacos.incluir_artefato(espaco_local, existente, importado=True)
        resumo.associados += 1
        artefato = existente
    else:
        conteudo = dict(p.get("conteudo") or {})
        blob = p.get("blob")
        blob_hash = None
        if blob:
            blob_hash = blob["hash"]
            if blob_hash not in conteudo_blobs:
                raise ValueError("blob do objeto ausente no pacote")
            caminho = f"{pk}.mhtml"
            blobs.gravar_blob(blobs.BUCKET_MHTML, caminho, conteudo_blobs[blob_hash])
            conteudo.update({"mhtml_bucket": blobs.BUCKET_MHTML, "mhtml_path": caminho})
        conteudo["federado"] = {"origem": e["author"], "evento": e["id"]}
        artefato = Artifact(
            id=pk, artifact_type=p["tipo"], content=conteudo, classification_level=nivel,
            tenant=espaco_local.organizacao, allow_external_llm=False,
            info_type=p.get("info_type") or Artifact.InfoType.FACT, sources=p.get("fontes") or [],
            blob_hash=blob_hash,
        )
        # bulk_create de propósito: o signal de `post_save` dispararia a extração, e o que
        # chega são resultados — nada é reprocessado (ADR 011, P5).
        Artifact.objects.bulk_create([artefato])
        espacos.incluir_artefato(espaco_local, artefato, importado=True)
        texto = p.get("texto")
        if texto:
            campos = {k: texto.get(k) for k in _CAMPOS_TEXTO if k in texto}
            for k in ("text", "title", "source_url", "page_type", "detection_source", "extractor_version"):
                campos[k] = campos.get(k) or ""
            for k in ("char_count", "word_count"):
                campos[k] = int(campos.get(k) or 0)
            DocumentText.objects.create(document=artefato, detection_confidence=None, **campos)
        resumo.importados += 1
        for a in p.get("alegacoes") or []:
            try:
                registrar_alegacao(
                    artefato=artefato, sujeito_ref=a["sujeito"], predicado=a["predicado"], autor_ref=a["autor"],
                    produtor=a["produtor"], produtor_versao=a["produtor_versao"], modelo=a.get("modelo") or "",
                    objeto_ref=a.get("objeto_ref"), objeto_literal=a.get("objeto_literal"),
                    confianca=None if a.get("confianca") is None else float(a["confianca"]),
                    evidencias=[EvidenciaEntrada(**ev) for ev in a["evidencias"]],
                )
                resumo.alegacoes += 1
            except (KeyError, TypeError, ValueError) as exc:
                resumo.erros.append(f"alegação de {p['objeto'][:20]}: {exc}"[:200])
    EventoFederado.objects.create(
        organizacao=organizacao, direcao=EventoFederado.Direcao.RECEBIDO, evento_id=e["id"], tipo=e["type"],
        espaco_urn=e["space"], autor_did=e["author"], prev=e["prev"], objeto_urn=p["objeto"], par=par,
        envelope_bruto=raw.decode("utf-8"),
    )
