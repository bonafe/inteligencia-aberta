# Cluster multi-máquina (fase 1 — mesma organização, confiança total)

Este documento descreve o que está **implementado hoje**: dono único, todas
as máquinas da mesma organização — não há nenhum gate de "o que pode ir para
qual máquina" ainda. Não existe conceito de "máquina primária" no
vocabulário do cluster: existe capacidade — uma máquina hospeda (ou não) a
infraestrutura compartilhada (Postgres/Redis/MinIO/Qdrant). Nenhum outro
tratamento depende disso; o roteador de LLM, por exemplo, trata toda
`Maquina` como igual, hospede infra ou não. O rumo — múltiplos donos, mais
capacidades por máquina (armazenamento/borda pública/offline), motor de
posicionamento com critério e colaboração entre organizações via `Projeto`
— está desenhado em [ADR-006](../arquitetura/decisoes/006-cluster-adaptativo-multiproprietario.md)
e nos [perfis de implantação](../visao/perfis-de-implantacao.md), ainda não
implementado. O desenho atual (`Maquina.organizacao`, `EventoReplicacao`)
já carrega os campos necessários para essa evolução sem precisar de
retrofit.

## Duas topologias, não confundir

- **Pool de processamento** (`Maquina.modo = compute`): a máquina só roda
  `worker`/`beat`, apontando pro Postgres/Redis/MinIO/Qdrant *compartilhado*
  do nó que hospeda a infra. Um banco lógico só — usa isso pra processar
  mais rápido, sem replicar nada.
- **Réplica** (`Maquina.modo = replica`): a máquina roda sua própria stack
  completa e recebe as mudanças de `Artifact`/`DocumentText`/
  `DocumentFragment` via replicação por log de eventos. Hoje só o lado de
  saída existe (ver "O que ainda não existe" abaixo) — dá pra puxar do nó
  que hospeda a infra, mas o outro lado do sincronismo ainda não foi
  implementado.

## Rede: VPN mesh (Tailscale/Headscale)

Nenhuma porta de infraestrutura (Postgres, Redis, MinIO, Qdrant) deve ficar
exposta em LAN crua ou na internet. Todas as máquinas do cluster entram numa
VPN mesh (Tailscale, ou um servidor Headscale próprio — mesmo protocolo), e
as portas só escutam na interface da VPN.

Isso também dá **descoberta autenticada de graça**: o MagicDNS do Tailscale/
Headscale resolve cada máquina por um nome estável
(`<hostname>.headscale.internal` ou `.ts.net`), sem precisar descobrir/digitar
IP nenhum — é isso que `scripts/entrar_no_cluster.py` (próxima seção) usa
pra achar sozinho qual máquina hospeda a infra.

1. Instale Tailscale (ou junte-se ao Headscale) em todas as máquinas e
   confirme que elas se enxergam (`tailscale status`).
2. Na máquina que vai hospedar a infra compartilhada, defina
   `CLUSTER_VPN_BIND_IP` no `.env` com o IP dela na VPN.
3. Suba essa máquina com o overlay de cluster:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.no-infraestrutura.yml up -d
   ```
   **Sem `CLUSTER_VPN_BIND_IP` definido, não suba este overlay** — o Docker
   publicaria as portas em todas as interfaces, não só na VPN.
4. Defina `CLUSTER_JOIN_SECRET` no `.env` dessa mesma máquina (um segredo
   qualquer, gerado uma vez) — é o que autoriza uma máquina nova a se
   registrar sozinha na próxima seção.
5. Defina `CLUSTER_LOCAL_APELIDO` no `.env` dessa máquina (ex.:
   `CLUSTER_LOCAL_APELIDO=hp-omen`) se ela também roda Ollama local e você
   quer que ela mesma entre no roteamento de LLM. **Não precisa rodar
   `registrar_maquina` contra ela mesma** — no primeiro heartbeat (até 30s
   depois de subir), ela se autorregistra sozinha como `Maquina` com
   `hospeda_infra_compartilhada=True`, contanto que exista exatamente uma
   `Organization` cadastrada (com mais de uma, é ambíguo demais adivinhar a
   dona, e aí sim precisa de `CLUSTER_MACHINE_ID` explícito via
   `registrar_maquina`).

## Adicionando uma máquina (automático)

Na máquina nova (repo clonado, Python 3, já na VPN):

```bash
python3 scripts/entrar_no_cluster.py --secret <CLUSTER_JOIN_SECRET> \
  --organizacao <slug-da-organizacao> --modo compute
```

O script (`scripts/entrar_no_cluster.py`, só biblioteca padrão — não precisa
instalar nada):

1. Pergunta pro `tailscale status --json` quais peers existem.
2. Pergunta a cada peer online `GET /cluster/api/v1/status/` até achar o que
   responde `"hospeda_infra_compartilhada": true` — a maioria recusa a
   conexão (máquinas `compute` só rodam `worker`, sem porta 8000) ou nem
   roda este projeto; isso é esperado, não erro.
3. Se autorregistra no nó de infraestrutura (`POST /cluster/api/v1/join/`,
   autenticado por `CLUSTER_JOIN_SECRET` — não precisa de ninguém rodar nada
   lá).
4. Escreve o `.env` desta máquina (a partir de `.env.example`) já com
   `POSTGRES_HOST`/`REDIS_URL`/`MINIO_ENDPOINT`/`QDRANT_HOST` apontando pro
   nome DNS estável do nó de infraestrutura, e
   `CLUSTER_MACHINE_ID`/`CLUSTER_MACHINE_TOKEN`.
5. Sobe `docker compose -f docker-compose.worker-node.yml up -d --build`.

**Ollama**: se esta máquina também roda Ollama local e você quer que ela
entre no roteamento de LLM (próxima seção), passe `--ollama-endpoint
http://localhost:11434`. Sem isso, `estruturar_llm_manual`/`comparar_llm`
continuam funcionando, mas só contra o `OLLAMA_HOST` local (sem cluster).

**Se nenhum peer responder que hospeda a infra**: o script erra e não tenta
virar dono da infra sozinho de propósito — configurar essa máquina continua
manual (passos 1-4 acima). Eleição automática só faria sentido depois que
existir replicação real do Postgres/Qdrant/MinIO entre máquinas (ver "O que
ainda não existe"); sem isso, "eleger" outra máquina só trocaria de banco
vazio, não resolveria nada.

### Registro manual (alternativa)

Sem `CLUSTER_JOIN_SECRET` configurado, ou pra controlar `--dono` /
`--hostname` explicitamente, o caminho antigo continua funcionando — rodado
à mão no nó que hospeda a infra:

```bash
docker compose exec portal python manage.py registrar_maquina \
  --apelido notebook-trabalho --organizacao <slug> --modo compute
```

Imprime `CLUSTER_MACHINE_ID`/`CLUSTER_MACHINE_TOKEN` uma única vez — depois
disso, escreva o `.env` da máquina nova à mão (mesmas chaves que o script
automático escreveria, ver acima) e suba com
`docker compose -f docker-compose.worker-node.yml up --build`.

## Roteamento de LLM entre máquinas com Ollama

Cada máquina com `ollama_endpoint` preenchido reporta, no próprio heartbeat
de 30s, quais modelos tem instalados (`apps/cluster/tasks.py`, via
`ollama_client.listar_modelos()`) — vira uma linha em `MaquinaModeloOllama`
por (máquina, modelo). Toda chamada real ao Ollama através dessas máquinas
também alimenta `tokens_por_segundo_medio` daquele par — Ollama já devolve
`eval_count`/`eval_duration` em toda resposta, então isso é aprendido de
graça, sem rodar nenhum benchmark sintético separado. Isto vale igual pra
toda `Maquina`, incluindo a que hospeda a infra compartilhada — ela não tem
prioridade nenhuma, compete pelo mesmo critério que qualquer outra.

`apps/cluster/llm_router.py::escolher_execucao(modelo)` usa esses dois sinais
pra decidir onde mandar a próxima chamada daquele modelo: máquinas nunca
testadas entram primeiro (toda máquina nova é experimentada pelo menos uma
vez), depois vence a de maior tokens/segundo observado.
`llm_common.py::gerar_texto` já chama o roteador sozinho quando
`provider == "ollama"` — sem nenhuma máquina registrada com aquele modelo
(instalação de máquina única, caso comum hoje), cai no `OLLAMA_HOST` local
de sempre, sem mudança de comportamento.

### Gateway compatível com OpenAI

`POST /v1/chat/completions` (`apps/cluster/gateway.py`) expõe o roteador
acima como um endpoint OpenAI-compatível — configure `base_url=".../v1"` em
qualquer ferramenta que fale esse protocolo (Langflow etc.) e ela usa o
cluster inteiro sem saber que há mais de uma máquina por trás.

- Autenticado por `Authorization: Bearer <LLM_GATEWAY_TOKEN>` — vazio (padrão)
  desliga o gateway inteiro (404).
- Só fala com Ollama das máquinas do cluster — nunca proxeia pra Anthropic
  nem pra qualquer provider externo. Classificação de dados
  (`policy_engine.py`, nunca tocado) decide *local vs. externo*; isso aqui é
  só *qual máquina local*, não cruza esse limite.
- Sem streaming: todo pedido vira uma chamada única (`stream: false`) ao
  Ollama.

## Observando o cluster

- `MaquinaStatus` (admin do Django) mostra CPU/RAM/disco de cada máquina,
  atualizado a cada 30s via o evento `maquina.heartbeat` (visível também no
  painel de eventos ao vivo, `/eventos/`).
- `manage.py reconstruir_status_maquinas` reconstrói `MaquinaStatus` do zero
  a partir do log de eventos, caso a projeção divirja por algum motivo.

## Testando o lado de saída da replicação (sem precisar de uma 2ª máquina)

O endpoint de replicação já pode ser testado sozinho, via curl, usando o
token de uma máquina `modo=replica` registrada:

```bash
curl -H "X-Machine-Token: <token>" \
  "http://<no-de-infra-na-vpn>/cluster/api/v1/replicacao/eventos/?desde=0"
```

Cada `Artifact`/`DocumentText`/`DocumentFragment` salvo gera uma entrada em
`EventoReplicacao` (admin do Django), que aparece nessa resposta.

## O que ainda não existe (próximos passos, não implementados)

- **Filas nomeadas por perfil de máquina** (`CELERY_TASK_ROUTES`): hoje toda
  máquina drena a mesma fila padrão. Rotear tarefas pesadas (extração,
  embeddings) pra máquinas com mais RAM é evolução futura — só vale a pena
  desenhar com uma 2ª máquina real de perfil diferente pra testar contra (o
  roteamento de LLM/Ollama, esse já existe — ver seção acima).
- **Lado de recepção da replicação**: puxar `EventoReplicacao` de um peer e
  aplicar localmente (`Artifact.origem_maquina`, download do MHTML via peer,
  `manage.py ressincronizar_maquina`). O lado de saída (este documento) já
  está pronto; falta o lado que recebe.
- **Fase 2 — replicação consciente de organização**: `eventos_para_peer`
  (`apps/cluster/replicacao.py`) é o único lugar que precisa mudar quando
  isso chegar — hoje devolve tudo, sem filtro nenhum.
- **Detecção de GPU/VRAM**: o roteador de LLM decide só com CPU/RAM/disco
  (`MaquinaStatus`) — suficiente hoje, mas quando aparecer uma máquina com
  GPU de fato, é extensão direta do mesmo padrão de heartbeat.
- **Benchmark sintético variando `num_thread`**: o aprendizado atual mede
  velocidade real por *máquina* (chamada de verdade, sem custo extra); testar
  vários valores de `num_thread` na mesma máquina pra achar o ótimo fica pra
  quando isso se provar necessário.
- **Streaming no gateway OpenAI-compatível.**
- **Failover automático de qual máquina hospeda a infra**: descoberta é
  automática (seção acima), mas se essa máquina cair, configurar outra pra
  assumir continua manual — exige replicação real do Postgres/Qdrant/MinIO
  primeiro (item "lado de recepção da replicação" acima), senão a "nova"
  eleita teria banco vazio.
