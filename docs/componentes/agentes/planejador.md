# Agente: Planejador

## Responsabilidade

Receber a entrada do usuário e produzir um plano de investigação estruturado: quais fontes consultar, quais agentes acionar, em que ordem, com que parâmetros. É o primeiro agente executado em toda investigação.

## Entrada

```json
{
  "query": "Investiga a empresa XPTO LTDA, CNPJ 00.000.000/0001-00",
  "classificacao": "público",
  "contexto_usuario": { "persona": "analista", "historico_relevante": [] }
}
```

## Saída

```json
{
  "objetivo": "Mapear vínculos societários e processos judiciais da empresa XPTO LTDA",
  "etapas": [
    {
      "ordem": 1,
      "agente": "coletor",
      "ferramentas": ["consultar_cnpj", "buscar_processos"],
      "parametros": { "cnpj": "00.000.000/0001-00" },
      "paralelo": false
    },
    {
      "ordem": 2,
      "agente": "coletor",
      "ferramentas": ["consultar_cnpj"],
      "parametros": { "cnpjs": ["sócios identificados na etapa 1"] },
      "paralelo": true,
      "depende_de": [1]
    },
    {
      "ordem": 3,
      "agente": "correlacionador",
      "depende_de": [1, 2]
    }
  ],
  "fontes_previstas": ["Receita Federal", "CNJ"],
  "estimativa_duracao_s": 45
}
```

## Critérios de Aceitação

- [ ] Plano produzido em < 5 segundos.
- [ ] Plano nunca inclui ferramentas bloqueadas pela classificação do dado.
- [ ] Etapas com dependência corretamente ordenadas — sem ciclos.
- [ ] Para consultas ambíguas, planejador retorna pergunta de esclarecimento em vez de plano parcial.
