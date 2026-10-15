# Cluster: roteamento de LLM e heartbeat entre máquinas

Este documento descreve o que está **implementado hoje** em `apps/cluster`.

**Cada instância é completa:** stack própria (Postgres, Redis, Garage, Qdrant, portal, orchestrator, worker, beat) e dados próprios. Não existe mais a topologia em que várias máquinas compartilham um único banco (o antigo modo `compute`, com `entrar_no_cluster.py` e os overlays `worker-node`/`no-infraestrutura`, foi removido em 2026-10-04: não era usado). Replicar **dados** entre máquinas — inclusive as suas próprias — é papel da **federação** ([ADR-010](../arquitetura/decisoes/010-federacao-por-log-assinado.md)), não do cluster.

O que o cluster faz:

- **Registro de máquinas** (`Maquina`) e **heartbeat** de recursos e modelos Ollama.
- **Roteamento de LLM**: escolher qual máquina executa a próxima chamada de um modelo, e o **gateway** autenticado pelo qual os peers se chamam (ADR 009).

## Rede: VPN mesh (Tailscale/Headscale)

O gateway de LLM e a API entre máquinas só devem ser alcançáveis pela VPN do dono (Tailscale, ou um Headscale próprio — mesmo protocolo). Nenhuma porta de infraestrutura (Postgres, Redis, Garage, Qdrant) é publicada para fora da própria máquina em produção (ver `docs/deploy.md`).

1. Instale Tailscale (ou junte-se ao Headscale) em todas as máquinas e confirme que elas se enxergam (`tailscale status`).
2. Em cada máquina, defina no `.env` o endpoint do gateway que ela anuncia (`LLM_GATEWAY_ENDPOINT_ANUNCIADO`, IP da VPN) e o `LLM_GATEWAY_TOKEN` do cluster (ver "Chamada entre nós pelo gateway").

## Registrando as máquinas e pares

- **A própria instância** se autorregistra como `Maquina` no primeiro heartbeat (até 30 s depois de subir), na organização do **administrador da instância** (o superusuário mais antigo; o cadastro é aberto e cada pessoa tem a sua organização), com o `did:key` da chave da instância, `confirmado` e `próprio`. Defina `CLUSTER_LOCAL_APELIDO` no `.env` (ex.: `antares`), estável entre reinícios. Com mais de uma organização é ambíguo demais adivinhar a dona: use `CLUSTER_MACHINE_ID`.
- **As outras instâncias** entram como **pares**, por **convite**, em `/cluster/pares/` (ADR 011). Não há mais cadastro manual (`registrar_maquina` foi removido) nem token por máquina.

### Enrolamento por convite

Cada instância precisa de `FEDERACAO_ENDPOINT_ANUNCIADO` no `.env` (o IP da VPN e a porta do portal, ex.: `http://100.64.0.5:8000`).

1. O administrador de **A** abre `/cluster/pares/`, escolhe o tipo que A dará ao par (padrão `terceiro`) e cria o **convite**. O código `ia1.…` **só aparece nessa tela**; vale 24 h e serve uma única vez.
2. O administrador de **B** cola o código e vê o nome, o endereço e a **impressão digital** de A.
3. Os dois administradores comparam a impressão digital **por fora do canal** (voz, mensagem, pessoalmente). Se for diferente, **não aceite**.
4. B aceita: chama A com uma requisição **assinada pela chave de B**; A confere a assinatura e consome o token. Cada lado fica com o outro como par **`pendente`**.
5. Em cada lado, o administrador clica **Confirmar** depois de conferir a impressão digital do outro. **Só então** o par vira `confirmado` — e só pares confirmados entram no roteamento de LLM e (nos próximos marcos) no canal de controle.

**"Próprio" dá poder.** O tipo é um rótulo que **cada lado** atribui ao outro; o sistema não prova que a outra instância é sua. Um par `próprio` poderá instalar e remover modelos do Ollama desta instância. Por isso marcar como próprio exige uma confirmação explícita e toda mudança de tipo fica no `AuditLog`. **Revogar** corta o canal; não recolhe o que já foi copiado. Só dono e administrador da organização convidam, aceitam, confirmam, mudam o tipo ou revogam; qualquer membro vê a lista.

O endereço de um par é validado (anti-SSRF): só `http`/`https`, sem usuário/senha, nunca link-local (`169.254.0.0/16`, onde ficam os metadados de nuvem); HTTP só em rede privada/VPN (RFC 1918, loopback, CGNAT `100.64.0.0/10` do Tailscale, `*.ts.net`, `.local` etc.), e HTTPS fora delas. Limite conhecido: o nome é checado no cadastro, não na hora de conectar (DNS rebinding não é pego).

### Enrolamento por linha de comando (automação)

Os mesmos serviços da tela têm comandos `manage.py` (`docker compose exec -T portal python manage.py …`), usados pelo Ansible (`tasks/pareamento.yml` da role `inteligencia_aberta`, em `rede_papagaio`):

| Comando | O que faz |
|---|---|
| `par_identidade [--json]` | DID, **impressão digital** e endereço anunciado desta instância |
| `par_convite [--tipo proprio --confirmo-proprio] [--json]` | cria o convite; o código sai em stdout, uma vez |
| `par_aceitar <código> --impressao-esperada … [--tipo …]` | aceita **só se** o DID do código tiver a impressão esperada |
| `par_confirmar --did … --impressao-esperada …` | confirma o par **só se** a impressão bater |
| `par_listar [--json]` | pares e estados |

Agem como o dono da organização da instância (`--usuario` troca). `par_aceitar` e `par_confirmar` são idempotentes (`mudou: false`). A conferência que a pessoa faria por voz é feita pelo **SSH do Ansible**, que lê a impressão de cada host; sem `--impressao-esperada` não há aceite nem confirmação. `proprio` exige `--confirmo-proprio` e continua auditado. Se uma instância for zerada (chave nova), revogue o par antigo em `/cluster/pares/` antes de repareá-la.

**Para a automação (Ansible):** rode o pareamento com **todos** os hosts do cluster no mesmo play (um `--limit` com um host só não pareia nada, e um host novo só se pareia com quem está no play). A role pula o pareamento com menos de 2 hosts e pode ser desligada com `-e inteligencia_aberta_parear=false`. Se só um lado conhece o outro (por exemplo, um host foi zerado), a role **para** e pede para revogar o lado que sobrou, em vez de adivinhar. Códigos de saída: `0` ok (inclui "sem mudança"); diferente de `0` com a mensagem em stderr (impressão divergente, sem administrador, sem `FEDERACAO_ENDPOINT_ANUNCIADO`, convite expirado ou já usado). Ainda não foi exercitado entre máquinas reais.

## Testar os modelos pelo chat do Agora

Em `/agora/`, adicione o componente **"Conversar com um modelo"** (`ia-llm-chat`, categoria Inteligência). Ele lista os modelos que estão instalados nas máquinas pareadas e **online**, deixa escolher a máquina (ou "Automático") e mostra quem respondeu e a velocidade. Detalhes, limites e privacidade em [`agora.md`](../componentes/interfaces/agora.md) e na ADR 020. Para um modelo recém-instalado aparecer, espere o heartbeat (até 30 s). O chat só chama o Ollama de máquinas do cluster; com o `LLM_GATEWAY_TOKEN` (a automação o gera), chamadas a máquinas de **outros hosts** passam pelo gateway autenticado do par em vez de falar direto com o Ollama dele.

## Ver e controlar os modelos do Ollama (`/cluster/`)

Cada máquina da tela tem a seção **Modelos do Ollama**: o que está instalado (tamanho, família, quantização, o que está **carregado na memória**, tokens/s), um campo para **instalar** (com sugestões de um catálogo curado e qualquer `nome:tag`), **remover**, e as **operações** em andamento com barra de progresso, **cancelar** e **tentar de novo**. Funciona igual com o Ollama **nativo** (Macs) ou em **container**: o portal só fala HTTP com o Ollama de **dentro da própria instância**; para uma máquina remota o comando vai pelo canal assinado.

- **Quem pode:** ver, qualquer membro da organização; instalar, remover e cancelar, só **dono ou administrador**. Numa máquina remota, só em **par próprio confirmado**.
- **Máquina offline:** os botões ficam **desabilitados**, com selo **Offline** e o motivo no tooltip, e um comando mesmo assim **falha na hora** (nada fica na fila). "Ollama indisponível" e "sem Ollama" têm selos próprios.
- **Pares:** cada instância **puxa** o estado dos pares próprios a cada 30 s e guarda o **último estado conhecido** ("dados de HH:MM" / "último estado conhecido"). É isso que também alimenta o roteador de LLM com os pares.
- **Uma operação por modelo:** instalar duas vezes, ou remover durante a instalação, é recusado (409). No máximo 3 operações ativas por máquina. A instalação roda numa **fila própria** (`ollama_admin`, serviço `worker-ollama`), sem timeout total; se a conexão cai no meio, o Ollama reaproveita o que já baixou — é só tentar de novo.
- **Espaço em disco:** a API do Ollama não informa. O compose monta o volume dos modelos **somente leitura** nos workers para medir; sem isso o espaço é "desconhecido" e **nada é bloqueado**. Com o dado, recusa instalar quando sobraria menos que a reserva (`OLLAMA_RESERVA_DISCO_GB`, 10 GB) e aborta se o tamanho real não couber. No Ollama **nativo**, aponte `OLLAMA_DATA_DIR_HOST` para o `~/.ollama`.
- **`OLLAMA_MODELOS` é só a semente do primeiro `up`:** remover um modelo que está nessa lista faz o `ollama-pull` trazê-lo de volta no próximo `up` (a tela avisa).
- **Catálogo:** é uma lista curada (`apps/cluster/catalogo_modelos.py`), com tamanhos aproximados, e **pode estar desatualizada**. Qualquer `nome:tag` digitado vale; se não existir, o Ollama responde com erro. Só o registry oficial é aceito por padrão.

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
- A resposta traz `x_ollama.eval_duration_ns` (extensão fora da API OpenAI),
  usada por quem encaminha o pedido para aprender tokens/segundo da máquina.

### Chamada entre nós pelo gateway (ADR 009)

O roteador não fala mais direto com o Ollama de um peer quando ele anuncia
gateway. Cada nó define, no `.env`:

```bash
LLM_GATEWAY_TOKEN=<o mesmo em todos os nós do cluster>
LLM_GATEWAY_ENDPOINT_ANUNCIADO=http://<ip-vpn>:8000   # base, sem /v1
```

`registrar_maquina` aceita `--gateway-endpoint`; o autorregistro local usa a
variável acima. Com isso o
Ollama do peer pode ficar em `127.0.0.1` (`OLLAMA_BIND_ADDR`), sem
autenticação exposta na VPN. Sem token local ou sem gateway anunciado, o
roteador volta a chamar o `ollama_endpoint` direto. O pedido encaminhado leva
`X-Cluster-Encaminhado` e o gateway de destino o executa localmente, sem
reencaminhar. `gateway_endpoint` só é gravado ao criar a `Maquina`; em uma já
registrada, edite no admin.

## Observando o cluster

- `MaquinaStatus` (admin do Django) mostra CPU/RAM/disco de cada máquina,
  atualizado a cada 30s via o evento `maquina.heartbeat` (visível também no
  painel de eventos ao vivo, `/eventos/`).
- `manage.py reconstruir_status_maquinas` reconstrói `MaquinaStatus` do zero
  a partir do log de eventos, caso a projeção divirja por algum motivo.

## Replicação de dados

Não existe mais replicação pelo cluster. O `EventoReplicacao`, o endpoint `/cluster/api/v1/replicacao/eventos/` e o token por máquina foram **removidos** (ADR 011): as máquinas do mesmo dono vão replicar pelo mecanismo da federação.

## O que ainda não existe

- **Detecção de GPU/VRAM**: o roteador de LLM decide só com CPU/RAM/disco (`MaquinaStatus`) — extensão direta do mesmo padrão de heartbeat.
- **Benchmark sintético variando `num_thread`**: o aprendizado atual mede velocidade real por *máquina* (chamada de verdade, sem custo extra).
- **Streaming no gateway OpenAI-compatível.**
- **Teste real entre duas máquinas** pelo gateway (só testado com mocks).
- **Máquina muito fraca** (ex.: VPS de 1 GB): talvez só borda/proxy; ainda em aberto (ADR 009).
