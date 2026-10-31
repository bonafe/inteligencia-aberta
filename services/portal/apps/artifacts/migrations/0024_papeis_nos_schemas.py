"""Papéis, tipo de documento e convenção de sinal nos schemas que já existem (ADR 021).

Os schemas gerados antes da ADR 021 não dizem o que cada campo é. Esta migração infere pelos
nomes — a única informação que eles têm — e só preenche o que estiver vazio: vencimento, total,
saldo anterior, data, descrição e valor de uma fatura de cartão. A heurística vive aqui, e não no
código da aplicação, de propósito: migração não pode depender de código que vai mudar.
"""

import re
import unicodedata

from django.db import migrations


def _chave(nome: str) -> str:
    texto = unicodedata.normalize("NFKD", str(nome or ""))
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


_CAMPOS = (
    (r"vencimento", "vencimento"),
    (r"^total", "total_documento"),
    (r"saldo_anterior", "saldo_anterior"),
    (r"saldo_final", "saldo_final"),
    (r"(numero|n)_?(do_)?cartao|cartao_numero|agencia|conta_corrente", "identifica_conta"),
    (r"titular", "titular"),
    (r"periodo|competencia|mes_referencia", "periodo"),
)
_COLUNAS = (
    (r"^data", "data"),
    (r"^valor", "valor"),
    (r"transac|descri|histor|estabelecimento", "descricao"),
    (r"^documento", "documento"),
    (r"^debito", "debito"),
    (r"^credito", "credito"),
)


def _inferir(itens, regras):
    usados = set()
    for item in itens:
        if item.get("papel"):
            usados.add(item["papel"])
    for item in itens:
        item.setdefault("papel", "")
        if item["papel"]:
            continue
        nome = _chave(item.get("nome"))
        for padrao, papel in regras:
            if papel not in usados and re.search(padrao, nome):
                item["papel"] = papel
                usados.add(papel)
                break


def preencher(apps, schema_editor):
    SchemaPagina = apps.get_model("artifacts", "SchemaPagina")
    for registro in SchemaPagina.objects.exclude(schema=None):
        schema = registro.schema
        _inferir(schema.get("campos", []), _CAMPOS)
        for lista in schema.get("listas", []):
            _inferir(lista.get("campos", []), _COLUNAS)
        papeis_campo = {c.get("papel") for c in schema.get("campos", [])}
        descricao = _chave(schema.get("descricao"))
        if not schema.get("tipo_documento"):
            if "extrato" in descricao:
                schema["tipo_documento"] = "extrato_conta"
            elif "fatura" in descricao or "vencimento" in papeis_campo:
                schema["tipo_documento"] = "fatura_cartao"
            else:
                schema["tipo_documento"] = ""
        if not schema.get("convencao"):
            # Só a fatura tem convenção segura (compra positiva); a do extrato a pessoa escolhe.
            schema["convencao"] = "positivo_e_saida" if schema["tipo_documento"] == "fatura_cartao" else ""
        registro.schema = schema
        registro.save(update_fields=["schema"])


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0023_schema_por_familia"),
    ]

    operations = [
        migrations.RunPython(preencher, migrations.RunPython.noop),
    ]
