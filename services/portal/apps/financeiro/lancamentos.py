"""Leitura de um documento → alegações → projeção `Movimento` (ADR 021).

Três passos, cada um com seu lugar:

1. **Parsing puro** (`extrair_linhas`, `linhas_do_schema`, `valor_assinado`, `natureza_do_movimento`,
   `parcela_da_descricao`, `parse_data`, `normalizar_descritor`): sem banco e sem LLM. Com o
   parser de página, o schema diz o **papel** de cada coluna (data, descrição, valor, D/C...) e a
   **convenção** de sinal; numa estruturação por LLM os nomes variam entre execuções
   ("transação", "descricao", "historico"), e a busca é por forma.
2. **Registro**: a leitura escolhida de um documento vira alegações pelo serviço único
   `registrar_alegacao`, com evidência apontando para a linha. Duas fontes, intercambiáveis:
   uma estruturação por LLM (`registrar`) ou uma extração **válida** do parser aprovado do tipo
   de página (`registrar_extracao`, sem LLM; automática quando o schema tem `alimenta_gastos`).
   A leitura registrada passa a ser a escolhida do documento (`LeituraLancamentos`); as
   alegações das outras leituras continuam ativas (ninguém as desmentiu), só não alimentam os
   Gastos. Trocar e voltar não perde nada, e a fatura nunca é somada duas vezes.
3. **Projeção** (`projetar_artefato`, `reconstruir`): das alegações ativas para `Movimento`, com
   a conta, o valor com o sinal da conta (negativo = saída), a natureza (compra, pagamento de
   fatura, entrada...), a competência (o mês em que o dinheiro saiu: o vencimento da fatura) e a
   parcela n/m. Apagar e refazer é seguro; contas, fusões e categorias são estado da pessoa.
"""

import calendar
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.db import transaction

from apps.artifacts.alegacoes import LITERAL_MAXIMO, EvidenciaEntrada, registrar_alegacao, truncar_trecho
from apps.artifacts.extractors.valores import parse_valor
from apps.artifacts.models import Claim, EstruturacaoLLM, ExtracaoPagina
from config.conteudo_hash import formato_valido as hash_valido

from .models import (
    CategoriaGasto, Conta, DescritorEstabelecimento, Estabelecimento, LeituraLancamentos, Movimento,
)

PRODUTOR = "estruturacao-llm"
#: Subir quando a forma das alegações mudar (predicados, sujeito, evidência).
PRODUTOR_VERSAO = "1"
LOCALIZADOR_TIPO = "estruturacao-llm"
PRODUTOR_PARSER = "parser-pagina"
PRODUTORES = (PRODUTOR, PRODUTOR_PARSER)

CATEGORIAS_PADRAO = (
    "Mercado", "Restaurante e bar", "Café e lanche", "Assinaturas e software", "Farmácia e saúde",
    "Transporte", "Viagem e hospedagem", "Casa", "Compras", "Impostos e tarifas", "Outros",
)

#: Se a data cair mais que isto depois da referência da fatura, é do ano anterior. Curta de
#: propósito: parcela traz a data da compra original ("15/12 ... PARC 10/10" numa fatura de outubro).
FOLGA_ANO = timedelta(days=10)

_SUJEITO = re.compile(r"#(estruturacao|parser)/([0-9a-f-]{36})/lancamento/(\d+)$")
_LISTAS_GENERICAS = {"lancamentos", "itens", "transacoes", "linhas", "movimentacoes", "compras"}
_CHAVES_DESCRICAO = ("descricao", "transacao", "historico", "estabelecimento", "lancamento", "detalhe", "nome")
_MESES = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


# ── parsing puro ────────────────────────────────────────────────────────────

def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _chave(nome: str) -> str:
    return _sem_acento(str(nome)).lower().strip()


@dataclass(frozen=True)
class Linha:
    indice: int
    data: str
    valor: str
    descricao: str
    secao: str
    original: dict
    documento: str = ""
    #: "D" ou "C" quando a página separa o sentido do valor (coluna D/C, débito e crédito).
    sentido: str = ""


#: Que conta cada tipo de documento descreve.
TIPO_CONTA = {"fatura_cartao": Conta.Tipo.CARTAO, "extrato_conta": Conta.Tipo.CORRENTE}
#: A leitura que não diz o que é (estruturação por LLM, schema anterior aos papéis) é tratada
#: como fatura de cartão, que era o único documento antes da ADR 021.
TIPO_DOCUMENTO_PADRAO, CONVENCAO_PADRAO = "fatura_cartao", "positivo_e_saida"


def _campos(item: dict) -> dict[str, str] | None:
    """Mapeia as chaves de uma linha para data/valor/descrição/seção, ou None se não for lançamento."""
    textos = {_chave(k): (k, v) for k, v in item.items() if isinstance(v, (str, int, float)) and str(v).strip()}
    valor = textos.get("valor") or next(
        (kv for k, kv in textos.items()
         if k.startswith("valor") and not any(m in k for m in ("us", "dolar", "usd", "moeda"))), None)
    data = textos.get("data") or next((kv for k, kv in textos.items() if k.startswith("data")), None)
    if valor is None or data is None:
        return None
    descricao = next((textos[k] for k in _CHAVES_DESCRICAO if k in textos), None)
    if descricao is None:
        usados = {"moeda", "secao"}
        descricao = next(
            (kv for k, kv in textos.items()
             if not k.startswith(("valor", "data")) and k not in usados and isinstance(kv[1], str)), None)
    if descricao is None:
        return None
    secao = textos.get("secao")
    return {
        "data": str(data[1]).strip(), "valor": str(valor[1]).strip(), "descricao": str(descricao[1]).strip(),
        "secao": str(secao[1]).strip() if secao else "",
    }


def extrair_linhas(structured_data) -> tuple[list[Linha], str]:
    """Os lançamentos de uma estruturação, em ordem, e o período/vencimento da fatura ("" se não houver).

    Linha é qualquer objeto de uma lista de objetos com data e valor. Linhas sem valor (cotação do
    dólar, observações) ficam de fora. A seção vem do campo da linha ou, quando a fatura separa as
    seções em listas, do nome da lista.
    """
    linhas: list[Linha] = []
    periodo = ""

    def visitar(no, nome_lista: str):
        nonlocal periodo
        if isinstance(no, dict):
            for k, v in no.items():
                ck = _chave(k)
                if not periodo and isinstance(v, str) and v.strip() and any(
                        m in ck for m in ("vencimento", "periodo", "referencia", "competencia")):
                    periodo = v.strip()
                visitar(v, ck)
        elif isinstance(no, list):
            for item in no:
                if isinstance(item, dict) and (campos := _campos(item)):
                    secao = campos["secao"] or ("" if nome_lista in _LISTAS_GENERICAS else nome_lista)
                    linhas.append(Linha(len(linhas), campos["data"], campos["valor"], campos["descricao"],
                                        secao, item))
                else:
                    visitar(item, nome_lista)

    visitar(structured_data, "")
    return linhas, periodo


def schema_tem_papeis(schema: dict) -> bool:
    """O schema diz qual coluna é o valor (ou débito e crédito): dá para ler sem adivinhar."""
    papeis = {c.get("papel") for l in schema.get("listas", []) for c in l.get("campos", [])}
    return "valor" in papeis or {"debito", "credito"} <= papeis


def linhas_do_schema(dados: dict, schema: dict) -> tuple[list[Linha], dict]:
    """Os movimentos de uma extração do parser, lidos pelos papéis do schema, e o contexto do
    documento: {"periodo", "conta", "titular"} (vazios quando o schema não os marca)."""
    campos = dados.get("campos", {})
    ctx = {c["papel"]: campos.get(c["nome"], "") for c in schema.get("campos", []) if c.get("papel")}
    contexto = {"periodo": ctx.get("vencimento") or ctx.get("periodo") or "",
                "conta": ctx.get("identifica_conta", ""), "titular": ctx.get("titular", "")}
    linhas: list[Linha] = []
    for lista in schema.get("listas", []):
        papel = {c["papel"]: c["nome"] for c in lista["campos"] if c.get("papel") not in ("", None, "complemento")}
        complementos = [c["nome"] for c in lista["campos"] if c.get("papel") == "complemento"]
        if "valor" not in papel and not {"debito", "credito"} <= papel.keys():
            continue
        for item in dados.get("listas", {}).get(lista["nome"], {}).get("itens", []):
            sentido = ""
            if "valor" in papel:
                valor = str(item.get(papel["valor"], "")).strip()
            else:
                debito = str(item.get(papel["debito"], "")).strip()
                valor, sentido = (debito, "D") if debito else (str(item.get(papel["credito"], "")).strip(), "C")
            if "dc" in papel and str(item.get(papel["dc"], "")).strip():
                sentido = str(item[papel["dc"]]).strip()[:1].upper()
            if not valor:
                continue
            partes = [item.get(papel.get("descricao", ""), "")] + [item.get(n, "") for n in complementos]
            descricao = " ".join(str(p).strip() for p in partes if p and str(p).strip())
            linhas.append(Linha(len(linhas), str(item.get(papel.get("data", ""), "")).strip(), valor,
                                descricao or "(sem descrição)", str(item.get("secao", "")), item,
                                documento=str(item.get(papel.get("documento", ""), "")).strip(), sentido=sentido))
    return linhas, contexto


def data_referencia(periodo: str) -> date | None:
    """A data que fecha a fatura: "15/10/2026", "Out/26", "outubro de 2026", "10/2026" (fim do mês)."""
    texto = _chave(periodo)
    if m := re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", texto):
        dia, mes, ano = (int(g) for g in m.groups())
        ano += 2000 if ano < 100 else 0
        try:
            return date(ano, mes, dia)
        except ValueError:
            return None
    mes = ano = None
    if m := re.search(r"\b([a-z]{3})[a-z]*\.?\s*(?:/|de|-)?\s*(\d{2,4})\b", texto):
        mes, ano = _MESES.get(m.group(1)), int(m.group(2))
    elif m := re.search(r"\b(\d{1,2})\s*/\s*(\d{4})\b", texto):
        mes, ano = int(m.group(1)), int(m.group(2))
    if not mes or not 1 <= mes <= 12:
        return None
    ano += 2000 if ano < 100 else 0
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def parse_data(texto: str, referencia: date) -> date | None:
    """"16/09" → data com o ano inferido da referência; "16/09/26", "2026-09-16" também valem."""
    texto = (texto or "").strip()
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", texto):
        ano, mes, dia = (int(g) for g in m.groups())
    elif m := re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", texto):
        dia, mes = int(m.group(1)), int(m.group(2))
        if m.group(3):
            ano = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        else:
            ano = referencia.year
            try:
                if date(ano, mes, dia) > referencia + FOLGA_ANO:
                    ano -= 1
            except ValueError:
                return None
    else:
        return None
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def normalizar_descritor(descricao: str) -> str:
    """O texto da maquininha sem o que varia entre compras no mesmo lugar: números de pedido e
    telefone, parcela, o código de país/UF no fim. "OPENAI *CHATGPT SUBSCR 4158799686 CA" →
    "OPENAI CHATGPT SUBSCR"."""
    texto = _sem_acento(descricao).upper().replace("*", " ")
    texto = re.sub(r"\bPARC(ELA)?\s*\d{1,2}\s*/\s*\d{1,2}\b", " ", texto)
    texto = re.sub(r"\b\d{2,4}[-.]\d{3,4}[-.]\d{4}\b", " ", texto)       # telefone
    texto = re.sub(r"\b\d{5,}\b", " ", texto)                               # pedido, assinatura
    texto = re.sub(r"\b\d{1,2}/\d{1,2}\b\s*$", " ", texto)                  # parcela solta no fim
    texto = re.sub(r"[^\w\s&.-]", " ", texto)
    palavras = texto.split()
    if len(palavras) > 1 and re.fullmatch(r"[A-Z]{2}", palavras[-1]):
        palavras.pop()                                                      # país ou UF
    return " ".join(palavras).strip(" .-")[:300]


def valor_assinado(texto: str, convencao: str, sentido: str = "") -> Decimal | None:
    """O valor com o sinal da conta (negativo = saída), conforme a convenção do documento."""
    valor = parse_valor(texto)
    if valor is None:
        return None
    if convencao == "positivo_e_saida":
        return -valor
    if convencao in ("sufixo_dc", "colunas_debito_credito"):
        if not sentido and (m := re.search(r"\b([DC])\s*$", (texto or "").strip().upper())):
            sentido = m.group(1)
        if sentido in ("D", "C"):
            return -abs(valor) if sentido == "D" else abs(valor)
    return valor


_TARIFA = re.compile(r"\b(IOF|ANUIDADE|TARIFA|JUROS|ENCARGOS?|MULTA)\b")
_PAGAMENTO = re.compile(r"\b(PGTO|PAGTO|PAGAMENTO)\b")
_PAGAMENTO_CARTAO = re.compile(r"\b(PGTO|PAGTO|PAGAMENTO)\b.*\bCARTAO\b")


def natureza_do_movimento(descricao: str, valor: Decimal | None, tipo_documento: str) -> str:
    """A natureza padrão pelo tipo de documento e pelo sinal; a conciliação refina depois.

    Fatura: saída é compra (ou tarifa); entrada é o pagamento que quita a fatura ou um estorno.
    Extrato: entrada é entrada; saída é tarifa, pagamento de fatura de cartão ou saída a terceiros.
    """
    N = Movimento.Natureza
    if valor is None:
        return N.OUTRO
    texto = _sem_acento(descricao).upper()
    if tipo_documento == "extrato_conta":
        if valor > 0:
            return N.ENTRADA
        if _PAGAMENTO_CARTAO.search(texto):
            return N.PAGAMENTO_FATURA
        return N.TARIFA if _TARIFA.search(texto) else N.SAIDA_TERCEIROS
    if valor > 0:
        return N.PAGAMENTO_FATURA if _PAGAMENTO.search(texto) else N.ESTORNO
    return N.TARIFA if _TARIFA.search(texto) else N.COMPRA


_PARCELA = re.compile(r"\bPARC(?:ELA)?\.?\s*(\d{1,2})\s*(?:/|DE)\s*(\d{1,2})\b")


def parcela_da_descricao(descricao: str) -> tuple[int, int] | None:
    """"Vindi Catum PARC 08/12 Campinas BR" → (8, 12); "PARCELA 8 DE 12" também."""
    m = _PARCELA.search(_sem_acento(descricao).upper())
    if not m:
        return None
    n, total = int(m.group(1)), int(m.group(2))
    return (n, total) if 0 < n <= total else None


def competencia(tipo_documento: str, referencia: date | None, data: date | None) -> date | None:
    """O mês em que o dinheiro saiu: na fatura, o do vencimento; no extrato, o da data."""
    base = referencia if tipo_documento != "extrato_conta" and referencia else data
    return date(base.year, base.month, 1) if base else None


def nome_legivel(descritor: str) -> str:
    return " ".join(p if len(p) <= 3 and not p.isalpha() else p.capitalize() for p in descritor.split())


# ── registro ────────────────────────────────────────────────────────────────

@dataclass
class Resultado:
    lancamentos: int = 0
    alegacoes_criadas: int = 0
    #: Outra leitura alimentava os Gastos deste documento e deixou de alimentar.
    substituiu: bool = False


def _autor(document_text, artefato) -> str:
    url = document_text.source_url or (artefato.content or {}).get("url", "")
    host = urlsplit(url).hostname if url else None
    return f"dominio:{host}" if host else f"urn:uuid:{artefato.id}"


def base_sujeito(blob_hash: str, estruturacao_id) -> str:
    return f"mencao:{blob_hash}#estruturacao/{estruturacao_id}"


@dataclass
class _Leitura:
    """De onde vêm os lançamentos: tudo o que muda entre estruturação por LLM e parser."""

    base: str
    fonte: str
    fonte_id: object
    produtor: str
    produtor_versao: str
    modelo: str
    localizador_tipo: str
    localizador: dict
    rotulo: str
    payload: dict


def _registrar(document_text, linhas: list[Linha], contexto: dict, leitura: _Leitura, user_id) -> Resultado:
    from apps.artifacts.tasks import correlacao_do_artefato
    from apps.events.emit import emit

    artefato = document_text.document
    if not hash_valido(artefato.blob_hash or ""):
        raise ValueError("a captura não tem hash de conteúdo; sem ele não há evidência")

    resultado = Resultado(lancamentos=len(linhas))
    comum = dict(
        correlation_id=correlacao_do_artefato(artefato.id), subject_type="artifact", subject_id=artefato.id,
        tenant_id=artefato.tenant_id, user_id=user_id, source="portal",
    )
    if not linhas:
        emit("financeiro.lancamentos", "vazio", message=f"{leitura.rotulo} não tem lista de lançamentos",
             payload=leitura.payload, **comum)
        return resultado

    autor = _autor(document_text, artefato)

    def alegar(sujeito, predicado, valor, localizador, trecho):
        _, criada = registrar_alegacao(
            artefato=artefato, sujeito_ref=sujeito, predicado=predicado, objeto_literal=valor,
            autor_ref=autor, produtor=leitura.produtor, produtor_versao=leitura.produtor_versao,
            modelo=leitura.modelo,
            evidencias=[EvidenciaEntrada(blob_hash=artefato.blob_hash, localizador_tipo=leitura.localizador_tipo,
                                         localizador=localizador, trecho=trecho)],
        )
        resultado.alegacoes_criadas += int(criada)

    with transaction.atomic():
        # O que o documento diz dele mesmo: o período/vencimento, de que conta é, de quem.
        for predicado, chave in (("ia:periodo", "periodo"), ("ia:conta", "conta"), ("ia:titular", "titular")):
            if valor := str(contexto.get(chave) or "").strip():
                alegar(leitura.base, predicado, valor[:LITERAL_MAXIMO], dict(leitura.localizador), truncar_trecho(valor))
        for linha in linhas:
            sujeito = f"{leitura.base}/lancamento/{linha.indice}"
            trecho = truncar_trecho(json.dumps(linha.original, ensure_ascii=False))
            loc = {**leitura.localizador, "indice": linha.indice}
            alegar(sujeito, "ia:data", linha.data, loc, trecho)
            alegar(sujeito, "ia:valor", linha.valor, loc, trecho)
            alegar(sujeito, "ia:descricao", linha.descricao[:LITERAL_MAXIMO], loc, trecho)
            if linha.secao:
                alegar(sujeito, "ia:secao", linha.secao, loc, trecho)
            if linha.documento:
                alegar(sujeito, "ia:documento", linha.documento[:LITERAL_MAXIMO], loc, trecho)
            if linha.sentido:
                alegar(sujeito, "ia:sentido", linha.sentido, loc, trecho)
        anterior = LeituraLancamentos.objects.filter(artefato=artefato).values_list("base", flat=True).first()
        resultado.substituiu = anterior is not None and anterior != leitura.base
        LeituraLancamentos.objects.update_or_create(
            artefato=artefato,
            defaults={"tenant_id": artefato.tenant_id, "base": leitura.base, "fonte": leitura.fonte,
                      "fonte_id": leitura.fonte_id},
        )
        projetar_artefato(artefato)

    emit("financeiro.lancamentos", "ok",
         message=f"{resultado.lancamentos} lançamentos registrados ({leitura.rotulo})",
         payload={**leitura.payload, "lancamentos": resultado.lancamentos,
                  "alegacoes_criadas": resultado.alegacoes_criadas, "substituiu": resultado.substituiu},
         **comum)
    return resultado


def registrar(estruturacao: EstruturacaoLLM, *, user_id=None) -> Resultado:
    """Transforma a estruturação por LLM escolhida em alegações e refaz a projeção do documento.

    `ValueError` com mensagem para a pessoa quando não dá (execução não concluída, captura sem
    hash). Idempotente: registrar de novo não duplica nada.
    """
    if estruturacao.status != EstruturacaoLLM.Status.CONCLUIDO or not estruturacao.structured_data:
        raise ValueError("só uma estruturação concluída pode virar lançamentos")
    document_text = estruturacao.document_text
    modelo = f"{estruturacao.provider}:{estruturacao.model_name}"
    linhas, periodo = extrair_linhas(estruturacao.structured_data)
    return _registrar(document_text, linhas, {"periodo": periodo}, _Leitura(
        base=base_sujeito(document_text.document.blob_hash, estruturacao.id),
        fonte=Movimento.Fonte.ESTRUTURACAO, fonte_id=estruturacao.id, produtor=PRODUTOR, produtor_versao=PRODUTOR_VERSAO, modelo=modelo,
        localizador_tipo=LOCALIZADOR_TIPO, localizador={"estruturacao": str(estruturacao.id)},
        rotulo=modelo, payload={"estruturacao_id": str(estruturacao.id)},
    ), user_id)


def registrar_extracao(extracao: ExtracaoPagina, *, user_id=None) -> Resultado:
    """Transforma uma extração **válida** do parser aprovado em alegações, sem LLM.

    O sujeito é do schema (não da execução): rodar o mesmo parser de novo na mesma captura
    produz as mesmas alegações, então registrar de novo não duplica nada.
    """
    if extracao.status != ExtracaoPagina.Status.VALIDO or not extracao.dados:
        raise ValueError("só uma extração que validou pode virar lançamentos")
    document_text, schema = extracao.document_text, extracao.schema
    if schema_tem_papeis(schema.schema or {}):
        linhas, contexto = linhas_do_schema(extracao.dados, schema.schema)
    else:                                       # schema anterior aos papéis: lê por forma
        linhas, periodo = extrair_linhas(extracao.dados)
        contexto = {"periodo": periodo}
    return _registrar(document_text, linhas, contexto, _Leitura(
        base=f"mencao:{document_text.document.blob_hash}#parser/{schema.id}",
        fonte=Movimento.Fonte.PARSER, fonte_id=schema.id, produtor=PRODUTOR_PARSER, produtor_versao=f"v{schema.versao}", modelo="",
        localizador_tipo=PRODUTOR_PARSER,
        localizador={"schema": str(schema.id), "versao": schema.versao, "extracao": str(extracao.id)},
        rotulo=f"parser v{schema.versao}, sem LLM",
        payload={"extracao_id": str(extracao.id), "schema_id": str(schema.id)},
    ), user_id)


# ── projeção ────────────────────────────────────────────────────────────────

def _estabelecimento(tenant_id, descricao: str, cache: dict) -> Estabelecimento | None:
    descritor = normalizar_descritor(descricao)
    if not descritor:
        return None
    if descritor not in cache:
        existente = (DescritorEstabelecimento.objects.select_related("estabelecimento")
                     .filter(tenant_id=tenant_id, descritor=descritor).first())
        if existente:
            cache[descritor] = existente.estabelecimento
        else:
            est = Estabelecimento.objects.create(tenant_id=tenant_id, nome=nome_legivel(descritor)[:200])
            DescritorEstabelecimento.objects.create(tenant_id=tenant_id, descritor=descritor, estabelecimento=est)
            cache[descritor] = est
    return cache[descritor]


def _regras(leitura) -> tuple[str, str]:
    """(tipo de documento, convenção de sinal) com que a leitura escolhida deve ser lida."""
    if leitura.fonte == Movimento.Fonte.PARSER:
        from apps.artifacts.models import SchemaPagina

        schema = SchemaPagina.objects.filter(pk=leitura.fonte_id).values_list("schema", flat=True).first() or {}
        return (schema.get("tipo_documento") or TIPO_DOCUMENTO_PADRAO, schema.get("convencao") or CONVENCAO_PADRAO)
    return TIPO_DOCUMENTO_PADRAO, CONVENCAO_PADRAO


def _instituicao(artefato) -> str:
    url = (artefato.content or {}).get("url", "")
    try:
        url = artefato.extracted_text.source_url or url
    except Exception:
        pass
    return (urlsplit(url).hostname if url else "") or "desconhecida"


def conta_para(tenant_id, tipo: str, instituicao: str, identificador: str) -> Conta:
    """A conta que tem este identificador; nasce na primeira vez que aparece."""
    for conta in Conta.objects.filter(tenant_id=tenant_id, tipo=tipo, instituicao=instituicao):
        if identificador in (conta.identificadores or []):
            return conta
    return Conta.objects.create(tenant_id=tenant_id, tipo=tipo, instituicao=instituicao,
                                identificadores=[identificador])


def projetar_artefato(artefato) -> int:
    """Refaz os `Movimento` de um documento a partir das alegações ativas da leitura escolhida.
    Devolve quantos (zero quando nenhuma leitura foi escolhida)."""
    from django.db.models import Q

    Movimento.objects.filter(artefato=artefato).delete()
    leitura = LeituraLancamentos.objects.filter(artefato=artefato).first()
    if leitura is None:
        return 0
    sujeitos: dict[str, dict[str, str]] = {}
    documento: dict[str, str] = {}
    da_leitura = Q(sujeito_ref=leitura.base) | Q(sujeito_ref__startswith=leitura.base + "/")
    for c in Claim.objects.filter(da_leitura, artefato=artefato, produtor__in=PRODUTORES,
                                  estado=Claim.Estado.ATIVA):
        if c.sujeito_ref == leitura.base:
            documento[c.predicado] = c.objeto_literal
        else:
            sujeitos.setdefault(c.sujeito_ref, {})[c.predicado] = c.objeto_literal

    tipo_documento, convencao = _regras(leitura)
    conta = conta_para(artefato.tenant_id, TIPO_CONTA.get(tipo_documento, Conta.Tipo.CARTAO),
                       _instituicao(artefato), documento.get("ia:conta", ""))
    if documento.get("ia:titular") and not conta.titular:
        conta.titular = documento["ia:titular"][:200]
        conta.save(update_fields=["titular"])
    vencimento = data_referencia(documento.get("ia:periodo", ""))
    referencia = vencimento or artefato.created_at.date()

    cache: dict = {}
    com_estabelecimento = {Movimento.Natureza.COMPRA, Movimento.Natureza.TARIFA, Movimento.Natureza.ESTORNO,
                           Movimento.Natureza.SAIDA_TERCEIROS}
    novos = []
    for sujeito, campos in sujeitos.items():
        m = _SUJEITO.search(sujeito)
        if not m or "ia:valor" not in campos or "ia:descricao" not in campos:
            continue
        valor = valor_assinado(campos["ia:valor"], convencao, campos.get("ia:sentido", ""))
        descricao = campos["ia:descricao"]
        natureza = natureza_do_movimento(descricao, valor, tipo_documento)
        data = parse_data(campos.get("ia:data", ""), referencia)
        parcela = parcela_da_descricao(descricao)
        novos.append(Movimento(
            tenant_id=artefato.tenant_id, artefato=artefato, conta=conta, fonte=m.group(1), fonte_id=m.group(2),
            indice=int(m.group(3)), sujeito_ref=sujeito, data=data, data_texto=campos.get("ia:data", "")[:100],
            competencia=competencia(tipo_documento, vencimento, data),
            valor=valor, valor_texto=campos["ia:valor"][:100], descricao=descricao[:500],
            documento=campos.get("ia:documento", "")[:100], secao=campos.get("ia:secao", "")[:200],
            natureza=natureza, parcela=parcela[0] if parcela else None, parcelas=parcela[1] if parcela else None,
            estabelecimento=(_estabelecimento(artefato.tenant_id, descricao, cache)
                             if natureza in com_estabelecimento else None),
            classification_level=artefato.classification_level,
        ))
    Movimento.objects.bulk_create(novos)
    return len(novos)


def reconstruir(tenant=None) -> int:
    """Refaz toda a projeção (de uma organização ou de todas). Devolve o total de movimentos."""
    from apps.artifacts.models import Artifact

    leituras = LeituraLancamentos.objects.all()
    movimentos = Movimento.objects.all()
    if tenant is not None:
        leituras = leituras.filter(tenant=tenant)
        movimentos = movimentos.filter(tenant=tenant)
    ids = set(leituras.values_list("artefato_id", flat=True))
    total = 0
    with transaction.atomic():
        movimentos.exclude(artefato_id__in=ids).delete()
        for artefato in Artifact.objects.filter(id__in=ids):
            total += projetar_artefato(artefato)
    return total


# ── estado da pessoa ────────────────────────────────────────────────────────

def garantir_categorias(tenant) -> None:
    """Semeia a lista padrão na primeira visita (organização sem nenhuma categoria)."""
    if not CategoriaGasto.objects.filter(tenant=tenant).exists():
        CategoriaGasto.objects.bulk_create(
            [CategoriaGasto(tenant=tenant, nome=n) for n in CATEGORIAS_PADRAO], ignore_conflicts=True)


def fundir(destino: Estabelecimento, origem: Estabelecimento) -> None:
    """Junta `origem` em `destino`: descritores e movimentos passam para ele, valendo para os
    documentos futuros. A categoria do destino prevalece; sem ela, herda a da origem."""
    if destino.tenant_id != origem.tenant_id:
        raise ValueError("estabelecimentos de organizações diferentes")
    if destino.pk == origem.pk:
        return
    with transaction.atomic():
        DescritorEstabelecimento.objects.filter(estabelecimento=origem).update(estabelecimento=destino)
        Movimento.objects.filter(estabelecimento=origem).update(estabelecimento=destino)
        if destino.categoria_id is None and origem.categoria_id is not None:
            destino.categoria_id = origem.categoria_id
            destino.save(update_fields=["categoria"])
        origem.delete()


def juntar_contas(destino: Conta, origem: Conta) -> None:
    """Junta `origem` em `destino` (o cartão "sem número" e o 1402 são o mesmo): identificadores
    e movimentos passam para ele, e os documentos futuros com aqueles identificadores também."""
    if destino.tenant_id != origem.tenant_id:
        raise ValueError("contas de organizações diferentes")
    if destino.pk == origem.pk:
        return
    with transaction.atomic():
        destino.identificadores = list(dict.fromkeys((destino.identificadores or []) + (origem.identificadores or [])))
        destino.titular = destino.titular or origem.titular
        destino.apelido = destino.apelido or origem.apelido
        destino.save(update_fields=["identificadores", "titular", "apelido"])
        Movimento.objects.filter(conta=origem).update(conta=destino)
        origem.delete()
