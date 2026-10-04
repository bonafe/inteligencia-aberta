# ADR 009 — Ollama como capacidade do nó

**Status:** aceito — 2026-10-03

## Contexto

O Ollama rodava nativo no host e era alcançado por `host.docker.internal:11434`. Queremos que o inteligencia-aberta o suba e gerencie, mas as máquinas da rede são muito diferentes: um desktop de 16 GB sem GPU (antares), um VPS de 1 GB que não comporta LLM (netuno) e MacBooks de 64 GB onde os modelos rodam de verdade. O roteamento entre máquinas já existe (ADR 006, `apps/cluster/`): cada `Maquina` anuncia `ollama_endpoint` e seus modelos no heartbeat, e `llm_router.escolher_execucao` escolhe a mais rápida.

## Decisão

O Ollama é uma **capacidade do nó**, não um serviço obrigatório da stack. Cada máquina escolhe um de três modos (no Ansible, uma variável por host que renderiza o `.env`):

| Modo | `.env` | Quando |
|---|---|---|
| `container` | `COMPOSE_PROFILES=ollama`, `OLLAMA_HOST=http://ollama:11434`, `OLLAMA_MODELOS` | Linux (CPU, ou NVIDIA com `docker-compose.gpu.yml`) |
| `nativo` (padrão) | `OLLAMA_HOST=http://host.docker.internal:11434` | **Macs**: o Docker no macOS não acessa a GPU Metal; em container o Ollama rodaria só em CPU, dentro da VM |
| `nenhum` | `OLLAMA_HOST=` (vazio) | Máquinas sem recurso para LLM; usam os peers |

- **Serviços `ollama` e `ollama-pull`** no `docker-compose.yml`, atrás do profile `ollama`. O `ollama-pull` baixa os modelos de `OLLAMA_MODELOS` e sai (idempotente), no padrão do `bootstrap`. Portal e worker **não** dependem dele: baixar modelo pode levar minutos.
- **Endpoint anunciado ≠ endpoint interno.** `OLLAMA_HOST` é o que esta instância usa, de dentro dos containers. `OLLAMA_ENDPOINT_ANUNCIADO` é o que vai no heartbeat para os peers e precisa ser alcançável pela VPN (`http://ollama:11434` só existe na rede do compose). Sem ele, cai em `OLLAMA_HOST`. O heartbeat lista os modelos pelo endereço local, não pelo anunciado.
- **Porta** publicada em `OLLAMA_BIND_ADDR:OLLAMA_PORTA` (padrão `127.0.0.1:11434`). Para o cluster alcançá-lo, `OLLAMA_BIND_ADDR` = IP da VPN. O Ollama não tem autenticação: nunca `0.0.0.0`.
- O serviço `ollama` **não usa `env_file`**: o servidor lê `OLLAMA_HOST` como endereço de bind, e o valor do `.env` (URL de cliente) o quebraria.

## Consequências

- Netuno não tenta subir LLM; antares pode subir um modelo pequeno em CPU ou só usar o persas; os Macs seguem nativos e anunciam o endpoint da VPN.
- Quem migra do Ollama nativo para o container na mesma máquina precisa mudar `OLLAMA_PORTA` (a 11434 do host já está ocupada) ou parar o nativo.
- Overlay `docker-compose.gpu.yml` não foi testado (a máquina de desenvolvimento não tem `nvidia-smi`).

## Chamada entre nós pelo gateway autenticado

Implementado. Antes, o roteador chamava o `ollama_endpoint` do peer diretamente, o que obrigava a expor um Ollama sem autenticação na VPN. Agora:

- **Anúncio.** Cada máquina anuncia `Maquina.gateway_endpoint` (`LLM_GATEWAY_ENDPOINT_ANUNCIADO`, base sem `/v1`, alcançável pela VPN). O segredo é o `LLM_GATEWAY_TOKEN`, **igual em todos os nós do cluster** e não guardado no banco.
- **Roteamento.** `llm_router.escolher_execucao` preenche `ExecucaoOllama.gateway` quando a máquina escolhida é um peer com gateway **e** esta instância tem `LLM_GATEWAY_TOKEN`. Para a máquina local, ou sem token, nada muda: fala direto com o Ollama. Um peer que só anuncia gateway (sem `ollama_endpoint`) também é candidato — o Ollama dele pode ficar em loopback.
- **Chamada.** `ollama_client._chamar(gateway=...)` faz `POST <gateway>/v1/chat/completions` com `Bearer` e converte a resposta ao formato do Ollama. Sem fallback para o Ollama direto: se o gateway falhar, a chamada falha.
- **Um salto só.** O cliente envia `X-Cluster-Encaminhado`; o gateway que o recebe executa no Ollama local e não consulta o cluster de novo, então gateways não formam laço.
- **Velocidade aprendida.** O gateway devolve `x_ollama.eval_duration_ns` (extensão nossa, fora da API OpenAI); sem ele o tokens/segundo do peer não seria aprendido.
- **Migração.** `ollama_endpoint` continua sendo anunciado e usado quando não há gateway/token. Para fechar o Ollama dos peers: definir `LLM_GATEWAY_TOKEN` (igual em todos) e `LLM_GATEWAY_ENDPOINT_ANUNCIADO` em cada nó, e então pôr `OLLAMA_BIND_ADDR=127.0.0.1`. O `gateway_endpoint` só é gravado quando a `Maquina` é criada; para uma já registrada, editá-lo no admin.

Limites conhecidos: `num_thread`/`num_ctx` ficam a cargo do peer (só `temperature` e `top_p` atravessam o gateway); a mesma chamada gera telemetria no chamador (com a `maquina_id` do peer) e no gateway do peer (finalidade `GATEWAY_EXTERNO`); a aplicação da política de classificação no destino segue em aberto.

## Pendências (fora desta decisão)

- **Registro de providers de LLM** (Ollama interno/externo, Claude, ChatGPT): hoje `provider` é `anthropic|ollama` fixo em `llm_common.py`.
- Viabilidade da stack completa no netuno (1 GB): talvez só Caddy/proxy.
