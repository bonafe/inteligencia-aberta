from email import policy
from email.parser import BytesParser

from apps.extensoes import dados
from apps.federacao import blobs

CAMPOS = ("From", "To", "Cc", "Subject", "Date", "Message-ID")


def cabecalhos(artefato, resultado):
    """Lê o `.eml` do acervo e guarda os cabeçalhos em `content["extensoes"]["email"]` (idempotente)."""
    conteudo = artefato.content
    mensagem = BytesParser(policy=policy.default).parsebytes(
        blobs.ler_blob(conteudo["bucket"], conteudo["caminho"]), headersonly=True)
    achados = {c.lower().replace("-", "_"): str(mensagem[c]) for c in CAMPOS if mensagem[c]}
    if not achados:
        resultado.vazio("sem cabeçalhos de e-mail")
        return
    dados.anexar(artefato, "email", achados)
    resultado.ok(f"e-mail de {achados.get('from', '?')}", assunto=achados.get("subject", ""))
