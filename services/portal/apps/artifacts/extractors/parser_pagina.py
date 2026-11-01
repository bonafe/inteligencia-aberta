"""Do schema de um tipo de página (`SchemaPagina`) ao parser, e do parser aos dados validados.

`montar_parser(schema, html)` sintetiza, sobre a amostra, onde buscar cada coisa:

- **campos únicos** pelo exemplo, com `dom2parser.parser.scalars` (locator ancorado no rótulo,
  nunca no valor). Campo sem exemplo fica em `sem_locator`: declarado, mas ainda não localizável;
- **listas** ligadas a um registro verificado do `dom2parser` pelas **colunas**: a coluna do
  schema casa com o campo do registro em que aparece o valor de exemplo (o nome que o LLM dá à
  estrutura não é o do `dom2parser`). Lista com seções ganha uma captura da classe da célula de
  rótulo: título de seção ("Restaurantes") e linha informativa ("Cotação do Dólar...") só se
  distinguem pela marcação.

O parser é **dado** (`ParserSpec` serializado + o mapeamento), executado por lxml; nunca código.

`executar(parser, schema, html)` extrai e **valida**: campo obrigatório vazio, coluna obrigatória
sem ligação, lista vazia e invariantes de soma que não fecham são problemas — o resultado diz
`ok: False` e por quê, em vez de devolver dado errado em silêncio. Em documento de dinheiro,
linha com descrição e sem valor também é problema (o parser não está achando o valor dela), e
o extrato de conta só valida se o saldo fecha: cada saldo conhecido (campo, coluna de saldo ou
linha de saldo declarada em `linhas_saldo`) tem de ser o anterior mais os lançamentos entre eles.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace
from decimal import Decimal

from .valores import parse_valor

FORMATO = 1
#: Itens devolvidos por lista na prévia (a validação usa todos).
ITENS_NA_PREVIA = 30


def _texto(valor) -> str:
    return " ".join(str(valor or "").split())


def _coluna_rotulo(lista: dict, colunas: dict) -> str | None:
    """A coluna de texto que carrega o título da seção: a primeira coluna `texto` ligada."""
    for col in lista["campos"]:
        if col["tipo"] == "texto" and col["nome"] in colunas:
            return col["nome"]
    return next(iter(colunas), None)


def _ligar_lista(lista: dict, registros: list, root) -> tuple | None:
    """O registro que mais casa com as colunas da lista e o mapeamento coluna → campo do registro.
    Empate: o de mais linhas, o mais fino (a linha do extrato, não o dia que a contém)."""
    from dom2parser.parser.executor import extract_records

    melhor = None
    for registro in registros:
        linhas = [r.values for r in extract_records(registro, root) if not r.skipped]
        nomes = {f.name for f in registro.fields}
        mapa: dict[str, str] = {}
        for col in lista["campos"]:
            exemplo = _texto(col["exemplo"])
            alvo = None
            if exemplo:
                alvo = next((f.name for f in registro.fields
                             if f.name not in mapa.values()
                             and any(_texto(v.get(f.name)) == exemplo for v in linhas)), None)
            if alvo is None and col["nome"] in nomes and col["nome"] not in mapa.values():
                alvo = col["nome"]
            if alvo:
                mapa[col["nome"]] = alvo
        if mapa and (melhor is None or (len(mapa), len(linhas)) > (len(melhor[1]), melhor[2])):
            melhor = (registro, mapa, len(linhas))
    return melhor[:2] if melhor else None


def montar_parser(schema: dict, html: str) -> tuple[dict, list[str]]:
    """O parser do schema sobre a amostra e os avisos do que não deu para ligar."""
    import dom2parser
    from dom2parser.html_io import parse_html
    from dom2parser.parser.scalars import synthesize_scalars
    from dom2parser.parser.spec import ParserSpec

    from .dom_parser import usable_records

    avisos: list[str] = []
    exemplos = {c["nome"]: c["exemplo"] for c in schema["campos"] if c["exemplo"]}
    sem_locator = [c["nome"] for c in schema["campos"] if not c["exemplo"]]
    for nome in sem_locator:
        avisos.append(f"campo '{nome}' sem exemplo: não tem onde ser buscado até haver amostra com valor")
    root = parse_html(html)
    registros = usable_records(dom2parser.compress(html).parser)
    # Valor que também está numa linha da lista (saldo igual a uma compra) vem de fora dela.
    escalares, falhas = synthesize_scalars(html, exemplos, records=registros) if exemplos else ([], [])
    for f in falhas:
        avisos.append(f"campo '{f['name']}': exemplo não localizado na página ({f['reason']})")
        sem_locator.append(f["name"])
    listas, records = [], []
    for lista in schema["listas"]:
        ligacao = _ligar_lista(lista, registros, root)
        if ligacao is None:
            avisos.append(f"lista '{lista['nome']}': nenhuma estrutura repetida da página casa com as colunas")
            continue
        registro, colunas = ligacao
        for col in lista["campos"]:
            if col["nome"] not in colunas:
                avisos.append(f"lista '{lista['nome']}': coluna '{col['nome']}' sem ligação na estrutura")
        campo_classe = None
        rotulo = _coluna_rotulo(lista, colunas)
        if lista["secoes"] and rotulo:
            base = next(f for f in registro.fields if f.name == colunas[rotulo])
            campo_classe = f"{base.name}__classe"
            registro = replace(registro, fields=[*registro.fields, replace(
                base, name=campo_classe, capture="attr", attribute="class", name_source="schema")])
        registro = replace(registro, name=f"lista:{lista['nome']}")
        records.append(registro)
        listas.append({"nome": lista["nome"], "registro": registro.name, "colunas": colunas,
                       "rotulo": rotulo, "campo_classe": campo_classe, "secoes": lista["secoes"]})

    spec = ParserSpec(schema_version=1, records=records, scalars=escalares)
    parser = {"formato": FORMATO, "spec": spec.to_dict(), "listas": listas, "sem_locator": sem_locator}
    return parser, avisos


def _papel(schema_lista: dict, papel: str) -> str | None:
    return next((c["nome"] for c in schema_lista["campos"] if c.get("papel") == papel), None)


def _chave_texto(texto: str) -> str:
    sem = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return " ".join(sem.lower().split())


def _linha_de_saldo(item: dict, schema_lista: dict) -> bool:
    marcas = [_chave_texto(m) for m in schema_lista.get("linhas_saldo") or []]
    descricao = _papel(schema_lista, "descricao") or next(
        (c["nome"] for c in schema_lista["campos"] if c["tipo"] == "texto"), None)
    texto = _chave_texto(item.get(descricao, "")) if descricao else ""
    return bool(texto) and any(texto.startswith(m) for m in marcas if m)


def _executar_lista(cfg: dict, registro, schema_lista: dict, root) -> dict:
    from dom2parser.parser.executor import extract_records

    colunas, rotulo, campo_classe = cfg["colunas"], cfg["rotulo"], cfg["campo_classe"]
    col_valor, col_saldo = _papel(schema_lista, "valor"), _papel(schema_lista, "saldo_linha")
    obrigatorias = [c["nome"] for c in schema_lista["campos"] if c["obrigatorio"] and c["nome"] in colunas]
    cabecalho = list(registro.skip_when.get("header_values") or [])
    originais = [f.name for f in registro.fields if f.name != campo_classe]
    linhas = []
    for r in extract_records(registro, root):
        v = r.values
        if [v.get(n, "") for n in originais] == cabecalho:
            continue
        item = {col: _texto(v.get(campo)) for col, campo in colunas.items()}
        if not any(item.values()):
            continue
        linhas.append((item, _texto(v.get(campo_classe)) if campo_classe else ""))

    completo = (lambda item: all(item[c] for c in obrigatorias)) if obrigatorias else (lambda item: all(item.values()))
    classes_itens = {classe for item, classe in linhas if completo(item)}
    itens, especiais, secoes, informativas, secao = [], [], [], 0, ""
    # Saldos conhecidos ao longo da lista: (quantos itens vieram antes, texto do saldo).
    saldos: list[tuple[int, str]] = []
    for item, classe in linhas:
        so_rotulo = rotulo and item.get(rotulo) and not any(v for k, v in item.items() if k != rotulo)
        if _linha_de_saldo(item, schema_lista):
            texto = item.get(col_saldo or "") or item.get(col_valor or "")
            if texto:
                saldos.append((len(itens), texto))
            continue
        if completo(item):
            itens.append({**item, "secao": secao} if cfg["secoes"] else item)
        elif so_rotulo and cfg["secoes"] and classe not in classes_itens:
            secao = item[rotulo]
            secoes.append(secao)
        elif so_rotulo:
            informativas += 1
        else:
            especiais.append(item)
        if col_saldo and item.get(col_saldo):
            saldos.append((len(itens), item[col_saldo]))
    return {"itens": itens, "especiais": especiais, "secoes": secoes, "informativas": informativas,
            "saldos": [{"apos_itens": n, "valor": t} for n, t in saldos]}


def _assinado(texto: str, convencao: str) -> Decimal | None:
    """O valor com o sinal do documento: negativo é saída/devedor."""
    valor = parse_valor(texto)
    if valor is None:
        return None
    if convencao == "sufixo_dc" and _texto(texto).upper().endswith("D"):
        return -abs(valor)
    if convencao == "sufixo_dc" and _texto(texto).upper().endswith("C"):
        return abs(valor)
    return valor


def _movimento(item: dict, schema_lista: dict, convencao: str) -> Decimal:
    if convencao == "colunas_debito_credito":
        debito = parse_valor(item.get(_papel(schema_lista, "debito") or "", "")) or Decimal(0)
        credito = parse_valor(item.get(_papel(schema_lista, "credito") or "", "")) or Decimal(0)
        return abs(credito) - abs(debito)
    valor = _assinado(item.get(_papel(schema_lista, "valor") or "", ""), convencao) or Decimal(0)
    return -valor if convencao == "positivo_e_saida" else valor


def _conferencia_de_saldo(schema: dict, campos: dict, listas: dict) -> dict | None:
    """Cada saldo conhecido é o anterior mais os lançamentos entre eles. None: menos de dois saldos."""
    convencao = schema.get("convencao", "")
    papel_campo = {c["papel"]: c["nome"] for c in schema["campos"] if c.get("papel")}
    for schema_lista in schema["listas"]:
        resultado = listas.get(schema_lista["nome"])
        if resultado is None or not ({"valor"} & {c.get("papel") for c in schema_lista["campos"]}
                                     or convencao == "colunas_debito_credito"):
            continue
        itens = resultado["itens"]
        marcos = [(s["apos_itens"], s["valor"]) for s in resultado.get("saldos", [])]
        if campos.get(papel_campo.get("saldo_anterior", "")):
            marcos.insert(0, (0, campos[papel_campo["saldo_anterior"]]))
        if campos.get(papel_campo.get("saldo_final", "")):
            marcos.append((len(itens), campos[papel_campo["saldo_final"]]))
        marcos = [(n, v) for n, t in marcos if (v := _assinado(t, convencao)) is not None]
        if len(marcos) < 2:
            continue
        tolerancia = Decimal(str(schema_lista.get("tolerancia_saldo") or "0"))
        falha, maior = None, Decimal(0)
        for (n_a, saldo_a), (n_b, saldo_b) in zip(marcos, marcos[1:]):
            obtido = saldo_a + sum((_movimento(i, schema_lista, convencao) for i in itens[n_a:n_b]), Decimal(0))
            maior = max(maior, abs(saldo_b - obtido))
            if abs(saldo_b - obtido) > tolerancia:
                falha = (saldo_b, obtido, n_a, n_b)
                break
        fora = marcos[0][0] + len(itens) - marcos[-1][0]
        descricao = f"saldo de '{schema_lista['nome']}': {len(marcos) - 1} trecho(s) entre saldos conhecidos"
        if maior and not falha:
            descricao += f"; maior diferença R$ {maior} (tolerância R$ {tolerancia} por trecho)"
        if fora:
            descricao += f"; {fora} lançamento(s) fora de qualquer trecho, não conferidos"
        if falha:
            esperado, obtido, n_a, n_b = falha
            return {"descricao": f"{descricao} (falhou nos lançamentos {n_a + 1} a {n_b})",
                    "esperado": str(esperado), "obtido": str(obtido), "ok": False}
        return {"descricao": descricao, "esperado": str(marcos[-1][1]), "obtido": str(marcos[-1][1]), "ok": True}
    return None


def executar(parser: dict, schema: dict, html: str) -> dict:
    """Extrai com o parser e valida contra o schema. Nunca levanta por dado ruim: diz o problema."""
    from dom2parser.html_io import parse_html
    from dom2parser.parser.scalars import extract_scalar
    from dom2parser.parser.spec import ParserSpec

    spec = ParserSpec.from_dict(parser["spec"])
    root = parse_html(html)
    problemas: list[str] = []

    campos = {c["nome"]: "" for c in schema["campos"]}
    for entry in spec.scalars:
        if entry.name in campos:
            campos[entry.name] = extract_scalar(entry, root)
    for c in schema["campos"]:
        if c["obrigatorio"] and not campos[c["nome"]]:
            problemas.append(f"campo obrigatório '{c['nome']}' vazio")

    registros = {r.name: r for r in spec.records}
    ligadas = {cfg["nome"]: cfg for cfg in parser["listas"]}
    listas = {}
    for nome, schema_lista in ((l["nome"], l) for l in schema["listas"]):
        cfg = ligadas.get(nome)
        if cfg is None:
            problemas.append(f"lista '{nome}' sem estrutura ligada")
            continue
        for col in schema_lista["campos"]:
            if col["obrigatorio"] and col["nome"] not in cfg["colunas"]:
                problemas.append(f"coluna obrigatória '{nome}.{col['nome']}' sem ligação")
        listas[nome] = _executar_lista(cfg, registros[cfg["registro"]], schema_lista, root)
        if not listas[nome]["itens"]:
            problemas.append(f"lista '{nome}' sem itens")
        colunas_valor = [c["nome"] for c in schema_lista["campos"] if c.get("papel") in ("valor", "debito", "credito")]
        colunas_texto = ([c["nome"] for c in schema_lista["campos"] if c.get("papel") in ("descricao", "complemento")]
                         or [c["nome"] for c in schema_lista["campos"] if c["tipo"] == "texto"])
        col_saldo = _papel(schema_lista, "saldo_linha")
        sem_valor = [e for e in listas[nome]["especiais"]
                     if schema.get("convencao") and colunas_valor and not any(e.get(c) for c in colunas_valor)
                     and any(e.get(c) for c in colunas_texto) and not (col_saldo and e.get(col_saldo))]
        if sem_valor:
            exemplo = next(sem_valor[0][c] for c in colunas_texto if sem_valor[0].get(c))
            problemas.append(f"lista '{nome}': {len(sem_valor)} linha(s) com descrição e sem valor "
                             f"(ex.: '{exemplo}'): o parser não está achando o valor delas")

    invariantes = []
    for inv in schema["invariantes"]:
        itens = listas.get(inv["lista"], {}).get("itens", [])
        obtido = sum((parse_valor(i.get(inv["campo"], "")) or Decimal(0) for i in itens), Decimal(0))
        obtido += sum((parse_valor(campos.get(m, "")) or Decimal(0) for m in inv["mais"]), Decimal(0))
        esperado = parse_valor(campos.get(inv["igual_a"], ""))
        ok = esperado is not None and obtido == esperado
        invariantes.append({"descricao": inv["descricao"] or f"soma de {inv['lista']}.{inv['campo']}",
                            "esperado": str(esperado) if esperado is not None else None,
                            "obtido": str(obtido), "ok": ok})
        if not ok:
            problemas.append(f"invariante não fecha: {invariantes[-1]['descricao']} "
                             f"(esperado {invariantes[-1]['esperado']}, obtido {obtido})")

    if schema.get("tipo_documento") == "extrato_conta":
        conferencia = _conferencia_de_saldo(schema, campos, listas)
        if conferencia is None:
            problemas.append("extrato de conta sem dois saldos conhecidos (saldo anterior e final, coluna ou "
                             "linhas de saldo): não há como conferir se faltou lançamento")
        else:
            invariantes.append(conferencia)
            if not conferencia["ok"]:
                problemas.append(f"saldo não fecha: {conferencia['descricao']} "
                                 f"(esperado {conferencia['esperado']}, obtido {conferencia['obtido']})")

    return {"campos": campos, "listas": listas, "invariantes": invariantes,
            "validacao": {"ok": not problemas, "problemas": problemas}}


def previa(resultado: dict) -> dict:
    """O resultado com as listas cortadas para caber na tela."""
    listas = {
        nome: {**l, "itens": l["itens"][:ITENS_NA_PREVIA], "total_itens": len(l["itens"]),
               "especiais": l["especiais"][:ITENS_NA_PREVIA]}
        for nome, l in resultado["listas"].items()
    }
    return {**resultado, "listas": listas}


def campos_localizados(parser: dict) -> dict:
    """Como cada campo único foi localizado (estratégia e rótulo), para a tela."""
    return {s["name"]: {"strategy": s["strategy"], "label": s["label"]} for s in parser["spec"].get("scalars", [])}


__all__ = ["FORMATO", "campos_localizados", "executar", "montar_parser", "previa"]
