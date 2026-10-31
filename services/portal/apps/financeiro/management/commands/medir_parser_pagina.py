"""Mede, sem LLM e sem gravar nada, o que o parser verificado do dom2parser tira de uma captura.

Spike do "parser por página" (schema → revisão → parser → reuso): antes de construir schema e tela,
saber se o parser determinístico chega à qualidade da estruturação por LLM. Para cada registro
verificado, classifica as linhas — lançamento, seção (só texto), especial (valor sem data),
informativa (data sem valor) —, propaga a seção, confere a soma contra o total que a própria página
declara e, com `--estruturacao`, compara linha a linha com uma leitura do LLM.

    manage.py medir_parser_pagina <artifact_id> [--estruturacao <id>] [--linhas]

O mapeamento das colunas (data/descrição/valor) ainda é por nome, como o da fatura BB; no fluxo
completo ele vem do schema revisado pela pessoa.
"""

import re
from collections import Counter
from dataclasses import replace
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.artifacts.models import Artifact, EstruturacaoLLM


def _texto(valor) -> str:
    return " ".join(str(valor or "").replace("\xa0", " ").split())


def _coluna(campos, *prefixos):
    return next((c for c in campos for p in prefixos if c.lower().startswith(p)), None)


class Command(BaseCommand):
    help = "Mede o parser verificado do dom2parser numa captura (só leitura, sem LLM)."

    def add_arguments(self, parser):
        parser.add_argument("artifact_id")
        parser.add_argument("--estruturacao", help="EstruturacaoLLM para comparar linha a linha")
        parser.add_argument("--linhas", action="store_true", help="lista as linhas de cada tipo")

    def handle(self, *args, **opts):
        import dom2parser
        from dom2parser.html_io import parse_html
        from dom2parser.parser.executor import extract_records

        from apps.artifacts.extractors import dom_parser
        from apps.artifacts.tasks import _html_do_mhtml, _ler_mhtml
        from apps.financeiro import lancamentos as L

        try:
            artefato = Artifact.objects.get(pk=opts["artifact_id"])
        except (Artifact.DoesNotExist, ValueError):
            raise CommandError("artefato não encontrado") from None
        content = artefato.content or {}
        if not content.get("mhtml_path"):
            raise CommandError("artefato sem MHTML")
        html, _ = _html_do_mhtml(_ler_mhtml(content.get("mhtml_bucket", "inteligencia-aberta-mhtml"),
                                            content["mhtml_path"]))
        if not html:
            raise CommandError("MHTML sem parte text/html")

        out = self.stdout.write
        spec = dom2parser.compress(html).parser
        registros = dom_parser.usable_records(spec)
        out(f"HTML: {len(html)} caracteres · registros verificados: {len(registros)} de {len(spec.records)}")

        root = parse_html(html)
        lancamentos, especiais, informativas, secoes = [], [], [], []
        for record in registros:
            campos = [f.name for f in record.fields]
            c_data, c_valor = _coluna(campos, "data"), _coluna(campos, "valor")
            c_desc = _coluna(campos, "transa", "descri", "histor", "lancamento") or next(
                (c for c in campos if c not in (c_data, c_valor) and not c.lower().startswith("moeda")), None)
            out(f"\n· {record.name} `{record.selector}` — data={c_data} descrição={c_desc} valor={c_valor}")
            if not (c_valor and c_desc):
                out("  sem colunas de valor e descrição: fora da medição")
                continue
            # A classe da célula da descrição separa título de seção ("Restaurantes") de linha
            # informativa ("Cotação do Dólar..."): o formato do parser já captura atributo.
            f_desc = next(f for f in record.fields if f.name == c_desc)
            c_classe = f"{c_desc}__classe"
            com_classe = replace(record, fields=[*record.fields, replace(
                f_desc, name=c_classe, capture="attr", attribute="class", name_source="spike")])
            cabecalho = list(record.skip_when.get("header_values") or [])
            linhas = []
            for r in extract_records(com_classe, root):
                v = r.values
                if [v.get(c, "") for c in campos] == cabecalho or not any(_texto(v.get(c)) for c in campos):
                    continue
                linhas.append({"data": _texto(v.get(c_data)), "descricao": _texto(v.get(c_desc)),
                               "valor": _texto(v.get(c_valor)), "classe": _texto(v.get(c_classe))})
            out(f"  {len(linhas)} linhas com conteúdo")
            classes_lanc = {x["classe"] for x in linhas if x["data"] and x["valor"]}
            secao = ""
            for x in linhas:
                if x["valor"] and x["data"]:
                    lancamentos.append({**x, "secao": secao})
                elif x["valor"]:
                    especiais.append(x)
                elif x["data"] or x["classe"] in classes_lanc:
                    informativas.append(x)
                elif x["descricao"]:
                    secao = x["descricao"]
                    secoes.append(secao)

        out(f"\nLançamentos: {len(lancamentos)} · especiais (valor sem data): {len(especiais)} · "
            f"informativas (data sem valor): {len(informativas)} · seções: {len(secoes)}")
        out(f"Seções: {', '.join(secoes)}")
        com_secao = sum(1 for x in lancamentos if x["secao"])
        out(f"Lançamentos com seção: {com_secao} de {len(lancamentos)}")
        for nome, grupo in (("ESPECIAL", especiais), ("INFORMATIVA", informativas)):
            for x in grupo:
                out(f"  {nome}: {x['descricao'] or '—'} | {x['data']} | {x['valor']}")
        if opts["linhas"]:
            for x in lancamentos:
                out(f"  {x['data']:>10}  {x['valor']:>12}  {x['descricao']}  [{x['secao']}]")

        # Invariante: saldo anterior + lançamentos (pagamento incluso) = total que a página declara.
        # Linhas de total/subtotal são conferência, não entram na soma.
        totais = [x for x in especiais if re.search(r"total", x["descricao"], re.I)]
        saldos = [x for x in especiais if x not in totais]
        def soma(grupo):
            return sum((L.parse_valor(x["valor"]) or Decimal(0) for x in grupo), Decimal(0))
        def natureza(x):        # convenção da fatura de cartão: positivo é saída
            return str(L.natureza_do_movimento(x["descricao"], L.valor_assinado(x["valor"], "positivo_e_saida"),
                                               "fatura_cartao"))
        tipos = Counter(natureza(x) for x in lancamentos)
        gastos = soma([x for x in lancamentos if natureza(x) != "pagamento_fatura"])
        texto = _texto(root.text_content())
        declarado = re.search(r"Total da fatura\s*-?\s*R\$\s*([\d.]+,\d{2})", texto, re.I)
        total_pagina = L.parse_valor(declarado.group(1)) if declarado else None
        out(f"\nTipos: {dict(tipos)}")
        out(f"Gastos (sem pagamento): {gastos}")
        out(f"Saldo anterior + lançamentos: {soma(saldos) + soma(lancamentos)} "
            f"({' + '.join(x['descricao'] for x in saldos) or 'sem saldo'})")
        resumo_totais = ", ".join(f"{x['descricao']}={x['valor']}" for x in totais) or "—"
        out(f"Totais da tabela: {resumo_totais}")
        out(f"Total declarado na página: {total_pagina if total_pagina is not None else 'não encontrado'}")
        if total_pagina is not None:
            fecha = "FECHA" if soma(saldos) + soma(lancamentos) == total_pagina else "NÃO FECHA"
            out(f"Invariante (saldo anterior + lançamentos = total declarado): {fecha}")

        if opts.get("estruturacao"):
            self._comparar(opts["estruturacao"], lancamentos, out, L)

    def _comparar(self, estruturacao_id, lancamentos, out, L):
        try:
            e = EstruturacaoLLM.objects.get(pk=estruturacao_id)
        except (EstruturacaoLLM.DoesNotExist, ValueError):
            raise CommandError("estruturação não encontrada") from None
        linhas_llm, _ = L.extrair_linhas(e.structured_data or {})

        def chave(data, valor):
            return (re.sub(r"\D", "", data)[:4], L.parse_valor(valor))

        parser = Counter(chave(x["data"], x["valor"]) for x in lancamentos)
        llm = Counter(chave(x.data, x.valor) for x in linhas_llm)
        so_parser, so_llm = parser - llm, llm - parser
        out(f"\nComparação com {e.provider}:{e.model_name} ({e.id}): parser {sum(parser.values())} · "
            f"LLM {sum(llm.values())} · em comum {sum((parser & llm).values())}")
        for x in lancamentos:
            k = chave(x["data"], x["valor"])
            if so_parser[k] > 0:
                so_parser[k] -= 1
                out(f"  só no parser: {x['data']} {x['valor']} {x['descricao']}")
        for x in linhas_llm:
            k = chave(x.data, x.valor)
            if so_llm[k] > 0:
                so_llm[k] -= 1
                out(f"  só no LLM:    {x.data} {x.valor} {x.descricao}")
