"""Que pastas do servidor o IPED pode ler — ADR 019.

O administrador configura as **raízes permitidas** (`IPED_RAIZES_PERMITIDAS`); quem pede um
processamento escolhe um caminho **relativo a uma raiz**. Nada de caminho absoluto do host
vindo do usuário: a resolução segue os links simbólicos e só aceita o que continua dentro
de uma raiz (barra `..`, links que fogem e raízes que se sobrepõem a áreas do sistema).
"""

import os
from pathlib import Path

from django.conf import settings

#: Raízes que nunca podem ser permitidas, porque expõem o próprio host.
RAIZES_PROIBIDAS = ("/", "/etc", "/proc", "/sys", "/dev", "/root", "/boot", "/var/run", "/run")


class CaminhoNaoPermitido(ValueError):
    """O caminho está fora das raízes permitidas (ou não existe)."""


def raizes_permitidas() -> list[Path]:
    """Raízes configuradas, resolvidas; descarta (e nunca aceita) as proibidas ou inexistentes."""
    saida = []
    for bruta in getattr(settings, "IPED_RAIZES_PERMITIDAS", []):
        if not os.path.isabs(bruta):
            continue
        raiz = Path(bruta).resolve()
        if str(raiz) in RAIZES_PROIBIDAS or not raiz.is_dir():
            continue
        saida.append(raiz)
    return saida


def resolver(raiz: str, relativo: str) -> Path:
    """Caminho real de `relativo` dentro de `raiz` (uma das raízes permitidas).

    `CaminhoNaoPermitido` se a raiz não é permitida, o relativo é absoluto, foge da raiz
    (`..` ou link simbólico) ou não existe.
    """
    permitidas = {str(r): r for r in raizes_permitidas()}
    base = permitidas.get(str(Path(raiz).resolve())) if raiz else None
    if base is None:
        raise CaminhoNaoPermitido("raiz não permitida")
    if relativo is None or os.path.isabs(relativo) or "\x00" in relativo:
        raise CaminhoNaoPermitido("o caminho deve ser relativo à raiz")
    alvo = (base / relativo).resolve()
    if alvo != base and base not in alvo.parents:
        raise CaminhoNaoPermitido("o caminho sai da raiz permitida")
    if not alvo.exists():
        raise CaminhoNaoPermitido("o caminho não existe")
    return alvo
