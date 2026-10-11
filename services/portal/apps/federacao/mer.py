"""MER da federação, lido dos models reais (Django) — alimenta o site e a tela de regras.

As **relações** vêm da introspecção das chaves estrangeiras, então não desatualizam.
Os **campos** mostrados são uma lista curada (`CAMPOS`): o diagrama é para entender, não
para listar colunas; um teste confere que todo campo citado ainda existe.

As entidades `PLANEJADAS` **não existem no banco**: aparecem rotuladas "(planejado)" para
o diagrama não afirmar mais do que o código faz.
"""

from django.apps import apps

#: modelo ("app.Modelo") -> campos mostrados. A PK é sempre `id` (UUID).
CAMPOS: dict[str, tuple[str, ...]] = {
    "accounts.Organization": ("name",),
    "artifacts.Artifact": ("artifact_type", "classification_level", "blob_hash", "tenant"),
    "artifacts.Claim": ("sujeito_ref", "predicado", "objeto_ref", "autor_ref", "estado"),
    "artifacts.Evidence": ("blob_hash", "localizador_tipo", "trecho"),
    "federacao.Space": ("nome", "arquivado", "organizacao"),
    "federacao.EspacoArtefato": ("espaco", "artefato"),
    "federacao.RegraReplicacao": (
        "efeito", "sentido", "par_ref", "par_tipo", "nivel", "tipo_objeto",
        "espaco_urn", "objeto_urn", "valida_ate", "padrao",
    ),
    "federacao.ChaveInstancia": ("did", "estado"),
    "cluster.Maquina": ("apelido", "did", "tipo", "estado", "eh_local"),
    "cluster.ConviteEnrolamento": ("tipo", "expira_em", "usado_em"),
}

#: Só as relações entre estes modelos entram no desenho.
MODELOS = tuple(CAMPOS)

#: Ainda sem tabela: (nome, campos, relações "Origem ||--o{ Destino : rótulo").
PLANEJADAS = (
    ("FederationEvent", ("space", "author", "type", "prev", "payload", "sig"),
     ("Space ||--o{ FederationEvent : contém",)),
    ("Foto", ("blob_hash", "exif", "capturada_em"), ("Artifact ||--o| Foto : é",)),
    ("Tag", ("nome",), ("Tag }o--o{ Artifact : etiqueta",)),
)

#: Rótulos curtos das relações que o Django só sabe chamar pelo nome do campo.
_ROTULO = {
    ("RegraReplicacao", "organizacao"): "tem regras",
    ("Space", "organizacao"): "tem espaços",
    ("Maquina", "organizacao"): "tem pares",
    ("Artifact", "tenant"): "é dono de",
    ("Claim", "artefato"): "origina",
    ("Claim", "tenant"): "é dono de",
    ("Evidence", "claim"): "tem prova",
    ("EspacoArtefato", "espaco"): "lista",
    ("EspacoArtefato", "artefato"): "está em",
    ("ConviteEnrolamento", "organizacao"): "convida",
}


def _modelo(rotulo: str):
    return apps.get_model(rotulo)


def entidades() -> list[dict]:
    """Entidades reais: nome, tabela e os campos curados com o tipo do Django."""
    saida = []
    for rotulo, campos in CAMPOS.items():
        m = _modelo(rotulo)
        itens = [{"nome": "id", "tipo": "uuid", "chave": "PK"}]
        for nome in campos:
            f = m._meta.get_field(nome)
            tipo = "uuid" if f.is_relation else f.get_internal_type().replace("Field", "").lower()
            itens.append({"nome": nome, "tipo": tipo, "chave": "FK" if f.is_relation else ""})
        saida.append({"nome": m.__name__, "tabela": m._meta.db_table, "campos": itens})
    return saida


def relacoes() -> list[tuple[str, str, str, str]]:
    """(origem, destino, cardinalidade mermaid, rótulo) a partir das FKs entre os modelos do desenho."""
    alvo = {_modelo(r) for r in MODELOS}
    saida = []
    for rotulo in MODELOS:
        m = _modelo(rotulo)
        for f in m._meta.get_fields():
            if f.is_relation and f.concrete and not f.many_to_many and f.related_model in alvo:
                card = "|o--o{" if f.null else "||--o{"
                rot = _ROTULO.get((m.__name__, f.name), f.name)
                saida.append((f.related_model.__name__, m.__name__, card, rot))
    return sorted(saida)


def mermaid() -> str:
    linhas = ["erDiagram"]
    for e in entidades():
        linhas.append(f"    {e['nome']} {{")
        for c in e["campos"]:
            chave = f" {c['chave']}" if c["chave"] else ""
            linhas.append(f"        {c['tipo']} {c['nome']}{chave}")
        linhas.append("    }")
    for o, d, card, rot in relacoes():
        linhas.append(f'    {o} {card} {d} : "{rot}"')
    for nome, campos, rels in PLANEJADAS:
        linhas.append(f'    {nome}["{nome} (planejado)"] {{')
        for c in campos:
            linhas.append(f"        string {c}")
        linhas.append("    }")
        for r in rels:
            origem, resto = r.split(" ", 1)
            card, destino, _, rotulo = resto.split(" ", 3)
            linhas.append(f'    {origem} {card} {destino} : "{rotulo}"')
    return "\n".join(linhas) + "\n"
