"""Valores em formato brasileiro, lidos como aparecem na página. Sem Django."""

import re
from decimal import Decimal, InvalidOperation


def parse_valor(texto: str) -> Decimal | None:
    """"R$ -9.964,56" → Decimal("-9964.56"). Aceita sinal antes, depois ou entre parênteses."""
    if not texto:
        return None
    negativo = "-" in texto or "−" in texto or ("(" in texto and ")" in texto)
    numero = re.sub(r"[^\d.,]", "", texto)
    if not re.search(r"\d", numero):
        return None
    if "," in numero:
        numero = numero.replace(".", "").replace(",", ".")
    elif numero.count(".") > 1 or re.search(r"\.\d{3}$", numero):
        numero = numero.replace(".", "")
    try:
        valor = Decimal(numero).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return -valor if negativo else valor
