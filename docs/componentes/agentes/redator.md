# Agente: Redator

## Responsabilidade

Sintetizar os resultados da investigação em linguagem natural clara. Produz dois formatos: relatório completo (para tela/PDF) e resumo conversacional (para voz e chat).

## Resultados

### Relatório Completo

Estrutura obrigatória:

1. **Sumário executivo** — 3 a 5 frases resumindo os principais achados.
2. **Achados** — seção por tema, cada achado com fonte citada.
3. **Grafo de vínculos** — referência ao grafo produzido pelo correlacionador.
4. **Inferências da IA** — seção separada e claramente marcada, com grau de confiança.
5. **Limitações** — o que não foi encontrado ou não pôde ser verificado.
6. **Fontes citadas** — lista completa com URLs e datas de acesso.
7. **Grau de confiança geral** — pontuação calculada com base nas fontes disponíveis.

### Resumo Conversacional

Para interfaces de voz e chat — texto curto, coloquial, sem estrutura formal. Apresenta os 3 achados mais relevantes e oferece aprofundamento.

## Adaptação por Persona

O redator adapta o vocabulário ao perfil do usuário (configurado no contexto):

| Persona | Estilo |
|---|---|
| Cidadão comum | Linguagem coloquial, sem jargão, analogias simples |
| Profissional técnico | Linguagem precisa, terminologia da área |
| Analista institucional | Relatório formal, numerado, exportável |

## Critérios de Aceitação

- [ ] Todo relatório tem as 7 seções obrigatórias.
- [ ] Nenhum fato no relatório sem fonte citada.
- [ ] Inferências da IA nunca aparecem na seção de "Achados" sem marcação explícita.
- [ ] Resumo conversacional cabe em < 200 palavras.
- [ ] Relatório exportável em PDF com formatação fiel ao conteúdo.
- [ ] Vocabulário do relatório adaptado ao perfil da persona configurada.
