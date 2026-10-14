"""Protocolo de arquivos com o `iped-runner` (infra/iped/runner.sh) — ADR 019.

O portal e o worker **não falam com o Docker**: escrevem um pedido num volume compartilhado e
leem o estado que o runner grava. O runner (sem rede, entrada somente leitura) é o único que
executa o IPED. Trocar o IPED de contêiner por um nativo no host não muda nada aqui.
"""

import re
from pathlib import Path

from django.conf import settings

ESTADOS_FINAIS = ("concluido", "falhou", "cancelado")
_RE_RELATIVO = re.compile(r"^[^\x00\r\n/-][^\x00\r\n]*$")


def trabalho() -> Path:
    return Path(settings.IPED_TRABALHO_DIR)


def _escrever(caminho: Path, texto: str) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(caminho.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.replace(caminho)


def enviar(pedido_id, *, perfil: str, relativo: str) -> None:
    """Grava o pedido. O runner revalida tudo, mas aqui já se recusa o que não cabe no formato."""
    if not _RE_RELATIVO.match(relativo or "") or ".." in Path(relativo).parts:
        raise ValueError("caminho de entrada inválido para o runner")
    _escrever(trabalho() / "pedidos" / f"{pedido_id}.pedido", f"perfil={perfil}\nentrada={relativo}\n")


def enviar_exportacao(pedido_id, *, caso, bookmark: str = "", consulta: str = "") -> None:
    """Pede ao runner a lista dos itens de um caso já processado: um bookmark OU uma consulta (nunca o caso inteiro)."""
    if bool(bookmark) == bool(consulta):
        raise ValueError("informe um bookmark ou uma consulta (e só um)")
    for valor in (bookmark, consulta):
        if "\n" in valor or "\r" in valor or "\x00" in valor:
            raise ValueError("bookmark/consulta não podem ter quebra de linha")
    linhas = f"acao=exportar\ncaso={caso}\n" + (f"bookmark={bookmark}\n" if bookmark else f"consulta={consulta}\n")
    _escrever(trabalho() / "pedidos" / f"{pedido_id}.pedido", linhas)


def resultado(pedido_id) -> Path:
    return trabalho() / "resultados" / f"{pedido_id}.jsonl"


def estado(pedido_id) -> str | None:
    """`executando`/`concluido`/`falhou`/`cancelado`, ou `None` se o runner ainda não pegou o pedido."""
    arq = trabalho() / "estado" / str(pedido_id)
    try:
        return arq.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def codigo_de_saida(pedido_id) -> int | None:
    try:
        return int((trabalho() / "codigo" / str(pedido_id)).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def pedir_cancelamento(pedido_id) -> None:
    _escrever(trabalho() / "cancelar" / str(pedido_id), "")


def final_do_log(pedido_id, limite: int = 4000) -> str:
    try:
        dados = (trabalho() / "logs" / f"{pedido_id}.log").read_bytes()
    except FileNotFoundError:
        return ""
    return dados[-limite:].decode("utf-8", errors="replace")
