# Decisão-003: LLM local obrigatório para dados restritos e confidenciais

**Status:** Aceito  
**Data:** 2026-04-25

## Contexto

A plataforma processa dados pessoais, financeiros e institucionais sensíveis. Modelos de linguagem externos (APIs de terceiros) são mais capazes e mais fáceis de usar, mas enviar dados sensíveis para eles representa risco de privacidade, incompatibilidade com LGPD, e potencial inviabilidade jurídica em contextos institucionais (ex: Receita Federal não pode enviar dados fiscais para servidores de terceiros).

## Decisão

Dados classificados como `restrito` ou `confidencial` são processados exclusivamente por LLMs rodando localmente (on-premise ou no próprio servidor do usuário). O Motor de Políticas bloqueia qualquer chamada a LLM externo quando o payload contém dados com essas classificações.

Dados `público` e `interno` podem usar LLMs externos.

O roteamento é feito pelo Motor de Políticas (código determinístico), não pelo LLM.

## Consequências

**Positivas:**
- Conformidade com LGPD por design, não por configuração.
- Viabilidade para organizações com restrições legais estritas.
- Usuários com dados sigilosos podem usar o sistema com confiança.

**Negativas:**
- LLMs locais têm capacidade inferior aos modelos externos de ponta.
- Custo de infraestrutura maior: o usuário/organização precisa de hardware capaz de rodar LLM local.
- Experiência pode ser mais lenta para dados sensíveis.

**Mitigações:**
- Para o MVP, dados sensíveis são processados com modelos locais menores mas suficientes (ex: Llama 3, Mistral).
- Separação clara de qual parte do fluxo usa LLM local vs. externo — dados públicos de suporte à análise podem usar modelos externos.

**Restrições geradas:**
- O Motor de Políticas é a única autoridade sobre roteamento de LLM — agentes não tomam essa decisão.
- A infraestrutura deve sempre ter ao menos um LLM local disponível antes de aceitar dados sensíveis.
- Registros devem indicar qual LLM processou cada dado.
