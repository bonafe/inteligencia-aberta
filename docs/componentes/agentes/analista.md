# Agente: Analista

## Responsabilidade

Gerar insights a partir dos artefatos validados e do grafo de vínculos. Produz conclusões, identifica padrões relevantes, e avalia o significado dos dados no contexto da investigação.

## Comportamento

- Recebe artefatos validados e o grafo de vínculos.
- Cruza com a consulta original do usuário para manter relevância.
- Produz insights classificados como `inferência` — nunca como `fato`.
- Indica o grau de confiança de cada insight.
- Sinaliza o que não foi possível determinar.

## Roteamento de LLM

O analista usa o LLM definido pelo Motor de Políticas para o contexto da investigação:
- Dados `público` ou `interno`: pode usar LLM externo.
- Dados `restrito` ou `confidencial`: usa LLM local obrigatoriamente.

## Critérios de Aceitação

- [ ] Todo insight produzido referencia os artefatos que o sustentam.
- [ ] Insights são classificados como `inferência` — nunca confundidos com fato.
- [ ] Analista produz seção explícita de limitações: "o que não foi possível determinar".
- [ ] Análise de investigação padrão (50 artefatos) entregue em < 60 segundos.
