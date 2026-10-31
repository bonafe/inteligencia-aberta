"""Schema de um tipo de página: o que um parser determinístico vai extrair de todas as
páginas daquele tipo (as próximas faturas do mesmo banco, por exemplo).

O LLM **declara** o schema uma vez, a partir de uma página de amostra: campos únicos
(vencimento, total), listas (lançamentos) e invariantes que a página permite conferir
(a soma da lista bate com o total). Cada campo leva um exemplo copiado da amostra — é
dele que o `dom2parser` sintetiza o locator depois (`dom2parser.parser.scalars`).

Declarado, não inferido: a tentativa anterior de reuso (commit `c6d8d1c`) guardava o que
a primeira captura *tinha* e perdia para sempre o campo que estava vazio nela. Aqui o
schema pode declarar campo sem valor na amostra, e uma pessoa o revisa antes de valer.

`normalizar_schema` é a régua determinística sobre a resposta do LLM: nomes em
snake_case e únicos, tipos conhecidos, invariantes que apontam para campos existentes.
O que ela descarta vira aviso, nunca silêncio. Sem Django, para testar isolado.
"""

from __future__ import annotations

import re
import unicodedata

from .llm_common import _extract_json, gerar_texto

TIPOS = ("texto", "data", "valor", "numero", "inteiro", "url")
#: Linhas de amostra por estrutura repetida no resumo enviado ao LLM.
AMOSTRAS_POR_ESTRUTURA = 3
EXEMPLO_MAXIMO = 500
MAX_TOKENS_RESPOSTA = 4096

_SYSTEM = """\
Você define o SCHEMA de dados de um TIPO de página, para que um parser determinístico \
extraia os mesmos campos de todas as páginas desse tipo (as próximas faturas do mesmo banco, \
os próximos processos do mesmo tribunal). Você NÃO extrai todos os dados: declara quais \
campos existem e dá UM exemplo de cada, copiado da página.

Você recebe o HTML completo e limpo da página e as ESTRUTURAS REPETIDAS que um analisador \
já encontrou e verificou (seletor, campos e algumas linhas reais).

Regras:
1. CAMPOS são valores que aparecem uma vez (vencimento, total, titular, número do documento). \
Para cada um: nome em português snake_case, tipo, descrição curta, obrigatorio (true se toda \
página desse tipo terá) e exemplo copiado EXATAMENTE como aparece (não converta, não formate).
2. LISTAS são dados que se repetem (lançamentos, itens, partes). Para cada lista: os campos de \
uma linha, cada um com exemplo copiado de UMA MESMA linha real; "secoes": true quando a lista é \
dividida por linhas-título (ex.: "Restaurantes", "Supermercados"); "estrutura": o nome da \
estrutura repetida correspondente, quando houver.
3. Declare também campos próprios desse tipo de documento que estejam vazios nesta página \
(exemplo "", obrigatorio false).
4. INVARIANTES são conferências que a página permite: a soma de uma coluna da lista, mais \
campos opcionais, igual a um campo total. Declare só se os números desta página confirmarem.
5. Ignore menus, rodapés, avisos e propaganda.

Tipos: texto, data, valor (dinheiro), numero, inteiro, url.

RETORNE APENAS O JSON ABAIXO. Nada antes, nada depois, sem markdown.

{
  "descricao": "<que tipo de página é, uma frase>",
  "campos": [
    {"nome": "vencimento", "tipo": "data", "descricao": "...", "obrigatorio": true, "exemplo": "..."}
  ],
  "listas": [
    {"nome": "lancamentos", "descricao": "...", "secoes": true, "estrutura": "<nome ou null>",
     "campos": [{"nome": "data", "tipo": "data", "descricao": "...", "obrigatorio": true, "exemplo": "..."}]}
  ],
  "invariantes": [
    {"tipo": "soma", "lista": "lancamentos", "campo": "valor", "mais": ["saldo_anterior"],
     "igual_a": "total_fatura", "descricao": "..."}
  ]
}
"""


def _slug(nome) -> str:
    texto = unicodedata.normalize("NFKD", str(nome or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")[:80]


def _unico(nome: str, usados: set[str]) -> str:
    final, n = nome, 2
    while final in usados:
        final, n = f"{nome}_{n}", n + 1
    usados.add(final)
    return final


def _campos(brutos, onde: str, avisos: list[str]) -> list[dict]:
    saida, usados = [], set()
    for bruto in brutos if isinstance(brutos, list) else []:
        if not isinstance(bruto, dict):
            continue
        nome = _slug(bruto.get("nome"))
        if not nome:
            avisos.append(f"{onde}: campo sem nome descartado")
            continue
        tipo = str(bruto.get("tipo") or "").strip().lower()
        if tipo not in TIPOS:
            avisos.append(f"{onde}.{nome}: tipo {tipo!r} desconhecido, usando 'texto'")
            tipo = "texto"
        exemplo = bruto.get("exemplo")
        saida.append({
            "nome": _unico(nome, usados),
            "tipo": tipo,
            "descricao": str(bruto.get("descricao") or "").strip()[:300],
            "obrigatorio": bool(bruto.get("obrigatorio")),
            "exemplo": "" if exemplo is None else " ".join(str(exemplo).split())[:EXEMPLO_MAXIMO],
        })
    return saida


def normalizar_schema(bruto) -> tuple[dict, list[str]]:
    """O schema na forma canônica e os avisos sobre o que foi corrigido ou descartado."""
    avisos: list[str] = []
    if not isinstance(bruto, dict):
        return {"descricao": "", "campos": [], "listas": [], "invariantes": []}, ["resposta não é um objeto JSON"]

    campos = _campos(bruto.get("campos"), "campos", avisos)
    nomes_campos = {c["nome"] for c in campos}

    listas, usados = [], set()
    for lista in bruto.get("listas") if isinstance(bruto.get("listas"), list) else []:
        if not isinstance(lista, dict):
            continue
        nome = _slug(lista.get("nome"))
        if not nome:
            avisos.append("listas: lista sem nome descartada")
            continue
        colunas = _campos(lista.get("campos"), f"listas.{nome}", avisos)
        if not colunas:
            avisos.append(f"listas.{nome}: lista sem campos descartada")
            continue
        estrutura = lista.get("estrutura")
        listas.append({
            "nome": _unico(nome, usados),
            "descricao": str(lista.get("descricao") or "").strip()[:300],
            "secoes": bool(lista.get("secoes")),
            "estrutura": str(estrutura).strip() if estrutura else None,
            "campos": colunas,
        })

    colunas_por_lista = {l["nome"]: {c["nome"] for c in l["campos"]} for l in listas}
    invariantes = []
    for inv in bruto.get("invariantes") if isinstance(bruto.get("invariantes"), list) else []:
        if not isinstance(inv, dict):
            continue
        tipo, lista = str(inv.get("tipo") or "").lower(), _slug(inv.get("lista"))
        campo, igual_a = _slug(inv.get("campo")), _slug(inv.get("igual_a"))
        mais = [_slug(m) for m in inv.get("mais") or [] if _slug(m)]
        motivo = None
        if tipo != "soma":
            motivo = f"tipo {tipo!r} não suportado (só 'soma')"
        elif campo not in colunas_por_lista.get(lista, set()):
            motivo = f"'{lista}.{campo}' não existe"
        elif igual_a not in nomes_campos:
            motivo = f"'{igual_a}' não é um campo"
        elif any(m not in nomes_campos for m in mais):
            motivo = f"'mais' cita campo inexistente: {mais}"
        if motivo:
            avisos.append(f"invariante descartado: {motivo}")
            continue
        invariantes.append({"tipo": "soma", "lista": lista, "campo": campo, "mais": mais, "igual_a": igual_a,
                            "descricao": str(inv.get("descricao") or "").strip()[:300]})

    schema = {
        "descricao": str(bruto.get("descricao") or "").strip()[:500],
        "campos": campos, "listas": listas, "invariantes": invariantes,
    }
    return schema, avisos


def schema_vazio(schema: dict) -> bool:
    return not schema["campos"] and not schema["listas"]


def resumo_estruturas(spec, html: str) -> str:
    """As estruturas repetidas verificadas pelo dom2parser, com algumas linhas reais: o
    suficiente para o LLM ligar cada lista do schema a uma estrutura."""
    from dom2parser.html_io import parse_html
    from dom2parser.parser.executor import extract_records

    from .dom_parser import usable_records

    registros = usable_records(spec)
    if not registros:
        return "(nenhuma estrutura repetida verificada)"
    root = parse_html(html)
    partes = []
    for r in registros:
        campos = ", ".join(f"{f.name} ({f.type})" for f in r.fields) or "_text"
        linhas = [x.values for x in extract_records(r, root)
                  if not x.skipped and any(v for v in x.values.values())][:AMOSTRAS_POR_ESTRUTURA]
        amostras = "\n".join(f"    {v}" for v in linhas)
        partes.append(f"- estrutura: {r.name}\n  seletor: {r.selector} ({r.count} ocorrências)\n"
                      f"  campos: {campos}\n  linhas de exemplo:\n{amostras}")
    return "\n".join(partes)


def gerar_schema(
    provider: str, model_name: str, html_enxuto: str, estruturas: str, url: str, *,
    titulo: str = "", subject_id=None, tenant_id=None, maquina_id=None,
) -> dict:
    """Pede o schema ao LLM. Devolve {schema, avisos, maquina_id}. Levanta em erro de
    chamada ou JSON ilegível — quem chama decide falhou."""
    from apps.events.models import Finalidade

    from .estruturacao_manual import num_ctx_para

    if not html_enxuto:
        raise ValueError("a captura não tem HTML para enviar ao LLM")
    titulo_line = f"Título: {titulo}\n" if titulo else ""
    prompt = (f"URL: {url}\n{titulo_line}\nESTRUTURAS REPETIDAS VERIFICADAS:\n{estruturas}\n\n"
              f"HTML da página:\n{html_enxuto}")
    texto, maquina_usada = gerar_texto(
        provider, model_name, _SYSTEM, prompt, max_tokens=MAX_TOKENS_RESPOSTA,
        num_ctx=num_ctx_para(_SYSTEM, prompt),
        finalidade=Finalidade.SCHEMA_PAGINA, subject_id=subject_id, tenant_id=tenant_id, maquina_id=maquina_id,
    )
    schema, avisos = normalizar_schema(_extract_json(texto))
    return {"schema": schema, "avisos": avisos, "maquina_id": maquina_usada}
