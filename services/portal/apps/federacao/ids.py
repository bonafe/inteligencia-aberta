"""Identificador global de objetos: `urn:uuid:<uuid>` — ADR 010.

Os modelos já usam UUID como PK, então **o identificador global é o próprio PK**
escrito como URN: não há coluna nova nem tabela de mapeamento. Duas consequências
a respeitar quando objetos passarem entre instâncias:

- o objeto importado **mantém** o UUID de origem; trocá-lo quebraria toda
  referência assinada que o cita;
- o ID não diz de onde o objeto veio — isso é papel do `author` (chave) e do
  espaço no envelope, nunca do prefixo do ID.

A forma emitida é sempre a canônica (minúscula, com hífens), porque IDs entram em
bytes assinados e `urn:uuid:ABC…` ≠ `urn:uuid:abc…` byte a byte. Na leitura, o
RFC 8141 trata `urn:` e `uuid:` sem diferenciar maiúsculas, então `uuid_de_urn`
aceita as duas e `normalizar` devolve a canônica.
"""

import uuid

PREFIXO = "urn:uuid:"


def urn_de(valor: "uuid.UUID | str") -> str:
    """URN canônica de um UUID (objeto `UUID` ou string em qualquer forma aceita)."""
    return PREFIXO + str(_como_uuid(valor))


def uuid_de_urn(urn: str) -> uuid.UUID:
    """UUID de uma URN `urn:uuid:`. `ValueError` se não for uma URN de objeto válida."""
    if not isinstance(urn, str) or urn[: len(PREFIXO)].lower() != PREFIXO:
        raise ValueError("não é uma URN urn:uuid:")
    texto = urn[len(PREFIXO):]
    # `uuid.UUID()` aceitaria chaves, ausência de hífens e um `urn:uuid:` extra;
    # aqui só a forma hifenizada de 36 caracteres.
    if len(texto) != 36 or texto.count("-") != 4:
        raise ValueError("UUID fora da forma canônica de 36 caracteres")
    return _como_uuid(texto)


def eh_urn_valida(valor: object) -> bool:
    try:
        uuid_de_urn(valor)  # type: ignore[arg-type]
    except ValueError:
        return False
    return True


def normalizar(urn: str) -> str:
    """Forma canônica de uma URN lida de fora (minúscula)."""
    return urn_de(uuid_de_urn(urn))


def _como_uuid(valor: "uuid.UUID | str") -> uuid.UUID:
    try:
        resultado = valor if isinstance(valor, uuid.UUID) else uuid.UUID(str(valor))
    except ValueError as exc:
        raise ValueError("UUID inválido") from exc
    # Rejeita o UUID nulo, o máximo e variantes que não são RFC 4122: nenhum é um
    # identificador de objeto legítimo.
    if resultado.variant != uuid.RFC_4122:
        raise ValueError("UUID de variante não RFC 4122 (inclui o nulo e o máximo)")
    return resultado
