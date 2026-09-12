# Observabilidade — Log de Eventos e Painel

> Decisão de origem: [`../arquitetura/decisoes/005-log-de-eventos-como-trilha-operacional.md`](../arquitetura/decisoes/005-log-de-eventos-como-trilha-operacional.md)

## Responsabilidade

Registrar de forma permanente e consultável tudo que o sistema faz com uma captura — do clique na extensão ao ponto gravado no Qdrant — e apresentar isso ao vivo.

Responde a quatro perguntas que antes não tinham resposta:

1. O que está acontecendo agora?
2. O que está na fila e o que está pendente?
3. Por que esta captura não produziu determinado dado?
4. O que aconteceu com aquela página capturada há três meses?

## Modelo

### `PipelineEvent` — o log (append-only)

Um fato ocorrido. Nunca é alterado nem apagado.

| Campo | Papel |
|---|---|
| `sequence` | Ordem total (sequência do Postgres). Cursor de replay e de reconexão do painel. |
| `correlation_id` | A unidade de trabalho ponta a ponta: uma captura. Atravessa os três serviços. |
| `causation_id` | O evento que provocou este, quando aplicável. |
| `source` | `extensao`, `orchestrator`, `portal`, `worker`, `beat`, `mcp`. |
| `stage` | Etapa, com namespace por ponto: `extracao.dom2parser`, `captura.armazenada`. |
| `status` | Ver tabela abaixo. |
| `subject_type` / `subject_id` | A que objeto se refere: `artifact`, `document_text`, `fragment`, `url_pattern`. |
| `message` | Frase curta em português, pronta para a interface. |
| `payload` | Detalhes estruturados. Teto de 8 KB. |
| `error` | Traceback, quando `falhou`. |
| `duration_ms` | Duração da etapa. |
| `hostname` / `process_id` | Identidade de execução. É o que torna o log legível com mais de um worker. |
| `celery_task_id` / `celery_task_name` | Ponte com o broker. |
| `occurred_at` / `recorded_at` | Carimbo na origem e na gravação. A diferença revela atraso de ingestão. |

### `PipelineRun` — a projeção

Uma linha por captura, derivada inteiramente do log por `apps/events/projecao.py`. Guarda o status geral, a trilha de etapas (`etapas`, JSONB), duração e contadores.

É descartável por definição: `manage.py reconstruir_projecoes` a regenera a partir dos eventos, e um teste garante que o resultado reconstruído é idêntico ao incremental.

## Semântica do `status`

| Valor | Significado |
|---|---|
| `iniciado` | A etapa começou e ainda não terminou. |
| `ok` | Terminou e produziu o resultado esperado. |
| `vazio` | **Terminou sem produzir resultado.** Não é erro: a página pode simplesmente não ter aquele dado. |
| `ignorado` | Não foi executada, por condição conhecida (já processado, sem MHTML, tipo incompatível). |
| `retentando` | Falhou e será tentada de novo. |
| `falhou` | Quebrou. `error` carrega o traceback. |

A separação entre `vazio` e `falhou` é a razão de ser deste modelo. Antes dela, um campo `NULL` em `DocumentText` era indistinguível entre "a biblioteca não está instalada", "a página não tem esse dado" e "o seletor estourou".

## Taxonomia de `stage`

| Prefixo | Emitido por | Cobre |
|---|---|---|
| `captura.*` | orchestrator, portal | `recebida`, `armazenada` (MinIO), `registrada`, `orfa` |
| `artefato.*` | portal | `ignorado` — as saídas silenciosas do signal `post_save` |
| `extracao.*` | worker | `iniciada`, `reiniciada`, `ignorada`, `minio`, `mhtml`, `trafilatura`, `dom2parser`, `extruct`, `cascata`, `schema_cache`, `llm`, `concluida` |
| `deteccao.*` | worker | `page_type` |
| `fragmentacao.*` | worker | `concluida`, `ignorada` |
| `embedding.*` | worker | `concluido`, `ignorado` |
| `task.*` | qualquer | `enfileirada`, `iniciada`, `concluida`, `falhou`, `retentada`, `revogada` — automático, via signals do Celery |
| `worker.*` | worker | `pronto`, `encerrando` — inventário de nós |
| `catchup.*` | beat | `varredura` — só quando há algo a reenfileirar |
| `politica.*` | orchestrator | `decisao` |
| `ferramenta.*` | mcp | `chamada` |
| `reprocessamento.*` | portal | `solicitado` |

`captura.orfa` merece destaque: é emitido quando o MHTML foi gravado no MinIO mas o registro no portal falhou. Sem ele, essa captura desaparecia sem rastro, porque o catch-up periódico varre `Artifact` e não o bucket.

## Regras do `payload`

- Teto de 8 KB serializado. Acima disso, os escalares são preservados e o resto vira `{"_truncado": true, "_bytes_originais": n}`.
- Nunca carrega conteúdo capturado: `html`, `mhtml`, `text`, `dom_representation`, `skeleton`. Só tamanhos, contagens, percentuais e nomes.
- Nunca carrega segredo: `api_key`, `token`, `password`, `secret`, `authorization`. Chaves descartadas são contadas em `_chaves_omitidas`.

## Propagação da correlação

```
extensão  crypto.randomUUID()  ─┐
                                ├─> form field correlation_id
orchestrator  aceita ou gera   ─┘
      │
      ├─> POST /artifacts/api/v1/artefatos/  {"correlation_id": …}
      │        └─> Artifact.content["correlation_id"]
      │
      └─> signal post_save ─> contextvar ─> header da task Celery
                                    └─> task_prerun repõe o contextvar
```

Quando um artefato não tem correlação declarada — capturas anteriores a este log, artefatos criados por `sync_minio_postgres.py` — usa-se `uuid5(NAMESPACE, artifact_id)`: determinístico, de modo que reprocessamentos do mesmo artefato caem na mesma execução.

Eventos sem correlação alguma (um worker subindo, a varredura do catch-up) são gravados no log mas **não** geram projeção: não pertencem a captura nenhuma.

## Endpoint de ingestão

`POST /eventos/api/v1/ingest/` — canal serviço-a-serviço, autenticado por `X-Internal-Token` com `constant_time_compare`, mesmo padrão de `ArtefatoCreateAPIView`. Aceita um evento ou um lote (`{"eventos": [...]}`, máximo 200).

Clientes: `services/orchestrator/eventos.py` e `services/mcp/eventos.py`. Ambos despacham em thread, com timeout curto, e nunca levantam.

## Tempo real

WebSocket em `/ws/eventos/`, via Django Channels com `channels-redis` no banco 1 do Redis (separado do broker do Celery, banco 0).

O consumer:
- recusa conexão anônima;
- assina **apenas** os grupos das organizações do usuário (`orgs_do_usuario`) — o isolamento multi-tenant é feito na assinatura, não na apresentação;
- aceita `?desde=<sequence>` e envia o backfill do Postgres antes de assinar;
- aceita filtros vindos do cliente, que só restringem.

O backfill relê com uma janela de segurança de 5 segundos: sob concorrência, um evento com `sequence` menor pode se tornar visível depois de um maior. O cliente deduplica por `id`.

Quando o WebSocket cai, o painel passa a fazer polling em `/eventos/api/v1/eventos/?desde=<sequence>` e volta ao WebSocket assim que possível. Perder o tempo real não pode significar perder o histórico.

## Interface

`/eventos/` — quatro zonas:

1. **Agora**: capturas em andamento, profundidade da fila, tasks ativas, pendências dos três gaps, falhas em 24 h.
2. **Execuções**: uma linha por captura com a trilha marcando ok etapa a etapa.
3. **Detalhe** (`/eventos/<correlation_id>/`): linha do tempo completa, com payload expansível, nó e processo executor, e botão de reprocessamento.
4. **Fluxo bruto**: todos os eventos, filtráveis por etapa, status e serviço, com pausa.

## Diagnóstico e reprocessamento

```bash
# Uma captura específica, por id ou por URL
manage.py reprocessar_captura <artifact_id|url> --forcar

# Capturas cujo DocumentText está sem um campo — o buraco que nenhum
# mecanismo automático cobre (o catch-up só enxerga artefato SEM DocumentText)
manage.py reprocessar_incompletos --criterio dom2parser --limite 20 [--simular]

# Regenera a projeção a partir do log
manage.py reconstruir_projecoes
```

## Retenção

Indefinida, por decisão de projeto. Não há purga. O controle de volume é o teto de payload e a ausência de projeção para eventos de infraestrutura.

## O que isto **não** é

Não substitui o `AuditLog`. O `AuditLog` é a trilha de compliance (quem acessou o quê, retenção mínima de dois anos, exportável pelo administrador da organização — ver [`../seguranca/classificacao.md`](../seguranca/classificacao.md)). O `PipelineEvent` é o diário operacional. Uma decisão do motor de políticas escreve nos dois.
