"""O que uma extensão de domínio declara ao núcleo (ADR 022).

Uma extensão é uma app Django que aponta, na sua `AppConfig`, para um `Extensao`:

    class MinhaExtensaoConfig(AppConfig):
        extensao = "apps.minha_extensao.extensao.EXTENSAO"

O núcleo lê só estes dados, pelo `registro`; nunca importa a extensão pelo nome. Tudo aqui é
declaração, sem comportamento: o que a extensão faz (projetar, escutar o parser de página) ela
faz do lado dela, pelos sinais e serviços públicos do núcleo.
"""

from dataclasses import dataclass, field

#: Os mesmos valores de `Artifact.ClassificationLevel`, em ordem crescente.
NIVEIS = ("publico", "interno", "restrito", "confidencial")


def mais_alto(*niveis: str | None) -> str | None:
    """O mais restrito dos níveis informados (ignora `None`): classificação só sobe."""
    validos = [n for n in niveis if n is not None]
    return max(validos, key=NIVEIS.index) if validos else None


@dataclass(frozen=True)
class Vocabulario:
    """O namespace dos predicados da extensão (`fin:valor`), versionado e imutável: mudança
    incompatível cria `v2`, nunca altera o `v1` (federação, "Nome e namespace")."""

    prefixo: str
    uri: str


@dataclass(frozen=True)
class TipoDado:
    """Um tipo de objeto da extensão. `piso` é o nível mínimo de classificação: dado deste tipo
    nunca fica abaixo dele, mesmo que o documento de origem seja menos restrito (só sobe).

    `predicados` são os do vocabulário que dizem algo sobre este tipo: a alegação com um deles
    nasce, no mínimo, no `piso` (`registrar_alegacao`). Predicado do vocabulário da extensão que
    nenhum tipo declara é recusado. O `nome` é o que as regras de replicação usam em `tipo_objeto`.
    """

    nome: str
    rotulo: str
    piso: str = "publico"
    predicados: tuple[str, ...] = ()

    def __post_init__(self):
        if self.piso not in NIVEIS:
            raise ValueError(f"piso desconhecido: {self.piso!r}")


@dataclass(frozen=True)
class Consumidor:
    """Algo que a extensão faz com a extração válida de um schema de página aprovado, ligado por
    schema na tela de revisão (`SchemaPagina.consumidores`). `chave` é estável: fica gravada."""

    chave: str
    rotulo: str
    descricao: str = ""


@dataclass(frozen=True)
class AcaoLeitura:
    """Um botão no visualizador sobre uma leitura do documento: a extração válida do parser de
    página e/ou a estruturação por LLM concluída. As rotas são relativas ao `prefixo_url` da
    extensão, com `{id}` no lugar do id da leitura; o visualizador faz POST e mostra a `mensagem`
    (e o `link`) do JSON devolvido, ou o `error`."""

    rotulo: str
    extracao: str = ""
    estruturacao: str = ""


@dataclass(frozen=True)
class ItemMenu:
    rotulo: str
    url_name: str


@dataclass(frozen=True)
class Extensao:
    nome: str
    rotulo: str
    versao: str
    vocabulario: Vocabulario
    #: Módulo de rotas e o prefixo em que é montado (`gastos/`).
    urls: str = ""
    prefixo_url: str = ""
    tipos: tuple[TipoDado, ...] = field(default_factory=tuple)
    consumidores: tuple[Consumidor, ...] = field(default_factory=tuple)
    acoes_leitura: tuple[AcaoLeitura, ...] = field(default_factory=tuple)
    menu: tuple[ItemMenu, ...] = field(default_factory=tuple)

    def tipo(self, nome: str) -> TipoDado | None:
        return next((t for t in self.tipos if t.nome == nome), None)
