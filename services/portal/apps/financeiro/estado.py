"""Exportar e importar o estado da pessoa nos Gastos (tela `/gastos/estado/`).

O que a projeção não refaz sozinha: as categorias, os estabelecimentos (nome, categoria e os
descritores de maquininha que foram juntados neles) e as contas (apelido, titular, se é própria
e os identificadores que foram juntados). Serve para zerar o sistema, reprocessar as faturas e
voltar ao que se tinha.

Nada de UUID no arquivo: depois de zerar, os IDs são outros. A identidade é o **descritor** (o
estabelecimento) e o **identificador** dentro do tipo e da instituição (a conta), os mesmos
critérios com que a projeção os encontra. Por isso importar antes ou depois de reprocessar dá no
mesmo: antes, cria o estabelecimento e a conta que a projeção vai achar; depois, junta e acerta
os que a projeção criou. Importar de novo o mesmo arquivo não muda nada.
"""

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from . import lancamentos
from .models import CategoriaGasto, Conta, DescritorEstabelecimento, Estabelecimento

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
    categorias_criadas: int = 0
    estabelecimentos_criados: int = 0
    estabelecimentos_atualizados: int = 0
    estabelecimentos_juntados: int = 0
    contas_criadas: int = 0
    contas_atualizadas: int = 0
    contas_juntadas: int = 0
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


def importar(tenant, dados) -> Resumo:
    """Aplica o arquivo à organização. Acrescenta e corrige, não apaga: o nome, a categoria e o
    apelido do arquivo vencem, mas um vazio no arquivo não limpa o que se preencheu depois de
    exportar. Tudo ou nada."""
    validar(dados)
    resumo = Resumo()
    with transaction.atomic():
        categorias = _importar_categorias(tenant, dados, resumo)
        for item in dados.get("estabelecimentos", []):
            if isinstance(item, dict):
                _importar_estabelecimento(tenant, item, categorias, resumo)
        for item in dados.get("contas", []):
            if isinstance(item, dict):
                _importar_conta(tenant, item, resumo)
        if resumo.contas_juntadas:
            lancamentos.agrupar_parceladas(tenant.pk)
    return resumo


def _importar_categorias(tenant, dados, resumo) -> dict[str, CategoriaGasto]:
    nomes = set(_lista_de_textos(dados.get("categorias", []), 80))
    nomes |= {_texto(e.get("categoria"), 80) for e in dados.get("estabelecimentos", []) if isinstance(e, dict)}
    for nome in sorted(n for n in nomes if n):
        _, criada = CategoriaGasto.objects.get_or_create(tenant=tenant, nome=nome)
        resumo.categorias_criadas += criada
    return {c.nome: c for c in CategoriaGasto.objects.filter(tenant=tenant)}


def _importar_estabelecimento(tenant, item, categorias, resumo) -> None:
    descritores = [d for d in _lista_de_textos(item.get("descritores"), 300) if d]
    nome = _texto(item.get("nome"), 200)
    if not descritores:
        resumo.avisos.append(f"estabelecimento sem descritor ignorado: {nome or '(sem nome)'}")
        return
    existentes = list(Estabelecimento.objects.filter(
        tenant=tenant, descritores__descritor__in=descritores).distinct().order_by("criado_em", "id"))
    categoria = categorias.get(_texto(item.get("categoria"), 80))
    if existentes:
        est, *outros = existentes
        for origem in outros:
            lancamentos.fundir(est, origem)
            resumo.estabelecimentos_juntados += 1
        est.refresh_from_db()
    else:
        est = Estabelecimento(tenant=tenant, nome=lancamentos.nome_legivel(descritores[0])[:200])
        resumo.estabelecimentos_criados += 1
    antes = (est.nome, est.categoria_id, est.empresa_ref)
    est.nome = nome or est.nome
    est.categoria = categoria or est.categoria
    est.empresa_ref = _texto(item.get("empresa_ref"), 500) or est.empresa_ref
    if existentes and (est.nome, est.categoria_id, est.empresa_ref) != antes:
        resumo.estabelecimentos_atualizados += 1
    est.save()
    ja_ligados = set(DescritorEstabelecimento.objects.filter(tenant=tenant, descritor__in=descritores)
                     .values_list("descritor", flat=True))
    DescritorEstabelecimento.objects.bulk_create([
        DescritorEstabelecimento(tenant=tenant, descritor=d, estabelecimento=est)
        for d in descritores if d not in ja_ligados
    ])


def _importar_conta(tenant, item, resumo) -> None:
    tipo = _texto(item.get("tipo"), 20)
    instituicao = _texto(item.get("instituicao"), 255)
    if tipo not in Conta.Tipo.values or not instituicao:
        resumo.avisos.append(f"conta ignorada: tipo {tipo!r}, instituição {instituicao!r}")
        return
    # "" é o documento que não traz número: identifica a conta "sem número" da instituição.
    identificadores = _lista_de_textos(item.get("identificadores"), 100) or [""]
    existentes = [c for c in Conta.objects.filter(tenant=tenant, tipo=tipo, instituicao=instituicao)
                  .order_by("criada_em", "id")
                  if set(c.identificadores or []) & set(identificadores)]
    if existentes:
        conta, *outras = existentes
        for origem in outras:
            lancamentos.juntar_contas(conta, origem)
            resumo.contas_juntadas += 1
        conta.refresh_from_db()
    else:
        conta = Conta(tenant=tenant, tipo=tipo, instituicao=instituicao, identificadores=[])
        resumo.contas_criadas += 1
    novo = {
        "identificadores": list(dict.fromkeys((conta.identificadores or []) + identificadores)),
        "apelido": _texto(item.get("apelido"), 120) or conta.apelido,
        "titular": _texto(item.get("titular"), 200) or conta.titular,
        "propria": item.get("propria") if isinstance(item.get("propria"), bool) else conta.propria,
    }
    if conta.pk and any(getattr(conta, k) != v for k, v in novo.items()):
        resumo.contas_atualizadas += 1
    for k, v in novo.items():
        setattr(conta, k, v)
    conta.save()
