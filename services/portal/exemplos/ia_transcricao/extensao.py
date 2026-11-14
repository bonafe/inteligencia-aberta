"""Exemplo de etapa pesada em fila própria + agendamento (ADR 023): transcrição de áudio e vídeo.

A etapa roda na fila `gpu` (só um worker iniciado com `-Q gpu` a atende) e o Beat varre, de tempos em tempos,
o que ficou sem transcrição. O motor (faster-whisper) é opcional: sem ele a etapa diz `ignorado`."""
from apps.extensoes.contrato import Agendamento, EtapaProcessamento, Extensao, Vocabulario

EXTENSAO = Extensao(
    nome="transcricao",
    rotulo="Transcrição",
    versao="1",
    requer_api=3,
    vocabulario=Vocabulario(prefixo="tr", uri="https://exemplo.invalid/ia/tr/v1#"),
    etapas=(EtapaProcessamento("transcricao.audio", "Transcrever áudio e vídeo", "ia_transcricao.etapas.transcrever",
                               tipos=("arquivo",), categorias=("audio", "video"), fila="gpu"),),
    agendamentos=(Agendamento("transcricao.varrer", "ia_transcricao.tasks.varrer_pendentes", 300.0),),
)
