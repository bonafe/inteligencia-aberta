"""Importa para o IA os itens que o IPED encontrou num caso — Fluxo B (ADR 019).

O IPED roda primeiro, sobre a pasta do host, **sem copiar nada**; você escolhe o que interessa (um
bookmark ou uma consulta) e só isso entra no acervo. O que o IA grava é o **arquivo original**,
lido da raiz permitida: o IA calcula o próprio SHA-256 (o `fastmode` do IPED nem calcula hash) e
guarda o blob no Garage como no Fluxo A. O que o IPED disse (categoria, tipo, id) vai em
`content["externo"]["iped:<caso>"]`, como proveniência.

Fora desta versão (contados, não importados): itens **dentro** de contêineres (zip, e-mail, imagem de
disco — `subitem`), apagados e recuperados (`deleted`/`carved`): não há arquivo no disco para ler.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from apps.artifacts import acervo
from apps.events.emit import emit

from . import caminhos, fila
from .models import ProcessamentoIPED

INTERVALO_S = 2


class ErroImportacao(ValueError):
    pass


@dataclass
class ResumoCaso:
    selecionados: int = 0
    sem_arquivo_no_disco: int = 0     # subitem, apagado, recuperado
    pastas: int = 0
    do_acervo: acervo.Resumo = field(default_factory=acervo.Resumo)


def exportar_itens(pedido: ProcessamentoIPED, *, bookmark: str = "", consulta: str = "", espera_s: int = 3600) -> Path:
    """Pede ao runner a lista de itens e espera o arquivo. `ErroImportacao` se o runner falhar ou estourar o prazo."""
    if pedido.estado != ProcessamentoIPED.Estado.CONCLUIDO:
        raise ErroImportacao(f"o processamento está '{pedido.estado}'; só um caso concluído pode ser importado")
    import uuid

    pid = uuid.uuid4()
    try:
        fila.enviar_exportacao(pid, caso=pedido.id, bookmark=bookmark, consulta=consulta)
    except ValueError as exc:
        raise ErroImportacao(str(exc)) from None
    limite = time.monotonic() + espera_s
    while True:
        atual = fila.estado(pid)
        if atual == "concluido":
            return fila.resultado(pid)
        if atual in ("falhou", "cancelado"):
            raise ErroImportacao("a exportação falhou no runner: " + fila.final_do_log(pid, 1500))
        if time.monotonic() > limite:
            fila.pedir_cancelamento(pid)
            raise ErroImportacao("o runner não respondeu dentro do prazo")
        time.sleep(INTERVALO_S)


def _linhas(arquivo: Path):
    with open(arquivo, encoding="utf-8") as fh:
        for linha in fh:
            if linha.strip():
                yield json.loads(linha)


def _original(item: dict, base: Path) -> Path | None:
    """O arquivo original do item dentro da evidência `base`, ou None se não existe como arquivo no disco."""
    if item.get("subitem") == "true" or item.get("deleted") == "true" or item.get("carved") == "true":
        return None
    rel = item.get("idInDataSource") or ""
    if not rel or rel.startswith("/") or "\x00" in rel:
        return None
    alvo = (base / rel).resolve()
    if base != alvo and base not in alvo.parents:
        return None   # link simbólico ou ".." fugindo da evidência
    return alvo if alvo.is_file() else None


def importar_itens(pedido: ProcessamentoIPED, arquivo: Path, *, usuario=None, nivel: str = "restrito",
                   simular: bool = False, progresso=None) -> ResumoCaso:
    base = caminhos.resolver(pedido.raiz, pedido.relativo)
    raiz = Path(pedido.raiz).resolve()
    if base.is_file():
        raise ErroImportacao("o processamento foi pedido sobre um arquivo; use uma pasta")
    resumo = ResumoCaso()
    if not simular:
        acervo.blobs.garantir_bucket(acervo.blobs.BUCKET_ACERVO)

    for item in _linhas(arquivo):
        resumo.selecionados += 1
        if item.get("isDir") == "true":
            resumo.pastas += 1
            continue
        original = _original(item, base)
        if original is None:
            resumo.sem_arquivo_no_disco += 1
            continue
        relativo = str(original.relative_to(raiz))
        acervo_r = resumo.do_acervo
        acervo_r.arquivos += 1
        extra = {"chave": f"iped:{pedido.id}",
                 "dados": {"item": item.get("id"), "categoria": item.get("category"),
                           "tipo": item.get("contentType"), "caminho_no_caso": item.get("path"),
                           "versao_iped": pedido.versao_iped, "perfil": pedido.perfil}}
        try:
            acervo._importar_arquivo(original, relativo, str(raiz), pedido.organizacao, usuario, nivel,
                                     acervo_r, simular, extra=extra,
                                     sha256_esperado=(item.get("hashes") or {}).get("sha-256"))
        except Exception as exc:
            acervo_r.erro(relativo, exc)
        if progresso:
            progresso(resumo, relativo)

    a = resumo.do_acervo
    emit("forense.iped.importado", "ok" if not a.total_erros else "falhou", source="portal",
         subject_type="processamento_iped", subject_id=pedido.id, tenant_id=pedido.organizacao_id,
         message=f"IPED → acervo: {a.novos} novo(s), {a.duplicados} duplicado(s), {a.pulados} pulado(s), "
                 f"{resumo.sem_arquivo_no_disco} sem arquivo no disco, {a.total_erros} erro(s)",
         payload={"selecionados": resumo.selecionados, "novos": a.novos, "duplicados": a.duplicados,
                  "pulados": a.pulados, "sem_arquivo": resumo.sem_arquivo_no_disco, "erros": a.total_erros,
                  "simulado": simular})
    return resumo
