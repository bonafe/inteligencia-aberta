"""Validação de segredos obrigatórios na subida.

Cópia de services/portal/config/segredos.py (cada serviço é uma imagem separada).
Só vale em produção — ver `em_producao()`. Em desenvolvimento o .env pode manter
os placeholders do .env.example.
"""

import os

_PLACEHOLDERS = ("change_me", "substitua-por")


def em_producao() -> bool:
    # O .env é um só para os três serviços; o modo é o do Django.
    return os.environ.get("DJANGO_SETTINGS_MODULE", "").endswith(".production")


def segredo_invalido(valor: str | None) -> bool:
    texto = (valor or "").strip()
    return not texto or texto.lower().startswith(_PLACEHOLDERS)


def validar_segredos(nomes: list[str]) -> None:
    if not em_producao():
        return
    ruins = [n for n in nomes if segredo_invalido(os.environ.get(n))]
    if ruins:
        raise RuntimeError(
            "Segredos obrigatórios ausentes ou com valor de exemplo (CHANGE_ME): "
            + ", ".join(ruins)
            + ". Gere um valor aleatório para cada um no .env."
        )
