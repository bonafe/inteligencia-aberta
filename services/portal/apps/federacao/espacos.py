"""Serviço de espaços — ADR 010/011.

Um `Space` agrupa artefatos; um artefato pode estar em vários. Esta etapa não
concede acesso (segue por organização) e não tem membros.

**Espaço padrão.** Cada organização tem um espaço implícito, sem linha no banco:
"todos os artefatos da organização". Ele tem um ID estável e **derivado** da
organização (`uuid5`), para poder ser citado em regras e eventos como qualquer
espaço. `artefatos_do_espaco` e `espacos_do_artefato` tratam os dois tipos de
forma uniforme.
"""

import uuid
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from .ids import urn_de
from .models import EspacoArtefato, Space

#: Espaço de nomes fixo — mudá-lo muda o ID do espaço padrão de todas as organizações.
NAMESPACE_ESPACO_PADRAO = uuid.UUID("0e5bd3c0-6c0a-5d6e-9a3e-1a0c2f6f1b57")
NOME_MAXIMO = 120


@dataclass(frozen=True)
class EspacoPadrao:
    """O espaço implícito de uma organização (sem linha no banco)."""

    organizacao: object

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(NAMESPACE_ESPACO_PADRAO, str(self.organizacao.id))

    @property
    def urn(self) -> str:
        return urn_de(self.id)

    @property
    def nome(self) -> str:
        return "Padrão"

    @property
    def organizacao_id(self):
        return self.organizacao.id

    arquivado = False


def espaco_padrao(organizacao) -> EspacoPadrao:
    return EspacoPadrao(organizacao)


def criar_espaco(*, organizacao, nome: str, descricao: str = "", criado_por=None) -> Space:
    """Cria um espaço explícito. `ValueError` se o nome for inválido ou já existir na organização."""
    nome = " ".join((nome or "").split())
    if not nome:
        raise ValueError("o nome do espaço não pode ser vazio")
    if len(nome) > NOME_MAXIMO:
        raise ValueError(f"o nome do espaço passa de {NOME_MAXIMO} caracteres")
    try:
        with transaction.atomic():
            return Space.objects.create(
                organizacao=organizacao, nome=nome, descricao=descricao or "", criado_por=criado_por,
            )
    except IntegrityError:
        raise ValueError(f"já existe um espaço chamado {nome!r} nesta organização") from None


def arquivar(espaco: Space) -> Space:
    """Arquivado não recebe novos artefatos; nada é apagado. Idempotente."""
    if not espaco.arquivado:
        espaco.arquivado = True
        espaco.save(update_fields=["arquivado"])
    return espaco


def desarquivar(espaco: Space) -> Space:
    if espaco.arquivado:
        espaco.arquivado = False
        espaco.save(update_fields=["arquivado"])
    return espaco


def incluir_artefato(espaco: Space, artefato, *, usuario=None, importado: bool = False) -> tuple[EspacoArtefato, bool]:
    """Inclui o artefato no espaço. Devolve `(item, criado)`; idempotente.

    Só artefato da **mesma organização** do espaço entra, a menos que seja
    `importado=True`: o objeto vindo de fora mantém o `tenant` de origem e só
    ganha a associação ao novo espaço (decisão P7). Espaço arquivado não recebe.
    """
    if not isinstance(espaco, Space):
        raise ValueError("o espaço padrão contém tudo da organização; não há o que incluir")
    if espaco.arquivado:
        raise ValueError("espaço arquivado não recebe artefatos")
    if not importado and artefato.tenant_id != espaco.organizacao_id:
        raise ValueError("o artefato é de outra organização que a do espaço")
    item, criado = EspacoArtefato.objects.get_or_create(
        espaco=espaco, artefato=artefato, defaults={"incluido_por": usuario},
    )
    return item, criado


def remover_artefato(espaco: Space, artefato) -> bool:
    """Tira o artefato do espaço (o artefato em si não é tocado). `False` se não estava."""
    if not isinstance(espaco, Space):
        raise ValueError("o espaço padrão contém tudo da organização; não há o que remover")
    apagados, _ = EspacoArtefato.objects.filter(espaco=espaco, artefato=artefato).delete()
    return apagados > 0


def artefatos_do_espaco(espaco):
    """`QuerySet` de `Artifact` do espaço — vale para o explícito e para o padrão."""
    from apps.artifacts.models import Artifact

    if isinstance(espaco, EspacoPadrao):
        return Artifact.objects.filter(tenant_id=espaco.organizacao_id)
    return Artifact.objects.filter(espaco_itens__espaco=espaco)


def espacos_do_artefato(artefato) -> list:
    """O espaço padrão da organização do artefato e os espaços explícitos em que ele está."""
    from apps.accounts.models import Organization

    organizacao = Organization.objects.get(pk=artefato.tenant_id)
    explicitos = list(Space.objects.filter(itens__artefato=artefato).order_by("nome"))
    return [espaco_padrao(organizacao), *explicitos]


def resolver_urn(urn: str):
    """O espaço (explícito ou padrão) de uma `urn:uuid:`, ou `None` se não existir aqui."""
    from apps.accounts.models import Organization

    from .ids import uuid_de_urn

    try:
        id_ = uuid_de_urn(urn)
    except ValueError:
        return None
    explicito = Space.objects.filter(pk=id_).first()
    if explicito:
        return explicito
    for organizacao in Organization.objects.all():
        padrao = espaco_padrao(organizacao)
        if padrao.id == id_:
            return padrao
    return None
