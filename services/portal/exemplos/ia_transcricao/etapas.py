import tempfile
from pathlib import Path

from apps.extensoes import dados
from apps.federacao import blobs


def _motor():
    """`transcrever(caminho) -> str`, ou None se o motor não está instalado nesta instância."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    modelo = WhisperModel("small")

    def transcrever(caminho: str) -> str:
        segmentos, _ = modelo.transcribe(caminho)
        return " ".join(s.text.strip() for s in segmentos)

    return transcrever


def transcrever(artefato, resultado):
    if dados.ler(artefato, "transcricao"):
        resultado.ignorado("já transcrito")
        return
    motor = _motor()
    if motor is None:
        resultado.ignorado("sem motor de transcrição (faster-whisper) nesta instância")
        return
    conteudo = artefato.content
    with tempfile.TemporaryDirectory(prefix="transcricao-") as tmp:
        destino = Path(tmp) / conteudo.get("nome", "midia")
        destino.write_bytes(blobs.ler_blob(conteudo["bucket"], conteudo["caminho"]))
        texto = motor(str(destino)).strip()
    if not texto:
        resultado.vazio("o áudio não rendeu texto")
        return
    dados.anexar(artefato, "transcricao", {"texto": texto, "motor": "faster-whisper"})
    resultado.ok(f"{len(texto)} caracteres transcritos", caracteres=len(texto))
