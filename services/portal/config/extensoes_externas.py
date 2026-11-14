"""Extensões instaladas de fora do repositório (ADR 023).

`EXTENSOES` (variável de ambiente) lista, separados por vírgula, o que a instância liga além do que
vem no repositório. Cada item é uma de duas coisas:

- o **nome de um entry point** do grupo `inteligencia_aberta.extensoes` (um pacote instalado com
  `pip` que declara `nome = "pacote.apps:MinhaConfig"` no seu `pyproject.toml`); ou
- o **caminho pontuado** de uma `AppConfig` (ou de um app) que já está no `PYTHONPATH`, por exemplo
  numa pasta montada em volume.

Nada é ligado só por estar instalado: o administrador da instância escolhe, pelo nome. Quem está
nessa lista roda dentro do processo do portal, com acesso total — só se instala código confiável.
Este módulo não importa o Django: é lido pelos settings antes de o Django carregar os apps.
"""
from importlib.metadata import entry_points

GRUPO = "inteligencia_aberta.extensoes"


def apps_das_extensoes(valor: str, entry_points_do_grupo=None) -> list[str]:
    """Os caminhos de `AppConfig` a acrescentar a `INSTALLED_APPS` (sem repetir, na ordem dada)."""
    nomes = [n.strip() for n in (valor or "").split(",") if n.strip()]
    if not nomes:
        return []
    if entry_points_do_grupo is None:
        entry_points_do_grupo = entry_points(group=GRUPO)
    por_nome = {ep.name: ep.value for ep in entry_points_do_grupo}
    apps: list[str] = []
    for nome in nomes:
        alvo = por_nome.get(nome, nome).replace(":", ".")   # "pacote.apps:Config" → "pacote.apps.Config"
        if alvo not in apps:
            apps.append(alvo)
    return apps
