"""Ferramentas do projeto: cada módulo expõe `FERRAMENTA`, um dicionário no contrato de `ferramentas.py`."""
from . import cnpj, noticias, processos

EMBUTIDAS = [cnpj.FERRAMENTA, processos.FERRAMENTA, noticias.FERRAMENTA]
