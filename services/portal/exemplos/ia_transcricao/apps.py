from django.apps import AppConfig


class TranscricaoConfig(AppConfig):
    name = "ia_transcricao"
    label = "ia_transcricao"
    verbose_name = "Transcrição de áudio e vídeo (exemplo de etapa em fila própria)"
    extensao = "ia_transcricao.extensao.EXTENSAO"
