"""O que a pessoa decide nos Gastos é alegação (ADR 022, etapa 3), e o estado é projeção dela.

"Este estabelecimento é Mercado", "estes dois descritores são o mesmo lugar", "esta conta se chama
Cartão da Ana": cada decisão é uma `Claim` sem artefato, produtor `pessoa`, autora a pessoa
(`urn:uuid:` do usuário), com evidência `decisao` (a tela e a ação). Nada se edita: a decisão nova
**revisa** a vigente do mesmo sujeito e predicado, e a anterior fica no histórico.

O sujeito é o que sobrevive a zerar e o que outra instância entende — nunca o UUID local:

| Sujeito | Predicados |
|---|---|
| `fin:descritor/<descritor>` | `fin:categoria` (→ `fin:categoria/<nome>`), `fin:nome`, `fin:empresa`, `fin:mesmoLugarQue` (→ outro descritor) |
| `fin:categoria/<nome>` | `fin:rotulo` (a categoria existe) |
| `fin:conta/<tipo>/<instituição>/<identificador>` | `fin:apelido`, `fin:contaPropria` (`sim`/`nao`), `fin:mesmaContaQue` (→ outra conta) |

`fin:nenhum` como objeto desfaz (sem categoria, sem apelido) sem apagar nada.

`projetar` refaz `Estabelecimento`, `DescritorEstabelecimento`, `CategoriaGasto` e o apelido e a
fusão das `Conta` a partir das decisões vigentes e dos descritores dos movimentos. O descritor do
sujeito que nenhum movimento tem mais passa de novo por `normalizar_descritor`: mudar a
normalização ("ALLIANZ SEGU 02 DE 10" → "ALLIANZ SEGU") não perde a decisão. Só vira
estabelecimento o que algum movimento usa; as decisões dos outros ficam esperando. Num estabelecimento com vários descritores vale a
decisão mais recente entre eles. A projeção junta, nunca separa: não há decisão de separar.
"""

from collections import Counter, defaultdict
from urllib.parse import quote, unquote

from django.db import transaction

from apps.artifacts.alegacoes import EVIDENCIA_DECISAO, EvidenciaEntrada, registrar_alegacao
from apps.artifacts.models import Claim

from .extensao import EXTENSAO
from .models import CategoriaGasto, CompraParcelada, Conta, DescritorEstabelecimento, Estabelecimento, Movimento

PRODUTOR = "pessoa"
PRODUTOR_VERSAO = "1"

CATEGORIA = "fin:categoria"
NOME = "fin:nome"
EMPRESA = "fin:empresa"
MESMO_LUGAR = "fin:mesmoLugarQue"
ROTULO = "fin:rotulo"
APELIDO = "fin:apelido"
PROPRIA = "fin:contaPropria"
MESMA_CONTA = "fin:mesmaContaQue"
#: Objeto que desfaz a decisão anterior (sem categoria, sem apelido).
NENHUM = "fin:nenhum"

#: Nível da decisão: o piso do tipo de dado de que ela trata (ADR 022, regra 5; `registrar_alegacao`
#: também o aplica pelo predicado).
_PISO = {t.nome: t.piso for t in EXTENSAO.tipos}
_ESTABELECIMENTO, _CONTA = "fin:Estabelecimento", "fin:Conta"


def ref_descritor(descritor: str) -> str:
    return "fin:descritor/" + quote(descritor, safe="")


def ref_categoria(nome: str) -> str:
    return "fin:categoria/" + quote(nome, safe="")


def ref_conta(tipo: str, instituicao: str, identificador: str) -> str:
    return "fin:conta/" + "/".join(quote(p, safe="") for p in (tipo, instituicao, identificador))


def _valor(ref: str | None, tipo: str) -> str | None:
    """`fin:descritor/IFD%20X` → `IFD X`; None se a referência não é desse tipo."""
    prefixo = f"fin:{tipo}/"
    return unquote(ref[len(prefixo):]) if ref and ref.startswith(prefixo) else None


def _conta_da_ref(ref: str | None) -> tuple[str, str, str] | None:
    if not ref or not ref.startswith("fin:conta/"):
        return None
    partes = ref[len("fin:conta/"):].split("/")
    return tuple(unquote(p) for p in partes) if len(partes) == 3 else None


def _mais_recente(claims):
    return max(claims, key=lambda c: (c.criada_em, str(c.pk)), default=None)


def vigentes(tenant_id) -> dict[tuple[str, str], Claim]:
    """A decisão que vale para cada (sujeito, predicado): a mais recente das ativas que nenhuma
    outra ativa revisa (com duas instâncias decidindo ao mesmo tempo, a mais recente vence)."""
    claims = list(Claim.objects.filter(tenant_id=tenant_id, artefato__isnull=True, produtor=PRODUTOR,
                                       estado=Claim.Estado.ATIVA).order_by("criada_em", "id"))
    revisadas = {c.revisa_id for c in claims if c.revisa_id}
    return {(c.sujeito_ref, c.predicado): c for c in claims if c.pk not in revisadas}


class Decisor:
    """Registra as decisões de uma pessoa numa organização. Não projeta: quem chama termina com
    `projetar(tenant.pk)` (uma vez, depois de um lote)."""

    def __init__(self, tenant, autor, **onde):
        self.tenant, self.autor, self.onde = tenant, autor, {k: str(v) for k, v in onde.items()}
        self.vig = vigentes(tenant.pk)
        self.registradas = 0

    def _decidir(self, sujeito, predicado, tipo, *, objeto_ref=None, objeto_literal=None) -> bool:
        atual = self.vig.get((sujeito, predicado))
        if atual is None and objeto_ref == NENHUM:
            return False
        if atual is not None and (atual.objeto_ref, atual.objeto_literal) == (objeto_ref, objeto_literal):
            return False
        claim, criada = registrar_alegacao(
            artefato=None, tenant=self.tenant, classificacao=_PISO[tipo],
            sujeito_ref=sujeito, predicado=predicado, objeto_ref=objeto_ref, objeto_literal=objeto_literal,
            autor_ref=f"urn:uuid:{self.autor.pk}", produtor=PRODUTOR, produtor_versao=PRODUTOR_VERSAO,
            evidencias=[EvidenciaEntrada(blob_hash="", localizador_tipo=EVIDENCIA_DECISAO, localizador=self.onde)],
            revisa=atual,
        )
        self.vig[(sujeito, predicado)] = claim
        self.registradas += int(criada)
        return criada

    # ── estabelecimentos ──

    def _do_grupo(self, descritores, predicado):
        return _mais_recente(c for d in descritores if (c := self.vig.get((ref_descritor(d), predicado))))

    def categorizar_descritores(self, descritores, categoria: str | None) -> None:
        objeto = ref_categoria(categoria) if categoria else NENHUM
        for d in descritores:
            self._decidir(ref_descritor(d), CATEGORIA, _ESTABELECIMENTO, objeto_ref=objeto)

    def categorizar(self, est: Estabelecimento, categoria: str | None) -> None:
        self.categorizar_descritores(_descritores(est), categoria)

    def renomear_descritores(self, descritores, nome: str) -> None:
        for d in descritores:
            self._decidir(ref_descritor(d), NOME, _ESTABELECIMENTO, objeto_literal=nome)

    def renomear(self, est: Estabelecimento, nome: str) -> None:
        self.renomear_descritores(_descritores(est), nome)

    def informar_empresa(self, descritores, empresa_ref: str) -> None:
        for d in descritores:
            self._decidir(ref_descritor(d), EMPRESA, _ESTABELECIMENTO, objeto_ref=empresa_ref)

    def juntar_descritores(self, alvo: str, outros) -> None:
        """Os `outros` são o mesmo lugar que `alvo`. Nome, categoria e empresa do grupo do alvo
        prevalecem: são reafirmados nos que chegam (a decisão mais recente é a que vale)."""
        do_alvo = {p: self._do_grupo([alvo], p) for p in (CATEGORIA, NOME, EMPRESA)}
        for d in outros:
            if d == alvo:
                continue
            self._decidir(ref_descritor(d), MESMO_LUGAR, _ESTABELECIMENTO, objeto_ref=ref_descritor(alvo))
            for predicado, c in do_alvo.items():
                if c is not None:
                    self._decidir(ref_descritor(d), predicado, _ESTABELECIMENTO,
                                  objeto_ref=c.objeto_ref, objeto_literal=c.objeto_literal)

    def fundir(self, destino: Estabelecimento, origem: Estabelecimento) -> None:
        if destino.tenant_id != origem.tenant_id:
            raise ValueError("estabelecimentos de organizações diferentes")
        de_destino = _descritores(destino)
        if destino.pk == origem.pk or not de_destino:
            return
        # Reafirma no alvo o que vale para o destino inteiro (a decisão pode estar noutro descritor dele).
        alvo = de_destino[0]
        for predicado in (CATEGORIA, NOME, EMPRESA):
            if (c := self._do_grupo(de_destino, predicado)) is not None:
                self._decidir(ref_descritor(alvo), predicado, _ESTABELECIMENTO,
                              objeto_ref=c.objeto_ref, objeto_literal=c.objeto_literal)
        self.juntar_descritores(alvo, _descritores(origem))

    def criar_categoria(self, nome: str) -> None:
        self._decidir(ref_categoria(nome), ROTULO, _ESTABELECIMENTO, objeto_literal=nome)

    # ── contas ──

    def _refs(self, conta: Conta) -> list[str]:
        return [ref_conta(conta.tipo, conta.instituicao, i) for i in (conta.identificadores or [""])]

    def apelidar_refs(self, refs, apelido: str) -> None:
        for r in refs:
            if apelido:
                self._decidir(r, APELIDO, _CONTA, objeto_literal=apelido)
            else:
                self._decidir(r, APELIDO, _CONTA, objeto_ref=NENHUM)

    def apelidar(self, conta: Conta, apelido: str) -> None:
        self.apelidar_refs(self._refs(conta), apelido)

    def marcar_propria_refs(self, refs, propria: bool) -> None:
        for r in refs:
            if propria and (r, PROPRIA) not in self.vig:
                continue                    # própria é o padrão: não precisa de decisão
            self._decidir(r, PROPRIA, _CONTA, objeto_literal="sim" if propria else "nao")

    def marcar_propria(self, conta: Conta, propria: bool) -> None:
        self.marcar_propria_refs(self._refs(conta), propria)

    def juntar_refs(self, alvo: str, outras) -> None:
        do_alvo = {p: self.vig.get((alvo, p)) for p in (APELIDO, PROPRIA)}
        for r in outras:
            if r == alvo:
                continue
            self._decidir(r, MESMA_CONTA, _CONTA, objeto_ref=alvo)
            for predicado, c in do_alvo.items():
                if c is not None:
                    self._decidir(r, predicado, _CONTA, objeto_ref=c.objeto_ref, objeto_literal=c.objeto_literal)

    def juntar_contas(self, destino: Conta, origem: Conta) -> None:
        if destino.tenant_id != origem.tenant_id:
            raise ValueError("contas de organizações diferentes")
        if (destino.tipo, destino.instituicao) != (origem.tipo, origem.instituicao):
            raise ValueError("só se juntam contas do mesmo tipo e da mesma instituição")
        if destino.pk == origem.pk:
            return
        refs = self._refs(destino)
        for predicado in (APELIDO, PROPRIA):
            if c := _mais_recente(c for r in refs if (c := self.vig.get((r, predicado)))):
                self._decidir(refs[0], predicado, _CONTA, objeto_ref=c.objeto_ref, objeto_literal=c.objeto_literal)
        self.juntar_refs(refs[0], self._refs(origem))


def _descritores(est: Estabelecimento) -> list[str]:
    return sorted(DescritorEstabelecimento.objects.filter(estabelecimento=est).values_list("descritor", flat=True))


# ── projeção ────────────────────────────────────────────────────────────────

class _Uniao:
    def __init__(self):
        self.pai: dict[str, str] = {}

    def achar(self, x: str) -> str:
        self.pai.setdefault(x, x)
        while self.pai[x] != x:
            self.pai[x] = self.pai[self.pai[x]]
            x = self.pai[x]
        return x

    def unir(self, a: str, b: str) -> None:
        ra, rb = self.achar(a), self.achar(b)
        if ra != rb:
            self.pai[max(ra, rb)] = min(ra, rb)

    def grupos(self) -> dict[str, set[str]]:
        saida = defaultdict(set)
        for x in list(self.pai):
            saida[self.achar(x)].add(x)
        return saida


def projetar(tenant_id) -> None:
    """Refaz o estado da pessoa a partir das decisões vigentes. Idempotente; a reconstrução dos
    lançamentos chama depois de projetar os movimentos, e cada tela depois de decidir."""
    vig = vigentes(tenant_id)
    with transaction.atomic():
        categorias = _projetar_categorias(tenant_id, vig)
        juntou_contas = _projetar_contas(tenant_id, vig)
        _projetar_estabelecimentos(tenant_id, vig, categorias)
        if juntou_contas:
            from .lancamentos import agrupar_parceladas

            agrupar_parceladas(tenant_id)


def _projetar_categorias(tenant_id, vig) -> dict[str, CategoriaGasto]:
    """Toda categoria criada ou usada numa decisão existe. Nenhuma é apagada (a lista padrão
    também mora aqui, semeada por `garantir_categorias`)."""
    nomes = set()
    for (sujeito, predicado), c in vig.items():
        if predicado == ROTULO and (nome := _valor(sujeito, "categoria")):
            nomes.add(nome[:80])
        elif predicado == CATEGORIA and (nome := _valor(c.objeto_ref, "categoria")):
            nomes.add(nome[:80])
    existentes = {c.nome: c for c in CategoriaGasto.objects.filter(tenant_id=tenant_id)}
    for nome in sorted(nomes - existentes.keys()):
        existentes[nome] = CategoriaGasto.objects.create(tenant_id=tenant_id, nome=nome)
    return existentes


def _projetar_contas(tenant_id, vig) -> bool:
    """Junta as contas que as decisões dizem ser a mesma (mesmo tipo e instituição) e aplica
    apelido e "própria". As contas nascem dos documentos (`lancamentos.conta_para`); identificadores
    que já estão na mesma conta continuam juntos. Devolve se alguma conta foi juntada."""
    contas = list(Conta.objects.filter(tenant_id=tenant_id).order_by("criada_em", "id"))
    uniao = _Uniao()
    refs_da_conta = {}
    for conta in contas:
        refs = [ref_conta(conta.tipo, conta.instituicao, i) for i in (conta.identificadores or [""])]
        refs_da_conta[conta.pk] = refs
        for r in refs:
            uniao.unir(refs[0], r)
    por_ref: dict[str, dict[str, Claim]] = defaultdict(dict)
    for (sujeito, predicado), c in vig.items():
        if not sujeito.startswith("fin:conta/"):
            continue
        if predicado == MESMA_CONTA:
            a, b = _conta_da_ref(sujeito), _conta_da_ref(c.objeto_ref)
            if a and b and a[:2] == b[:2]:
                uniao.unir(sujeito, c.objeto_ref)
        else:
            por_ref[sujeito][predicado] = c
    grupos = uniao.grupos()

    juntou = False
    destino_do_grupo: dict[str, Conta] = {}
    for conta in contas:
        raiz = uniao.achar(refs_da_conta[conta.pk][0])
        destino = destino_do_grupo.setdefault(raiz, conta)
        if destino is conta:
            continue
        destino.identificadores = list(dict.fromkeys((destino.identificadores or []) + (conta.identificadores or [])))
        destino.titular = destino.titular or conta.titular
        destino.save(update_fields=["identificadores", "titular"])
        Movimento.objects.filter(conta=conta).update(conta=destino)
        conta.delete()
        juntou = True

    for raiz, conta in destino_do_grupo.items():
        refs = grupos[raiz]
        apelido = _mais_recente(c for r in refs if (c := por_ref[r].get(APELIDO)))
        propria = _mais_recente(c for r in refs if (c := por_ref[r].get(PROPRIA)))
        novo = ((apelido.objeto_literal or "")[:120] if apelido else "", not (propria and propria.objeto_literal == "nao"))
        if (conta.apelido, conta.propria) != novo:
            conta.apelido, conta.propria = novo
            conta.save(update_fields=["apelido", "propria"])
    return juntou


def _projetar_estabelecimentos(tenant_id, vig, categorias) -> None:
    from .lancamentos import COM_ESTABELECIMENTO, nome_legivel, normalizar_descritor

    uniao = _Uniao()
    contagem: Counter = Counter()
    descritor_do_movimento = {}
    for mid, descricao, est_id in (Movimento.objects.filter(tenant_id=tenant_id, natureza__in=COM_ESTABELECIMENTO)
                                   .values_list("id", "descricao", "estabelecimento_id")):
        if d := normalizar_descritor(descricao):
            descritor_do_movimento[mid] = (d, est_id)
            contagem[d] += 1
            uniao.achar(d)
    def de_hoje(bruto: str | None) -> str:
        """O descritor da decisão como é hoje: ele mesmo, se algum movimento ainda o tem; senão,
        normalizado de novo (a normalização mudou). Não normaliza o que já bate: ela não é
        idempotente ("SANTA CRUZ DO" perderia o "DO" como se fosse UF)."""
        return bruto if not bruto or bruto in contagem else normalizar_descritor(bruto)

    # Decisões de cada descritor (vários sujeitos antigos podem dar no mesmo descritor de hoje).
    decisoes: dict[str, dict[str, Claim]] = defaultdict(dict)
    for (sujeito, predicado), c in vig.items():
        if not (d := de_hoje(_valor(sujeito, "descritor"))):
            continue
        uniao.achar(d)
        if predicado == MESMO_LUGAR:
            if outro := de_hoje(_valor(c.objeto_ref, "descritor")):
                uniao.unir(d, outro)
        elif (atual := decisoes[d].get(predicado)) is None or _mais_recente([atual, c]) is c:
            decisoes[d][predicado] = c

    # Só vira estabelecimento o grupo que algum movimento usa; as decisões dos outros esperam.
    grupos = [g for g in uniao.grupos().values() if any(d in contagem for d in g)]
    grupos.sort(key=lambda g: (-sum(contagem[d] for d in g), min(g)))
    ests = {e.pk: e for e in Estabelecimento.objects.filter(tenant_id=tenant_id)}
    dono = dict(DescritorEstabelecimento.objects.filter(tenant_id=tenant_id).values_list("descritor", "estabelecimento_id"))
    ests_dos_movimentos = defaultdict(set)
    for d, est_id in descritor_do_movimento.values():
        if est_id:
            ests_dos_movimentos[d].add(est_id)

    alvo: dict[str, object] = {}
    usados = set()
    for g in grupos:
        do_grupo = {p: _mais_recente(c for d in g if (c := decisoes[d].get(p))) for p in (NOME, CATEGORIA, EMPRESA)}
        principal = max(sorted(d for d in g if d in contagem), key=lambda d: contagem[d])
        nome = (do_grupo[NOME].objeto_literal if do_grupo[NOME] else None) or nome_legivel(principal)
        categoria = categorias.get(_valor(do_grupo[CATEGORIA].objeto_ref, "categoria")) if do_grupo[CATEGORIA] else None
        empresa = do_grupo[EMPRESA].objeto_ref if do_grupo[EMPRESA] and do_grupo[EMPRESA].objeto_ref != NENHUM else ""
        campos = {"nome": nome[:200], "categoria_id": categoria.pk if categoria else None, "empresa_ref": empresa or ""}
        candidatos = {dono.get(d) for d in g} | set().union(*(ests_dos_movimentos[d] for d in g))
        candidatos = sorted((ests[i] for i in candidatos if i in ests and i not in usados),
                            key=lambda e: (e.criado_em, str(e.pk)))
        if candidatos:
            est = candidatos[0]
            if any(getattr(est, k) != v for k, v in campos.items()):
                for k, v in campos.items():
                    setattr(est, k, v)
                est.save(update_fields=["nome", "categoria", "empresa_ref"])
        else:
            est = Estabelecimento.objects.create(tenant_id=tenant_id, **campos)
        usados.add(est.pk)
        for d in g:
            alvo[d] = est.pk

    DescritorEstabelecimento.objects.filter(tenant_id=tenant_id).exclude(descritor__in=list(alvo)).delete()
    for d, est_id in alvo.items():
        if d in dono and dono[d] != est_id:
            DescritorEstabelecimento.objects.filter(tenant_id=tenant_id, descritor=d).update(estabelecimento_id=est_id)
    DescritorEstabelecimento.objects.bulk_create([
        DescritorEstabelecimento(tenant_id=tenant_id, descritor=d, estabelecimento_id=est_id)
        for d, est_id in alvo.items() if d not in dono
    ])

    mudar = defaultdict(list)
    for mid, (d, est_id) in descritor_do_movimento.items():
        if alvo[d] != est_id:
            mudar[alvo[d]].append(mid)
    for est_id, ids in mudar.items():
        Movimento.objects.filter(pk__in=ids).update(estabelecimento_id=est_id)
    for compra_id, descritor, est_id in (CompraParcelada.objects.filter(tenant_id=tenant_id)
                                         .values_list("id", "descritor", "estabelecimento_id")):
        if descritor in alvo and alvo[descritor] != est_id:
            CompraParcelada.objects.filter(pk=compra_id).update(estabelecimento_id=alvo[descritor])
    Estabelecimento.objects.filter(tenant_id=tenant_id).exclude(pk__in=usados).delete()
