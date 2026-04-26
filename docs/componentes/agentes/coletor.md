# Agente: Coletor

## Responsabilidade

Executar as coletas definidas pelo planejador: acionar ferramentas via MCP, obter dados de fontes externas e internas, e retornar artefatos brutos com proveniência completa.

O coletor não interpreta nem analisa — apenas coleta e registra o que veio de onde.

## Comportamento

- Executa cada ferramenta com os parâmetros definidos no plano.
- Para cada resultado, cria um artefato com metadados de proveniência (fonte, URL, data de coleta, confiança).
- Etapas paralelas do plano são executadas em paralelo.
- Em caso de falha de uma fonte, tenta fonte alternativa se disponível; registra o erro no artefato.
- Nunca descarta dado bruto — mesmo dado de baixa confiança é registrado como tal.

## Critérios de Aceitação

- [ ] Cada artefato produzido tem `fonte`, `data_coleta` e `confianca` preenchidos.
- [ ] Falha de uma fonte não interrompe coleta das demais.
- [ ] Dados de fontes indisponíveis geram artefato de erro — orquestrador decide se continua.
- [ ] Concorrência máxima configurável (padrão: 5 ferramentas simultâneas por investigação).
