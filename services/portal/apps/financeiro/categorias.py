"""Sugestão de categoria para o estabelecimento ainda sem categoria (tela `/gastos/categorizar/`).

Determinística, sem LLM, e aprendida do que a própria pessoa já escolheu:

1. **vizinho**: um estabelecimento já categorizado cujo descritor começa pela mesma palavra
   ("AGROCOC COMER" e "AGROCOC COMERCIO DE RA"); palavra genérica ("CASA", "MERCADO") e o
   prefixo da maquininha ("MP", "PG", "IFD") não contam;
2. **tema**: palavra-chave do ramo ("POSTO", "DROGARIA", "HOTEL"), que aponta categorias
   candidatas; vale a que a pessoa mais usa entre as que existem (quem nunca usou "Café e lanche"
   e põe padaria em "Restaurante e bar" recebe "Restaurante e bar");
3. **colado**: o mesmo, dentro de uma palavra colada ("PINHEIRORACOES", "RAIA2199"): só média.

`alta` é o que a tela pode aplicar de uma vez: o vizinho que casa nas duas primeiras palavras
sem nenhum vizinho discordar, o vizinho que o tema confirma, ou um tema inequívoco. O resto é
`media`: aparece pré-selecionado, mas só grava quando a pessoa confirma.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, F, Sum

from .models import CategoriaGasto, Estabelecimento

ALTA, MEDIA = "alta", "media"

#: Prefixos de maquininha e de intermediador, que não dizem nada do lugar.
PREFIXOS = {"MP", "PG", "PAG", "IFD", "DL", "EC", "PDV", "SQ", "IZ", "PICPAY", "PAGSEGURO", "SUMUP", "STONE",
            "EBANX", "EBN", "PP", "HNA", "APP"}
#: Primeiras palavras que muitos lugares diferentes compartilham: não servem para achar vizinho.
GENERICAS = {
    "CASA", "COMERCIAL", "COMERCIO", "LOJA", "LOJAS", "SANTA", "SANTO", "SAO", "NOVA", "NOVO", "AUTO", "POSTO",
    "BAR", "CAFE", "RESTAURANTE", "MERCADO", "SUPERMERCADO", "PADARIA", "PANIFICADORA", "DROGARIA", "FARMACIA",
    "HOTEL", "ESTORNO", "IOF", "CENTRO", "EMPORIO", "DISTRIBUIDORA", "RECORD", "QUIOSQUE", "CONCESSIONARIA",
    "AMAZON", "REDE", "GRUPO", "CENTER", "CENTRAL", "SUPER", "MINI", "PONTO", "ESPACO", "ATELIE", "STUDIO",
    "ARMAZEM", "THE",
}

#: (expressão, categorias candidatas em ordem de preferência, inequívoca). A ordem importa: a
#: primeira que casa vence ("MERCADO LIVRE" é compra online antes de ser mercado).
TEMAS = [
    (r"\b(AMAZONPRIME\w*|AMAZON PRIME|AMAZON MUSIC|PRIME VIDEO|AUDIBLE|KINDLE UNLIMITED)\b",
     ["Assinaturas e software"], True),
    (r"\b(MERCADOLIVRE|MERCADO LIVRE|MERCADOPAGO|MELI)\b", ["Compras Online", "Compras"], True),
    (r"\bAUTO (ELETRICA|PECAS|CENTER|MECANICA|SOCORRO)\b", ["Transporte"], True),
    (r"\b(SEGUROS?|SEGURADORA)\b", ["Transporte"], False),
    (r"\b(SUPERMERC\w*|ATACAD\w*|CARREFOUR|ASSAI|HORTIFRUT\w*|SACOLAO|ACOUGUE|MERCADO|MERCEARIA|MERCADINHO"
     r"|PAO DE ACUCAR)\b", ["Mercado"], True),
    (r"\b(RESTAURANTE|RESTAURANT|REST|CHOPERIA|CHOPP|CHURRASC\w*|PARRILLA|PIZZ\w*|BISTRO|BAR|BOTECO|LANCHONETE"
     r"|LANCHES|BURGER|HAMBURG\w*|SUSHI|TEMAKI|ESPETINHO|CANTINA|TRATTORIA|IFOOD|CONTROLEDEBAR|RODOSNACK)\b",
     ["Restaurante e bar"], True),
    (r"\b(CAFE|CAFETERIA|COFFEE|PADARIA|PANIFICADORA|PANIF|CONFEITARIA|DOCERIA|SORVETERIA|SORVETES|ACAI|GELATO"
     r"|PASTELARIA|BOLOS|\w*CAFETERIA)\b", ["Café e lanche", "Restaurante e bar"], True),
    (r"\b(DROGARIA|DROGASIL\w*|FARMACIA|DROGAL|PAGUE MENOS|CLINICA|LABORATORIO|HOSPITAL|ODONTO\w*|OTICA"
     r"|UNIMED|PSICOLOG\w*|FISIO\w*)\b", ["Farmácia e saúde"], True),
    (r"\b(LAB|MEDIC\w*|EXAMES?)\b", ["Farmácia e saúde"], False),
    (r"\b(POSTO|COMBUST\w*|SHELL|IPIRANGA|PETROBRAS|ESTACIONAMENTO|ESTAC|PARK\w*|PEDAGIO|AUTOBAN"
     r"|CONCESSIONARIA|SEM PARAR|CONECTCAR|VELOE|UBER|TAXI|METRO|RODOVIARIA|PNEU\w*|MECANICA|BORRACHARIA"
     r"|LAVA RAPIDO|DETRAN|ZONA AZUL)\b", ["Transporte"], True),
    (r"\b(HOTEL|HOTEIS|POUSADA|HOSTEL|AIRBNB|BOOKING|IBIS|MOTEL|RESORT)\b",
     ["Hospedagem", "Viagem e hospedagem"], True),
    (r"\b(LATAM|DECOLAR|123MILHAS|PASSAGE\w*|AEROPORTO|LOCALIZA|MOVIDA|UNIDAS)\b",
     ["Viagem e hospedagem", "Transporte"], False),
    (r"\b(IOF|ANUIDADE|TARIFA|JUROS|ENCARGOS?|MULTA|IMPOSTO|DARF|IPVA|IPTU)\b", ["Impostos e tarifas"], True),
    (r"\b(NETFLIX|SPOTIFY|DEEZER|YOUTUBE|DISNEY|HBO|HBOMAX|APPLE|ICLOUD|GOOGLE|OPENAI|CHATGPT|ANTHROPIC"
     r"|CLAUDE|GITHUB|LINODE|AKAMAI|DIGITALOCEAN|AWS|MICROSOFT|ADOBE|DROPBOX|NOTION|ZOOM|CANVA|GLOBOPLAY"
     r"|PARAMOUNT|HOSTINGER|CLOUDFLARE)\b", ["Assinaturas e software"], True),
    (r"\b(AMAZON\w*|SHOPEE|ALIEXPRESS|SHEIN|MAGALU|MAGAZINE LUIZA|KABUM|AMERICANAS|SUBMARINO|TEMU)\b",
     ["Compras Online", "Compras"], True),
    (r"\b(PET|PETSHOP|PET SHOP|PETZ|AGROPEC\w*|RACAO|RACOES|VETERINAR\w*|COBASI)\b", ["Pet"], True),
    (r"\b(ACADEMIA|SMARTFIT|SMART FIT|CROSSFIT|PILATES|NATACAO|YOGA|GYMPASS|WELLHUB)\b",
     ["Atividade Física"], True),
    (r"\b(CONSTRU\w*|MATERIAIS|LEROY|TELHA\w*|CIMENTO|MADEIR\w*|FERRAGENS|FERRAGISTA|HIDRAULICA|TINTAS"
     r"|VIDRACARIA|MARMORARIA|DEPOSITO)\b", ["Construção"], True),
    (r"\b(ELETRICA)\b", ["Construção"], False),
    (r"\b(MOVEIS|ELETRODOM\w*|COLCHO\w*|CAMICADO|TOK STOK|ETNA)\b", ["Móveis e Eletrodomésticos", "Casa"], False),
    (r"\b(ROUPAS|MODAS?|CONFEC\w*|CALCADOS|RENNER|RIACHUELO|HERING|ZARA|MALHAS|BOUTIQUE|SAPATARIA)\b",
     ["Roupas", "Compras"], False),
    (r"\b(GAS|AGUA|ULTRAGAZ|LIQUIGAS|ENERGIA|CPFL|SABESP|SANASA|VIVO|CLARO|INTERNET)\b", ["Casa"], False),
]
_TEMAS = [(re.compile(rx), candidatas, inequivoca) for rx, candidatas, inequivoca in TEMAS]

#: Pedaço do ramo dentro de uma palavra colada; sempre média. O olhar para trás afasta
#: "DECORACAO", "GERACAO" e "CORACAO" da ração.
COLADOS = [
    (r"(?<!GE)(?<!CO)RAC(AO|OES)", ["Pet"]), (r"AGROPEC", ["Pet"]),
    (r"BISTRO|GRILL|CHURRAS|PIZZ", ["Restaurante e bar"]),
    (r"CAFETERIA|PADARIA|SORVET|BOLOS", ["Café e lanche", "Restaurante e bar"]),
    (r"DROGA|FARMA|^RAIA\d", ["Farmácia e saúde"]),
    (r"POSTODE|AUTOPOSTO", ["Transporte"]),
    (r"TINTAS|PARAFUSO|CONSTRU", ["Construção"]),
    (r"MERCADO|HORTI|CARNES|ACOUGUE", ["Mercado"]),
    (r"HOTEL|POUSADA", ["Hospedagem", "Viagem e hospedagem"]),
]
_COLADOS = [(re.compile(rx), candidatas) for rx, candidatas in COLADOS]


def palavras(descritor: str) -> list[str]:
    """As palavras que dizem algo do lugar: sem número solto, sem letra solta e sem o prefixo da maquininha."""
    ws = [w for w in descritor.split() if not w.isdigit() and len(w) > 1]
    while ws and ws[0] in PREFIXOS:
        ws.pop(0)
    return ws


def chave_vizinho(descritor: str) -> str | None:
    ws = palavras(descritor)
    if not ws or ws[0] in GENERICAS or len(ws[0]) < 4:
        return None
    return ws[0]


@dataclass(frozen=True)
class Sugestao:
    categoria: str
    confianca: str
    motivo: str


class Sugeridor:
    """Monta o índice do que a pessoa já categorizou e sugere para o resto. Puro (sem banco):
    recebe `(categoria, descritores)` dos categorizados e os nomes das categorias que existem."""

    def __init__(self, categorizados: list[tuple[str, list[str]]], categorias: list[str]):
        self.categorias = set(categorias)
        self.uso = Counter(cat for cat, _ in categorizados)
        self.vizinhos: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
        for cat, descritores in categorizados:
            for d in descritores:
                if k := chave_vizinho(d):
                    self.vizinhos[k].append((cat, palavras(d)[:2]))

    def _escolher(self, candidatas: list[str]) -> str | None:
        existentes = [c for c in candidatas if c in self.categorias]
        if not existentes:
            return None
        return max(existentes, key=lambda c: (self.uso[c], -candidatas.index(c)))

    def _tema(self, descritores: list[str]) -> tuple[str | None, bool]:
        for d in descritores:
            texto = " ".join(palavras(d))
            for rx, candidatas, inequivoca in _TEMAS:
                if rx.search(texto) and (cat := self._escolher(candidatas)):
                    return cat, inequivoca
        return None, False

    def _colado(self, descritores: list[str]) -> str | None:
        for d in descritores:
            for w in palavras(d):
                for rx, candidatas in _COLADOS:
                    if rx.search(w) and (cat := self._escolher(candidatas)):
                        return cat
        return None

    def sugerir(self, descritores: list[str]) -> Sugestao | None:
        votos: Counter = Counter()
        duas_palavras = False
        for d in descritores:
            for cat, inicio in self.vizinhos.get(chave_vizinho(d) or "", []):
                votos[cat] += 1
                duas_palavras |= inicio == palavras(d)[:2]
        tema, inequivoca = self._tema(descritores)
        if votos:
            (vizinho, n), *outros = votos.most_common()
            if not outros and tema in (None, vizinho) and (duas_palavras or tema == vizinho):
                return Sugestao(vizinho, ALTA, "parecido com outro que você já categorizou")
            return Sugestao(vizinho, MEDIA, "parecido com outro que você já categorizou")
        if tema:
            return Sugestao(tema, ALTA if inequivoca else MEDIA, "palavra do ramo")
        if colado := self._colado(descritores):
            return Sugestao(colado, MEDIA, "palavra do ramo, colada")
        return None


def sugeridor_da_org(tenant) -> Sugeridor:
    categorizados = [
        (e.categoria.nome, [d.descritor for d in e.descritores.all()])
        for e in Estabelecimento.objects.filter(tenant=tenant, categoria__isnull=False)
        .select_related("categoria").prefetch_related("descritores")
    ]
    nomes = list(CategoriaGasto.objects.filter(tenant=tenant).values_list("nome", flat=True))
    return Sugeridor(categorizados, nomes)


def pendentes(tenant) -> list[dict]:
    """Os estabelecimentos sem categoria, do maior gasto para o menor, cada um com a sugestão."""
    sugeridor = sugeridor_da_org(tenant)
    linhas = []
    for e in (Estabelecimento.objects.filter(tenant=tenant, categoria__isnull=True)
              .annotate(total=Sum(-F("movimentos__valor")), n=Count("movimentos"))
              .prefetch_related("descritores")):
        descritores = [d.descritor for d in e.descritores.all()]
        linhas.append({"estabelecimento": e, "descritores": descritores, "total": e.total or 0, "n": e.n,
                       "sugestao": sugeridor.sugerir(descritores)})
    linhas.sort(key=lambda l: (-l["total"], l["estabelecimento"].nome))
    return linhas


def aplicar_alta(tenant) -> int:
    """Grava as sugestões de alta confiança. Devolve quantos estabelecimentos ganharam categoria."""
    por_nome = {c.nome: c for c in CategoriaGasto.objects.filter(tenant=tenant)}
    aplicadas = 0
    with transaction.atomic():
        for linha in pendentes(tenant):
            s = linha["sugestao"]
            if s and s.confianca == ALTA and s.categoria in por_nome:
                est = linha["estabelecimento"]
                est.categoria = por_nome[s.categoria]
                est.save(update_fields=["categoria"])
                aplicadas += 1
    return aplicadas
