"""Estruturação → alegações → projeção `Lancamento`.

Três passos, cada um com seu lugar:

1. **Parsing puro** (`extrair_linhas`, `parse_valor`, `parse_data`, `normalizar_descritor`,
   `tipo_do_lancamento`): sem banco e sem LLM. Os nomes de campo vêm de um LLM e variam entre
   execuções ("transação", "descricao", "historico"), por isso a busca é por forma, não por caminho.
2. **Registro** (`registrar`): a pessoa escolhe uma estruturação concluída e ela vira alegações
   pelo serviço único `registrar_alegacao`, com evidência apontando para a linha. As alegações de
   outra estruturação do mesmo documento são retratadas — a fatura passa a ser lida só pela escolhida.
3. **Projeção** (`projetar_artefato`, `reconstruir`): das alegações ativas para `Lancamento`.
   Apagar e refazer a projeção é seguro; as fusões e categorias ficam em `Estabelecimento`.

Convenção de sinal (fatura de cartão): positivo é gasto, negativo é pagamento ou estorno.
"""

import calendar
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.db import transaction

from apps.artifacts.alegacoes import LITERAL_MAXIMO, EvidenciaEntrada, registrar_alegacao, retratar, truncar_trecho
from apps.artifacts.models import Claim, EstruturacaoLLM
from config.conteudo_hash import formato_valido as hash_valido

from .models import CategoriaGasto, DescritorEstabelecimento, Estabelecimento, Lancamento

PRODUTOR = "estruturacao-llm"
#: Subir quando a forma das alegações mudar (predicados, sujeito, evidência).
PRODUTOR_VERSAO = "1"
LOCALIZADOR_TIPO = "estruturacao-llm"

CATEGORIAS_PADRAO = (
    "Mercado", "Restaurante e bar", "Café e lanche", "Assinaturas e software", "Farmácia e saúde",
    "Transporte", "Viagem e hospedagem", "Casa", "Compras", "Impostos e tarifas", "Outros",
)

#: Se a data cair mais que isto depois da referência da fatura, é do ano anterior. Curta de
#: propósito: parcela traz a data da compra original ("15/12 ... PARC 10/10" numa fatura de outubro).
FOLGA_ANO = timedelta(days=10)

_SUJEITO = re.compile(r"#estruturacao/([0-9a-f-]{36})/lancamento/(\d+)$")
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


def tipo_do_lancamento(descricao: str, valor: Decimal | None) -> str:
    texto = _sem_acento(descricao).upper()
    if valor is not None and valor < 0:
        if re.search(r"\b(PGTO|PAGTO|PAGAMENTO)\b", texto):
            return Lancamento.Tipo.PAGAMENTO
        return Lancamento.Tipo.ESTORNO
    if re.search(r"\b(IOF|ANUIDADE|TARIFA|JUROS|ENCARGOS?|MULTA)\b", texto):
        return Lancamento.Tipo.TARIFA
    return Lancamento.Tipo.COMPRA


def nome_legivel(descritor: str) -> str:
    return " ".join(p if len(p) <= 3 and not p.isalpha() else p.capitalize() for p in descritor.split())


# ── registro ────────────────────────────────────────────────────────────────

@dataclass
class Resultado:
    lancamentos: int = 0
    alegacoes_criadas: int = 0
    retratadas: int = 0


def _autor(document_text, artefato) -> str:
    url = document_text.source_url or (artefato.content or {}).get("url", "")
    host = urlsplit(url).hostname if url else None
    return f"dominio:{host}" if host else f"urn:uuid:{artefato.id}"


def base_sujeito(blob_hash: str, estruturacao_id) -> str:
    return f"mencao:{blob_hash}#estruturacao/{estruturacao_id}"


def registrar(estruturacao: EstruturacaoLLM, *, user_id=None) -> Resultado:
    """Transforma a estruturação escolhida em alegações e refaz a projeção do documento.

    `ValueError` com mensagem para a pessoa quando não dá (execução não concluída, captura sem
    hash). Idempotente: registrar de novo não duplica nada.
    """
    from apps.artifacts.tasks import correlacao_do_artefato
    from apps.events.emit import emit

    if estruturacao.status != EstruturacaoLLM.Status.CONCLUIDO or not estruturacao.structured_data:
        raise ValueError("só uma estruturação concluída pode virar lançamentos")
    document_text = estruturacao.document_text
    artefato = document_text.document
    if not hash_valido(artefato.blob_hash or ""):
        raise ValueError("a captura não tem hash de conteúdo; sem ele não há evidência")

    linhas, periodo = extrair_linhas(estruturacao.structured_data)
    resultado = Resultado(lancamentos=len(linhas))
    comum = dict(
        correlation_id=correlacao_do_artefato(artefato.id), subject_type="artifact", subject_id=artefato.id,
        tenant_id=artefato.tenant_id, user_id=user_id, source="portal",
    )
    if not linhas:
        emit("financeiro.lancamentos", "vazio", message="a estruturação não tem lista de lançamentos",
             payload={"estruturacao_id": str(estruturacao.id)}, **comum)
        return resultado

    base = base_sujeito(artefato.blob_hash, estruturacao.id)
    autor = _autor(document_text, artefato)
    modelo = f"{estruturacao.provider}:{estruturacao.model_name}"

    def alegar(sujeito, predicado, valor, localizador, trecho):
        _, criada = registrar_alegacao(
            artefato=artefato, sujeito_ref=sujeito, predicado=predicado, objeto_literal=valor,
            autor_ref=autor, produtor=PRODUTOR, produtor_versao=PRODUTOR_VERSAO, modelo=modelo,
            evidencias=[EvidenciaEntrada(blob_hash=artefato.blob_hash, localizador_tipo=LOCALIZADOR_TIPO,
                                         localizador=localizador, trecho=trecho)],
        )
        resultado.alegacoes_criadas += int(criada)

    with transaction.atomic():
        if periodo:
            alegar(base, "ia:periodo", periodo, {"estruturacao": str(estruturacao.id)}, truncar_trecho(periodo))
        for linha in linhas:
            sujeito = f"{base}/lancamento/{linha.indice}"
            trecho = truncar_trecho(json.dumps(linha.original, ensure_ascii=False))
            loc = {"estruturacao": str(estruturacao.id), "indice": linha.indice}
            alegar(sujeito, "ia:data", linha.data, loc, trecho)
            alegar(sujeito, "ia:valor", linha.valor, loc, trecho)
            alegar(sujeito, "ia:descricao", linha.descricao[:LITERAL_MAXIMO], loc, trecho)
            if linha.secao:
                alegar(sujeito, "ia:secao", linha.secao, loc, trecho)
        antigas = (Claim.objects.filter(artefato=artefato, produtor=PRODUTOR, estado=Claim.Estado.ATIVA)
                   .exclude(sujeito_ref__startswith=base))
        for claim in antigas:
            retratar(claim)
            resultado.retratadas += 1
        projetar_artefato(artefato)

    emit("financeiro.lancamentos", "ok",
         message=f"{resultado.lancamentos} lançamentos registrados ({modelo})",
         payload={"estruturacao_id": str(estruturacao.id), "lancamentos": resultado.lancamentos,
                  "alegacoes_criadas": resultado.alegacoes_criadas, "retratadas": resultado.retratadas},
         **comum)
    return resultado


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


def projetar_artefato(artefato) -> int:
    """Refaz os `Lancamento` de um documento a partir das alegações ativas. Devolve quantos."""
    sujeitos: dict[str, dict[str, str]] = {}
    periodos: dict[str, str] = {}
    for c in Claim.objects.filter(artefato=artefato, produtor=PRODUTOR, estado=Claim.Estado.ATIVA):
        if c.predicado == "ia:periodo":
            periodos[c.sujeito_ref] = c.objeto_literal
        else:
            sujeitos.setdefault(c.sujeito_ref, {})[c.predicado] = c.objeto_literal

    Lancamento.objects.filter(artefato=artefato).delete()
    cache: dict = {}
    novos = []
    for sujeito, campos in sujeitos.items():
        m = _SUJEITO.search(sujeito)
        if not m or "ia:valor" not in campos or "ia:descricao" not in campos:
            continue
        base = sujeito.rsplit("/lancamento/", 1)[0]
        referencia = data_referencia(periodos.get(base, "")) or artefato.created_at.date()
        valor = parse_valor(campos["ia:valor"])
        descricao = campos["ia:descricao"]
        tipo = tipo_do_lancamento(descricao, valor)
        novos.append(Lancamento(
            tenant_id=artefato.tenant_id, artefato=artefato, estruturacao_id=m.group(1), indice=int(m.group(2)),
            sujeito_ref=sujeito, data=parse_data(campos.get("ia:data", ""), referencia),
            data_texto=campos.get("ia:data", "")[:100], valor=valor, valor_texto=campos["ia:valor"][:100],
            descricao=descricao[:500], secao=campos.get("ia:secao", "")[:200], tipo=tipo,
            estabelecimento=(None if tipo == Lancamento.Tipo.PAGAMENTO
                             else _estabelecimento(artefato.tenant_id, descricao, cache)),
            classification_level=artefato.classification_level,
        ))
    Lancamento.objects.bulk_create(novos)
    return len(novos)


def reconstruir(tenant=None) -> int:
    """Refaz toda a projeção (de uma organização ou de todas). Devolve o total de lançamentos."""
    from apps.artifacts.models import Artifact

    claims = Claim.objects.filter(produtor=PRODUTOR, estado=Claim.Estado.ATIVA)
    lancamentos = Lancamento.objects.all()
    if tenant is not None:
        claims = claims.filter(tenant=tenant)
        lancamentos = lancamentos.filter(tenant=tenant)
    ids = set(claims.values_list("artefato_id", flat=True))
    total = 0
    with transaction.atomic():
        lancamentos.exclude(artefato_id__in=ids).delete()
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
    """Junta `origem` em `destino`: descritores e lançamentos passam para ele, valendo para as
    faturas futuras. A categoria do destino prevalece; sem ela, herda a da origem."""
    if destino.tenant_id != origem.tenant_id:
        raise ValueError("estabelecimentos de organizações diferentes")
    if destino.pk == origem.pk:
        return
    with transaction.atomic():
        DescritorEstabelecimento.objects.filter(estabelecimento=origem).update(estabelecimento=destino)
        Lancamento.objects.filter(estabelecimento=origem).update(estabelecimento=destino)
        if destino.categoria_id is None and origem.categoria_id is not None:
            destino.categoria_id = origem.categoria_id
            destino.save(update_fields=["categoria"])
        origem.delete()
