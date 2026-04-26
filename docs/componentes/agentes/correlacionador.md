# Agente: Correlacionador

## Responsabilidade

Encontrar conexões e padrões entre artefatos coletados e extraídos. Produz o grafo de vínculos entre entidades — pessoas, empresas, endereços, processos.

## Operações

- **Deduplicação:** Identifica que "João da Silva" e "JOÃO DA SILVA" nos documentos são a mesma entidade.
- **Resolução de entidades:** Liga CPF à pessoa, CNPJ à empresa, número ao processo.
- **Identificação de vínculos:** Detecta que a mesma pessoa aparece como sócia em múltiplas empresas.
- **Análise de padrões:** Empresas com mesmo endereço, sócios com histórico de falências, etc.

## Saída

Artefatos de vínculo com `tipo`, `origem`, `destino`, `fonte` e `confiança`. Estes são persistidos no Neo4j via MCP.

## Critérios de Aceitação

- [ ] Deduplicação de entidades com mesmo CPF/CNPJ é 100% precisa.
- [ ] Vínculos produzidos têm sempre `fonte` associada — nenhum vínculo sem evidência.
- [ ] Grafo com 500 entidades processado em < 30 segundos.
- [ ] Vínculos de baixa confiança (< 0.7) são marcados explicitamente.
