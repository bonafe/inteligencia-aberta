# Decisão-005: Log de eventos append-only como trilha operacional do sistema

**Status:** Aceito  
**Data:** 2026-09-11

## Contexto

O sistema executa um pipeline assíncrono distribuído por três serviços (extensão → orquestrador → portal → workers Celery) e não conseguia contar o que fazia. Todo o rastro de execução existia apenas como `logger.info` no stdout de contêineres: quando o contêiner reiniciava, a história desaparecia. Nenhum modelo de domínio guarda estado de processamento — o progresso era inferido pela *ausência* de linhas em outras tabelas (`scan_unprocessed_documents` procura buracos), e falhas não deixavam marca alguma no banco.

Na prática isso significava que perguntas operacionais básicas não tinham resposta: o que está sendo processado agora, o que está na fila, por que esta captura não gerou determinado dado, o que aconteceu com aquela página capturada na semana passada. Um caso concreto motivou a decisão: uma captura não gerou os dados do `dom2parser`, e a investigação revelou oito caminhos distintos de código que produzem esse mesmo sintoma — vários deles sem log nenhum. Pior: quando o registro do artefato no portal falha, o MHTML fica órfão no MinIO, nenhum `Artifact` é criado, e o catch-up periódico não o vê, porque varre `Artifact` e não o bucket. A captura simplesmente sumia.

Como a próxima etapa do projeto é escala horizontal, qualquer solução precisava nascer multiprocesso, e não ser retrofitada depois.

Três abordagens foram consideradas:

- **Agregação de logs (ELK, Loki):** resolve a persistência, mas trata o rastro como texto. Perguntar "quantas capturas estão pendentes" ou "qual estratégia de extração venceu" exigiria parsing frágil, e a correlação entre serviços continuaria sendo feita por *grep*.
- **Event sourcing pleno:** `Artifact`, `DocumentText` e `DocumentFragment` deixariam de ser escritos diretamente e passariam a ser projeções materializadas do log. Máxima pureza e replay total, ao custo de reescrever o pipeline inteiro, todas as views e o admin — desproporcional para a Fase 0 e arriscado para o contrato de classificação.
- **Log de eventos append-only com projeção derivada:** um registro estruturado por etapa, com ordem total e correlação ponta a ponta, do qual se derivam as visões de leitura. Os modelos de domínio seguem como estão.

## Decisão

Adotamos o log de eventos append-only como trilha operacional do sistema.

Toda etapa relevante de todo serviço emite um `PipelineEvent`: uma linha imutável com `stage` (que etapa), `status` (como terminou), `correlation_id` (a que captura pertence), `payload` estruturado, duração, e a identidade da máquina e do processo que executou. O modelo vive em `apps/events/` no portal, que é o dono do log; orquestrador e MCP despacham por HTTP no mesmo canal serviço-a-serviço já usado para criar artefatos (`X-Internal-Token`).

O `status` tem seis valores, e a distinção entre `vazio` e `falhou` é deliberada e central: separa "a etapa rodou e não produziu resultado" de "a etapa quebrou". Era exatamente essa distinção que faltava — um campo `NULL` no banco não dizia qual dos dois havia acontecido.

Do log deriva-se `PipelineRun`, uma projeção com uma linha por captura, que serve as telas rápidas e as contagens de pendência. A projeção é descartável: `manage.py reconstruir_projecoes` a regenera integralmente a partir dos eventos, e a equivalência entre a construção incremental e a reconstruída é verificada por teste automatizado. Nenhum estado de negócio depende do log — `Artifact`, `DocumentText`, `DocumentFragment` e `AuditLog` permanecem inalterados.

A emissão é infalível por construção: `emit()` nunca propaga exceção a quem a chamou. Observabilidade que derruba o pipeline é pior que a ausência dela.

O transporte ao vivo é WebSocket via Django Channels, com o Redis (banco 1, separado do broker do Celery) como channel layer, o que dá fanout para várias réplicas do portal sem coordenação adicional. O Postgres é o histórico durável; o channel layer é apenas transporte.

## Consequências

**Positivas:**
- Toda captura tem uma linha do tempo completa e permanente, do clique na extensão ao ponto gravado no Qdrant, recuperável meses depois.
- Falhas deixam traceback no banco, não só no stdout de um contêiner que pode já ter sido reiniciado.
- Pendências e profundidade de fila viram número consultável, calculado pela mesma função que o catch-up usa para decidir o que reenfileirar.
- Em cluster, cada evento diz em que nó e em que processo ocorreu.
- Diagnosticar um caso passa a ser reproduzível: reprocessa-se a captura e a trilha diz o que houve.
- O log serve de base para as métricas agregadas e o tracing de agentes previstos para a Fase 5, em vez de competir com eles.

**Negativas:**
- Volume: uma captura gera algumas dezenas de eventos, e a retenção é indefinida por decisão de projeto. Uma página com muitos fragmentos multiplica as linhas.
- Uma escrita adicional por etapa no caminho quente do pipeline.
- Dois lugares registram uma decisão de política (`AuditLog` e `PipelineEvent`), com a redundância que isso implica.
- O portal passa a exigir servidor ASGI (daphne) e, por consequência, WhiteNoise para servir os próprios estáticos.

**Mitigações:**
- O `payload` tem teto de 8 KB e descarta chaves de conteúdo e de segredo; eventos de infraestrutura não geram projeção.
- A gravação é barata (um `INSERT` mais um `UPDATE` na projeção); a publicação no channel layer é isolada e best-effort.
- A redundância com o `AuditLog` é intencional e está normatizada abaixo.

**Restrições geradas:**
- Toda etapa do pipeline emite evento, e emitir evento nunca pode derrubar a etapa.
- `PipelineEvent` é append-only: nenhum processo faz `UPDATE` ou `DELETE`, e o admin é somente leitura.
- `PipelineEvent` **não substitui** o `AuditLog`. O `AuditLog` é a trilha de compliance exigida por `docs/seguranca/classificacao.md`; o evento é a trilha operacional. Quando uma operação exige auditoria, as duas são escritas.
- O motor de políticas permanece determinístico e sem I/O: ele decide, e quem consome a decisão a registra. Emitir evento jamais pode influenciar uma decisão de política.
- Toda projeção derivada do log deve ser reconstruível a partir dele, e essa equivalência deve ser coberta por teste.
- Todo serviço propaga o `correlation_id` que recebe, e o gera apenas quando é a origem da unidade de trabalho.
- O `payload` de um evento nunca carrega conteúdo capturado (HTML, MHTML, texto extraído, representação compacta) nem segredo.
