"""Registro e retratação de alegações — o único caminho para criar `Claim`.

`registrar_alegacao` valida tudo antes de gravar e cria a alegação **junto com as
suas evidências, numa transação**: não existe alegação sem evidência. É
idempotente por `(artefato, chave)`: repetir a mesma extração devolve a alegação
que já existe em vez de duplicá-la. Uma versão nova do produtor gera alegações
novas, e as antigas permanecem (histórico).
"""

import hashlib
import re
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from config.conteudo_hash import formato_valido as hash_valido

from .models import Claim, Evidence
from .referencias import normalizar_referencia

PREDICADO = re.compile(r"^(schema|ia|prov):[A-Za-z][A-Za-z0-9]*$")
LITERAL_MAXIMO = 2000


@dataclass(frozen=True)
class EvidenciaEntrada:
    blob_hash: str
    localizador_tipo: str
    localizador: dict = field(default_factory=dict)
    trecho: str = ""


def truncar_trecho(texto: str) -> str:
    """Corta no limite de `Evidence.TRECHO_MAXIMO`, marcando o corte com reticências."""
    texto = " ".join((texto or "").split())
    limite = Evidence.TRECHO_MAXIMO
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def _chave(sujeito, predicado, objeto_ref, objeto_literal, autor, produtor, versao) -> str:
    partes = [sujeito, predicado, objeto_ref or "", objeto_literal or "", autor, produtor, versao]
    return hashlib.sha256("\x1f".join(partes).encode()).hexdigest()


def registrar_alegacao(
    *, artefato, sujeito_ref: str, predicado: str, autor_ref: str, produtor: str, produtor_versao: str,
    evidencias: list[EvidenciaEntrada], objeto_ref: str | None = None, objeto_literal: str | None = None,
    modelo: str = "", confianca: float | None = None, revisa: Claim | None = None,
) -> tuple[Claim, bool]:
    """Cria a alegação com as evidências e devolve `(claim, criada)`.

    `ValueError` para qualquer entrada inválida (nada é gravado). `criada=False`
    quando a mesma alegação já existia para este artefato.
    """
    if (objeto_ref is None) == (objeto_literal is None):
        raise ValueError("informe exatamente um entre objeto_ref e objeto_literal")
    if not PREDICADO.match(predicado or ""):
        raise ValueError("predicado deve ser `schema:…`, `ia:…` ou `prov:…`")
    if not produtor or not produtor_versao:
        raise ValueError("produtor e produtor_versao são obrigatórios")
    if confianca is not None and not 0 <= confianca <= 1:
        raise ValueError("confianca fora de [0, 1]")
    if not evidencias:
        raise ValueError("uma alegação precisa de ao menos uma evidência")

    sujeito = normalizar_referencia(sujeito_ref)
    autor = normalizar_referencia(autor_ref)
    objeto_ref = normalizar_referencia(objeto_ref) if objeto_ref is not None else None
    if objeto_literal is not None:
        objeto_literal = str(objeto_literal)
        if not objeto_literal.strip():
            raise ValueError("objeto_literal vazio")
        if len(objeto_literal) > LITERAL_MAXIMO:
            raise ValueError(f"objeto_literal acima de {LITERAL_MAXIMO} caracteres")

    for ev in evidencias:
        if not hash_valido(ev.blob_hash):
            raise ValueError("evidência com blob_hash inválido")
        if not ev.localizador_tipo:
            raise ValueError("evidência sem localizador_tipo")
        if len(ev.trecho) > Evidence.TRECHO_MAXIMO:
            raise ValueError(f"trecho acima de {Evidence.TRECHO_MAXIMO} caracteres (use truncar_trecho)")

    chave = _chave(sujeito, predicado, objeto_ref, objeto_literal, autor, produtor, produtor_versao)
    existente = Claim.objects.filter(artefato=artefato, chave=chave).first()
    if existente:
        return existente, False

    try:
        with transaction.atomic():
            claim = Claim.objects.create(
                tenant_id=artefato.tenant_id, artefato=artefato,
                sujeito_ref=sujeito, predicado=predicado,
                objeto_ref=objeto_ref, objeto_literal=objeto_literal,
                autor_ref=autor, produtor=produtor, produtor_versao=produtor_versao, modelo=modelo,
                extractor_confidence=confianca, classification_level=artefato.classification_level,
                revisa=revisa, chave=chave,
            )
            Evidence.objects.bulk_create([
                Evidence(claim=claim, blob_hash=ev.blob_hash, localizador_tipo=ev.localizador_tipo,
                         localizador=ev.localizador, trecho=ev.trecho)
                for ev in evidencias
            ])
    except IntegrityError:
        # Corrida: outro processo criou a mesma alegação entre a leitura e o INSERT.
        existente = Claim.objects.filter(artefato=artefato, chave=chave).first()
        if existente is None:
            raise
        return existente, False
    return claim, True


def retratar(claim: Claim) -> Claim:
    """Marca a alegação como retratada. Não apaga nada; idempotente."""
    if claim.estado != Claim.Estado.RETRATADA:
        claim.estado = Claim.Estado.RETRATADA
        claim.retratada_em = timezone.now()
        claim.save(update_fields=["estado", "retratada_em"])
    return claim
