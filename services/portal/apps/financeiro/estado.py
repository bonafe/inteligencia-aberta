"""Exportar e importar o estado da pessoa nos Gastos (tela `/gastos/estado/`).

As decisões da pessoa já são alegações (`decisoes.py`) e voltam sozinhas quando os lançamentos
são reconstruídos; o arquivo é a conveniência para zerar o banco inteiro ou levar o estado a
outra instância antes da federação: as categorias, os estabelecimentos (nome, categoria e os
descritores de maquininha juntados neles) e as contas (apelido, se é própria e os identificadores
juntados; o titular vai no arquivo mas vem dos documentos).

Nada de UUID no arquivo: depois de zerar, os IDs são outros. A identidade é o **descritor** (o
estabelecimento) e o **identificador** dentro do tipo e da instituição (a conta), os mesmos
sujeitos das decisões. Importar **registra decisões** de quem importa, e só as que mudam algo:
importar antes ou depois de reprocessar dá no mesmo, e importar de novo o mesmo arquivo não
registra nada.
"""

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from . import decisoes, lancamentos
from .models import CategoriaGasto, Conta, Estabelecimento

FORMATO = "inteligencia-aberta/gastos-estado"
VERSAO = 1


def exportar(tenant) -> dict:
    return {
        "formato": FORMATO,
        "versao": VERSAO,
        "exportado_em": timezone.now().isoformat(),
        "organizacao": tenant.name,
        "categorias": list(CategoriaGasto.objects.filter(tenant=tenant).order_by("nome")
                           .values_list("nome", flat=True)),
        "estabelecimentos": [
            {"nome": e.nome, "categoria": e.categoria.nome if e.categoria else None, "empresa_ref": e.empresa_ref,
             "descritores": sorted(d.descritor for d in e.descritores.all())}
            for e in Estabelecimento.objects.filter(tenant=tenant).select_related("categoria")
            .prefetch_related("descritores").order_by("nome")
        ],
        "contas": [
            {"tipo": c.tipo, "instituicao": c.instituicao, "identificadores": c.identificadores or [],
             "apelido": c.apelido, "titular": c.titular, "propria": c.propria}
            for c in Conta.objects.filter(tenant=tenant).order_by("tipo", "instituicao", "criada_em")
        ],
    }


@dataclass
class Resumo:
    categorias: int = 0
    estabelecimentos: int = 0
    contas: int = 0
    #: Decisões novas registradas (o que já valia não é registrado de novo).
    decisoes: int = 0
    avisos: list[str] = field(default_factory=list)


def _texto(valor, limite: int) -> str:
    return valor.strip()[:limite] if isinstance(valor, str) else ""


def _lista_de_textos(valor, limite: int) -> list[str]:
    if not isinstance(valor, list):
        return []
    return list(dict.fromkeys(v.strip()[:limite] for v in valor if isinstance(v, str)))


def validar(dados) -> None:
    """`ValueError` com a explicação, se não for um arquivo de estado desta versão."""
    if not isinstance(dados, dict) or dados.get("formato") != FORMATO:
        raise ValueError("não é um arquivo exportado pela tela de estado dos Gastos")
    if dados.get("versao") != VERSAO:
        raise ValueError(f"versão {dados.get('versao')!r} do arquivo; esta instância lê a {VERSAO}")
    for chave in ("categorias", "estabelecimentos", "contas"):
        if not isinstance(dados.get(chave, []), list):
            raise ValueError(f"'{chave}' deveria ser uma lista")


def importar(tenant, dados, autor) -> Resumo:
    """Registra como decisões de `autor` o que o arquivo diz e projeta o estado. Acrescenta e
    corrige, não apaga: o nome, a categoria e o apelido do arquivo valem, mas um vazio no arquivo
    não desfaz o que se decidiu depois de exportar. Tudo ou nada."""
    validar(dados)
    resumo = Resumo()
    decisor = decisoes.Decisor(tenant, autor, tela="estado", acao="importar")
    with transaction.atomic():
        for nome in _lista_de_textos(dados.get("categorias", []), 80):
            if nome:
                decisor.criar_categoria(nome)
                resumo.categorias += 1
        for item in dados.get("estabelecimentos", []):
            if isinstance(item, dict):
                _importar_estabelecimento(decisor, item, resumo)
        for item in dados.get("contas", []):
            if isinstance(item, dict):
                _importar_conta(decisor, item, resumo)
        resumo.decisoes = decisor.registradas
        decisoes.projetar(tenant.pk)
    return resumo


def _importar_estabelecimento(decisor, item, resumo) -> None:
    descritores = [d for d in _lista_de_textos(item.get("descritores"), 300) if d]
    nome = _texto(item.get("nome"), 200)
    if not descritores:
        resumo.avisos.append(f"estabelecimento sem descritor ignorado: {nome or '(sem nome)'}")
        return
    resumo.estabelecimentos += 1
    alvo, *outros = descritores
    decisor.juntar_descritores(alvo, outros)
    # O nome automático (o do próprio descritor) não é decisão: a projeção chega nele sozinha.
    if nome and nome not in {lancamentos.nome_legivel(d)[:200] for d in descritores}:
        decisor.renomear_descritores(descritores, nome)
    if categoria := _texto(item.get("categoria"), 80):
        decisor.categorizar_descritores(descritores, categoria)
    if empresa := _texto(item.get("empresa_ref"), 500):
        try:
            decisor.informar_empresa(descritores, empresa)
        except ValueError:
            resumo.avisos.append(f"referência de empresa inválida ignorada: {empresa}")


def _importar_conta(decisor, item, resumo) -> None:
    tipo = _texto(item.get("tipo"), 20)
    instituicao = _texto(item.get("instituicao"), 255)
    if tipo not in Conta.Tipo.values or not instituicao:
        resumo.avisos.append(f"conta ignorada: tipo {tipo!r}, instituição {instituicao!r}")
        return
    resumo.contas += 1
    # "" é o documento que não traz número: identifica a conta "sem número" da instituição.
    identificadores = _lista_de_textos(item.get("identificadores"), 100) or [""]
    refs = [decisoes.ref_conta(tipo, instituicao, i) for i in identificadores]
    decisor.juntar_refs(refs[0], refs[1:])
    if apelido := _texto(item.get("apelido"), 120):
        decisor.apelidar_refs(refs, apelido)
    if isinstance(item.get("propria"), bool):
        decisor.marcar_propria_refs(refs, item["propria"])
