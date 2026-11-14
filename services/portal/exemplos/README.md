# Extensão de exemplo

`ia_exemplo` é a menor extensão de domínio que cumpre o contrato (ADR 022/023): um vocabulário, um tipo
com piso de classificação, um item de menu e uma rota. Serve de modelo e de teste de que uma extensão
**fora de `apps/`** é descoberta pelo núcleo sem editar `INSTALLED_APPS`.

Para ligá-la numa instância, ponha esta pasta no `PYTHONPATH` (ou instale o pacote com `pip install ./exemplos`)
e defina `EXTENSOES=exemplo` (nome do entry point) ou `EXTENSOES=ia_exemplo.apps.ExemploConfig`.
