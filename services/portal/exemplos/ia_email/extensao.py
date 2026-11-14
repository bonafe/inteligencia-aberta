"""Exemplo de fonte de ingestão + etapa de processamento (ADR 023): e-mails em arquivos `.eml`.

A fonte traz uma pasta de `.eml` para o acervo (o núcleo guarda, deduplica e cataloga); a etapa roda para
cada `.eml` novo e extrai os cabeçalhos. Nenhum tipo de artefato novo: o e-mail é um `arquivo`."""
from apps.extensoes.contrato import EtapaProcessamento, Extensao, FonteIngestao, Vocabulario

EXTENSAO = Extensao(
    nome="email",
    rotulo="E-mail",
    versao="1",
    requer_api=3,
    vocabulario=Vocabulario(prefixo="mail", uri="https://exemplo.invalid/ia/mail/v1#"),
    fontes=(FonteIngestao("email.pasta", "Pasta de e-mails (.eml)",
                          "Importa os arquivos .eml de uma pasta para o acervo.", "ia_email.fonte.importar"),),
    etapas=(EtapaProcessamento("email.cabecalhos", "Cabeçalhos do e-mail", "ia_email.etapas.cabecalhos",
                               tipos=("arquivo",), sufixos=(".eml",)),),
)
