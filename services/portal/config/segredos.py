"""Validação de segredos obrigatórios na subida.

Uma instância com segredo vazio ou com o valor de exemplo do `.env.example`
"funciona" — e é por isso que é perigosa: JWTs assináveis por qualquer um, tokens
de serviço previsíveis. Em produção o processo se recusa a subir, listando de uma
vez tudo o que está errado (em vez de um erro por tentativa).

Há cópias deste módulo em services/orchestrator/segredos.py e services/mcp/segredos.py:
cada serviço é uma imagem separada, sem código compartilhado.
"""

import os

# Prefixos dos placeholders usados no .env.example (atual e antigo).
_PLACEHOLDERS = ("change_me", "substitua-por")


def segredo_invalido(valor: str | None) -> bool:
    texto = (valor or "").strip()
    return not texto or texto.lower().startswith(_PLACEHOLDERS)


def validar_segredos(nomes: list[str]) -> None:
    ruins = [n for n in nomes if segredo_invalido(os.environ.get(n))]
    if ruins:
        raise RuntimeError(
            "Segredos obrigatórios ausentes ou com valor de exemplo (CHANGE_ME): "
            + ", ".join(ruins)
            + ". Gere um valor aleatório para cada um no .env "
            "(ex.: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")."
        )
