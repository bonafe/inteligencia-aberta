# Implantação

Uma instância por host, cada uma com seus próprios segredos. Pensado para ser subido por automação (Ansible), mas funciona à mão com os mesmos passos.

## Subir uma instância

```bash
git clone <repo> && cd inteligencia-aberta
cp .env.example .env            # preencher segredos e variáveis de implantação (abaixo)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps   # tudo "healthy"
```

**Sempre com `-f ... -f docker-compose.prod.yml`.** Sem `-f`, o compose carrega o `docker-compose.override.yml` sozinho — que é o de desenvolvimento (runserver, `--reload`, volumes de código, portas abertas).

O passo `bootstrap` roda a cada `up`: aplica migrations, garante os buckets do Garage (S3), cria o superusuário inicial e gera a chave Ed25519 da instância (ver "Chave da instância", abaixo). É idempotente e o portal, o worker e o beat só sobem depois dele terminar com sucesso. Não há passo manual.

Critério de sucesso para a automação: nenhum serviço `unhealthy` e `bootstrap` como `Exited (0)` (é o esperado: roda e sai). `worker` e `beat` não têm healthcheck — basta estarem `Up`. Verificar a aplicação com `/health` (ver o contrato abaixo).

## Contrato para automação (Ansible)

Resumo operacional para quem implanta por código. O restante deste documento explica o porquê de cada item.

**Pré-requisitos no host:** `git`, Docker Engine com o plugin `docker compose` v2.

**Passos, nesta ordem (todos idempotentes):**

1. `git clone` (ou `git pull`) do repositório.
2. Gerar o `.env` a partir de `.env.example` — **cada segredo aleatório e distinto por host** (tabela em "Segredos"). Permissão `0600`. Valores mínimos de produção:
   - `DJANGO_SETTINGS_MODULE=config.settings.production`
   - os 6 segredos, mais `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD`
   - `ALLOWED_HOSTS` e `CSRF_TRUSTED_ORIGINS` (os nomes pelos quais o host é acessado; origens com `https://`)
   - `TLS_MODE` (`proxy` com `tailscale serve`/Caddy na frente; `none` só se HTTP puro for decisão consciente)
   - `BIND_ADDR`, `DATA_DIR`, `INSTANCIA_NOME`, `IA_VERSION` (sugestão: `git rev-parse --short HEAD`)
   - LLM do nó (opcional; ver "LLM por nó e entre nós"): `OLLAMA_HOST`/`COMPOSE_PROFILES`/`OLLAMA_MODELOS` conforme o modo, e, no cluster, `LLM_GATEWAY_TOKEN` + `LLM_GATEWAY_ENDPOINT_ANUNCIADO`
   - host com domínio público: também `IA_DOMINIO` e `ACME_EMAIL`, e `--profile publico` no comando
3. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` (acrescentar `--profile publico` no host público). **Nunca sem os dois `-f`**: sem `-f` o compose carrega o `docker-compose.override.yml`, que é de desenvolvimento.
4. Esperar e verificar (abaixo). Se `bootstrap` falhar (`docker compose ... logs bootstrap`), nada mais sobe — é a causa raiz a investigar.

**Verificação de saúde:**

```bash
# Dentro do host. O header Host é necessário: o Django rejeita (400) um Host que não
# esteja em ALLOWED_HOSTS, e localhost/127.0.0.1/portal são sempre aceitos.
curl -fsS -H 'Host: localhost' http://${BIND_ADDR:-127.0.0.1}:8000/health   # portal
curl -fsS http://${BIND_ADDR:-127.0.0.1}:8001/health                         # orchestrator
```

Ambos devolvem JSON com `"status": "ok"`, `servico`, `instancia` (= `INSTANCIA_NOME`) e `versao` (= `IA_VERSION`). O portal responde `503` se não alcança o banco. Conferir `instancia`/`versao` confirma que a instância certa subiu na versão esperada. O MCP não publica porta; sua saúde aparece em `docker compose ps`.

**Armadilhas:**

- Um segredo vazio ou `CHANGE_ME` faz o serviço recusar a subida, com a lista de variáveis na mensagem de erro.
- `POSTGRES_PASSWORD` só vale na criação dos dados em `DATA_DIR`; trocá-lo depois não altera a senha já gravada. As chaves do Garage (`S3_*`) são reimportadas a cada boot pelo `garage`, mas o `GARAGE_RPC_SECRET` fica gravado no nó. Gere uma vez por host e preserve.
- `DJANGO_SUPERUSER_*` só é lido enquanto não existe nenhum usuário; mudá-lo depois não cria nem altera ninguém.
- `/registro/` fica fechado (403) depois do primeiro usuário — é o comportamento desejado em produção.
- O projeto compose se chama `inteligencia-aberta` (fixo); dois checkouts no mesmo host colidiriam.
- Atualizar de versão é repetir os passos 1 a 4: o `bootstrap` reaplica as migrations e os dados ficam em `DATA_DIR`.

## Segredos

Cada um deve ser aleatório e **único por instância**. Em produção o processo se recusa a subir se algum estiver vazio ou com `CHANGE_ME` (a mensagem de erro lista todos os problemas de uma vez).

| Variável | Para quê |
|---|---|
| `DJANGO_SECRET_KEY` | Sessões, CSRF, assinaturas do Django |
| `JWT_SIGNING_KEY` | Assina (portal) e valida (orchestrator) os JWT da extensão — idêntico nos dois |
| `INTERNAL_API_TOKEN` | Canal serviço-a-serviço (orchestrator/MCP → portal) |
| `MCP_API_TOKEN` | Header `X-Mcp-Token` das ferramentas do MCP |
| `POSTGRES_PASSWORD` | Banco |
| `S3_SECRET_KEY` | Armazenamento de objetos (Garage): 64 hex — `openssl rand -hex 32` |
| `S3_ACCESS_KEY` | Id da chave S3: `GK` + 24 hex — `echo "GK$(openssl rand -hex 12)"` (formato exigido pelo Garage) |
| `GARAGE_RPC_SECRET` | Segredo do RPC do Garage: 64 hex — `openssl rand -hex 32` |
| `DJANGO_SUPERUSER_PASSWORD` | Senha do dono inicial (só é lida no primeiro boot) |

Gerar: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

`ANTHROPIC_API_KEY` é **opcional**: a stack sobe sem ela. É a chave **global** da instância; cada organização pode cadastrar a sua em Admin → *LLM providers* (tipo *Externo*, fornecedor *Anthropic*), e a da organização vence a global. As chaves cadastradas ficam cifradas no banco (Fernet) com `FIELD_ENCRYPTION_KEY`; sem ela, a cifra deriva de `DJANGO_SECRET_KEY`, e **trocar o `DJANGO_SECRET_KEY` depois torna as chaves salvas ilegíveis** (o sistema cai na chave global e registra aviso; é preciso cadastrá-las de novo). Para a automação: gere `FIELD_ENCRYPTION_KEY` uma vez por instância (`python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`), preserve-a e **inclua-a no backup** — sem ela os dados cifrados não se recuperam. Não é exigida pela validação de segredos — mas veja "Chave da instância" abaixo: a chave da federação também depende dela.

**Atenção:** `POSTGRES_PASSWORD` só vale na criação dos dados. Trocá-lo depois num `DATA_DIR` existente não altera a senha já gravada. Os formatos de `S3_ACCESS_KEY`, `S3_SECRET_KEY` e `GARAGE_RPC_SECRET` não são livres (hex de tamanho fixo): o `garage` recusa subir com outro formato, e o `token_urlsafe` indicado acima **não serve** para eles.

### Chave da instância (federação) — `FIELD_ENCRYPTION_KEY` deixa de ser opcional na prática

A primeira execução do `bootstrap` gera o par Ed25519 da instância ([ADR 010](arquitetura/decisoes/010-federacao-por-log-assinado.md)) e guarda a chave privada **cifrada no banco** (tabela `federacao_chave_instancia`) com o mesmo Fernet das chaves de LLM. O `did:key` resultante é a identidade da instância na federação; consulte-o com `docker compose exec portal python manage.py chave_instancia` (só o DID público é exibido).

Por isso, **defina `FIELD_ENCRYPTION_KEY` antes do primeiro `up`** (e preserve-a):

- Sem ela, a cifra deriva de `DJANGO_SECRET_KEY`. Se a chave da instância for criada assim e depois você definir `FIELD_ENCRYPTION_KEY` (ou trocar o `DJANGO_SECRET_KEY`), a privada fica **ilegível** — e hoje não existe comando para recifrá-la ou recriá-la sem perder o DID.
- Diferente das chaves de LLM (que se recadastram), perder a privada da instância significa **perder a identidade**: uma chave nova tem outro `did:key`, e quem já confiava no antigo precisa enrolar de novo. Nenhum par existe hoje, mas isso vale a partir do primeiro intercâmbio.
- **Backup:** `FIELD_ENCRYPTION_KEY` **e** o banco (ao menos `federacao_chave_instancia`). Um sem o outro não recupera a chave.
- Em instância que já existia antes desta mudança, o `up` (via `bootstrap`) cria a chave na primeira vez; confira com o comando acima.

### Pares (outras instâncias) — enrolamento e rede

Para uma instância conhecer outras ([ADR 011](arquitetura/decisoes/011-politica-de-replicacao.md)), cada uma define no `.env` `FEDERACAO_ENDPOINT_ANUNCIADO` (a base por onde os pares a alcançam, em geral o IP da VPN e a porta do portal, como `http://100.64.0.5:8000`) e o enrolamento é feito em `/cluster/pares/`, por convite, com **conferência da impressão digital** pelos dois administradores (ver `docs/operacao/escala-multimaquina.md`).

- O **Caddy bloqueia `/federacao/*`** (resposta 404): o canal entre instâncias é de VPN/LAN e viaja direto na porta do portal (`BIND_ADDR:8000`), nunca pela internet. Outros proxies na frente do portal devem bloquear esse prefixo se a URL for pública.
- As requisições entre instâncias são **assinadas** e tolerância de relógio é de ±60 s (`FEDERACAO_JANELA_RELOGIO_S`): mantenha **NTP** em todas as máquinas. Os nonces anti-replay ficam no Redis (banco 2, `CACHE_URL`).
- HTTP puro só em rede privada/VPN; fora dela o par precisa usar HTTPS (`FEDERACAO_PERMITE_HTTP_PUBLICO=true` desfaz a exigência; desaconselhado).

## Variáveis de implantação

| Variável | Padrão | Função |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.development` | **Em produção: `config.settings.production`** |
| `INSTANCIA_NOME`, `IA_VERSION` | vazio | Identidade e versão (ex.: git sha), expostas em `/health` |
| `ALLOWED_HOSTS` | vazio | Nomes pelos quais o portal é acessado, separados por vírgula |
| `CSRF_TRUSTED_ORIGINS` | vazio | Origens com esquema (`https://…`); necessário para login/registro atrás de proxy |
| `TLS_MODE` | `proxy` | `proxy`: HTTPS terminado na frente (redirect, cookies seguros). `none`: HTTP puro |
| `SECURE_HSTS_SECONDS` | `0` | HSTS é pegajoso no navegador; ligar só com HTTPS estável |
| `REGISTRO_ABERTO` | `false` (prod) | Cadastro público; em produção fecha após o primeiro usuário |
| `BIND_ADDR` | `127.0.0.1` | Interface onde 8000/8001 são publicadas |
| `DATA_DIR` | `./data` | Onde ficam postgres, garage, qdrant, redis (pode ser outro disco) |
| `GARAGE_VERSION` | `v2.4.1` | Versão do Garage embutida na imagem de `infra/garage/` (build-arg) |
| `COMPOSE_PROFILES` | vazio | `ollama` sobe o Ollama em container (modo `container`, ADR 009) |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama que esta instância usa: nativo (padrão), `http://ollama:11434` (container) ou vazio (sem LLM local) |
| `OLLAMA_ENDPOINT_ANUNCIADO` | = `OLLAMA_HOST` | Endpoint informado aos peers no heartbeat; precisa ser alcançável pela VPN |
| `LLM_GATEWAY_TOKEN` / `LLM_GATEWAY_ENDPOINT_ANUNCIADO` | vazio | Gateway LLM entre nós (token por cluster, endpoint por host) — ver "LLM por nó e entre nós" |
| `OLLAMA_MODELOS` | vazio | Modelos baixados no `up` pelo `ollama-pull` (separados por espaço) |
| `OLLAMA_BIND_ADDR` / `OLLAMA_PORTA` | `127.0.0.1` / `11434` | Onde o container publica o Ollama; IP da VPN para o cluster alcançá-lo |
| `GARAGE_CAPACITY` | `100GB` | Capacidade declarada do nó único; não reserva disco |
| `CORS_ALLOWED_ORIGINS` | vazio em prod | Origens web do orchestrator; a extensão não precisa |
| `DJANGO_SUPERUSER_USERNAME` / `_EMAIL` | vazio | Dono inicial; sem as três, nenhum é criado |

## LLM por nó e entre nós (Ollama e gateway)

Tudo aqui é **opcional**: sem nada definido, a instância usa o Ollama nativo do host (`OLLAMA_HOST` padrão) e não participa de roteamento entre máquinas. Decisões em `docs/arquitetura/decisoes/009-ollama-como-capacidade-do-no.md`.

### 1. Modo do Ollama — por host

Cada host escolhe **um** modo (uma variável de inventário por host, que renderiza o `.env`):

| Modo | `.env` | Quando |
|---|---|---|
| `container` | `COMPOSE_PROFILES=ollama`, `OLLAMA_HOST=http://ollama:11434`, `OLLAMA_MODELOS="<modelos separados por espaço>"` | Linux (CPU; NVIDIA com `docker-compose.gpu.yml`, não testado) |
| `nativo` (padrão) | `OLLAMA_HOST=http://host.docker.internal:11434` | macOS: o Docker não acessa a GPU Metal; o Ollama roda no host, fora do compose |
| `nenhum` | `OLLAMA_HOST=` (vazio) | VPS pequena sem recurso para LLM; usa os peers |

- `OLLAMA_MODELOS` só vale no modo `container`: o `ollama-pull` baixa os modelos no `up` e sai (idempotente). Portal e worker não esperam por ele — baixar leva minutos.
- No modo `container`, a porta do Ollama é publicada em `OLLAMA_BIND_ADDR:OLLAMA_PORTA` (padrão `127.0.0.1:11434`). Se o Ollama nativo do host já ocupa a 11434, mude `OLLAMA_PORTA` ou pare o nativo. **Com o gateway (abaixo), mantenha `127.0.0.1`**; só use o IP da VPN se os peers ainda chamarem o Ollama direto. O Ollama não tem autenticação: nunca `0.0.0.0`.
- Não coloque `OLLAMA_HOST` no `env_file` do serviço `ollama`: lá ele é endereço de bind, e o valor do `.env` (URL de cliente) o quebra. O compose já trata isso.

### 2. Gateway entre nós — token por cluster, endpoint por host

Para os peers usarem o LLM deste nó **sem** expor o Ollama na VPN, cada nó anuncia o seu gateway (`POST /v1/chat/completions`, servido pelo portal na 8000):

| Variável | Escopo | Valor |
|---|---|---|
| `LLM_GATEWAY_TOKEN` | **por cluster** — idêntico em todos os nós | Segredo aleatório (`token_urlsafe(32)`), gerado **uma vez** pela automação e distribuído. É a exceção à regra "um segredo distinto por host". Vazio = gateway desligado (404) e este nó não usa o gateway dos peers |
| `LLM_GATEWAY_ENDPOINT_ANUNCIADO` | **por host** | Base sem `/v1`, alcançável pelos peers na VPN: `http://<ip-vpn-do-host>:8000`. Vazio = não anuncia |
| `BIND_ADDR` | **por host** | Tem de ser o **IP da VPN** (ou uma interface que os peers alcancem) para a porta 8000 ser acessível; com o padrão `127.0.0.1` o gateway só responde localmente |
| `OLLAMA_ENDPOINT_ANUNCIADO` | por host | Só necessário enquanto algum peer ainda chamar o Ollama direto (sem gateway/token). Alcançável pela VPN; vazio = usa `OLLAMA_HOST` |

Regras de decisão para a automação:

- Um nó só **chama** o gateway de um peer se tiver `LLM_GATEWAY_TOKEN` (e o peer anunciar `gateway_endpoint`); sem isso, o roteador cai no `ollama_endpoint` direto. Para fechar o Ollama dos peers: defina o token (igual em todos) e `LLM_GATEWAY_ENDPOINT_ANUNCIADO` em **todos** os nós, depois deixe `OLLAMA_BIND_ADDR=127.0.0.1`.
- A porta 8000 em `BIND_ADDR` serve também o portal. No host com Caddy (`--profile publico`), o `infra/caddy/Caddyfile` responde `404` para `/v1/*`: o gateway **não** é publicado na internet, só fica acessível a quem alcança `BIND_ADDR:8000` (a VPN) — e ainda exige o token. Isso não vale para outros proxies: com `tailscale serve` ou outro na frente do portal, bloqueie `/v1/*` se a URL for pública.
- `gateway_endpoint` (e `ollama_endpoint`) da `Maquina` é gravado **só quando ela é criada**: autorregistro local (usa `LLM_GATEWAY_ENDPOINT_ANUNCIADO`). Mudar a variável depois **não** atualiza uma `Maquina` existente — edite no admin do portal.
- O autorregistro local só funciona com **uma** organização; com várias, defina `CLUSTER_MACHINE_ID` (ver `docs/operacao/escala-multimaquina.md`).
- Validação: `curl -fsS -X POST -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{"model":"<modelo>","messages":[{"role":"user","content":"oi"}]}' http://<ip-vpn>:8000/v1/chat/completions` deve devolver JSON no formato OpenAI; sem o header, `401`; sem `LLM_GATEWAY_TOKEN` no nó, `404`. Este comando **não foi testado entre máquinas reais**.

## Portas, rede e HTTPS

Em produção só o **portal (8000)** e o **orchestrator (8001)** publicam porta, e só em `BIND_ADDR`. MCP, Postgres, Redis, Qdrant e Garage ficam na rede interna do compose.

**Host só na tailnet:** manter `BIND_ADDR=127.0.0.1` e terminar o TLS com o Tailscale (comandos **não testados** — conferir a sintaxe na versão instalada de `tailscale serve`):

```bash
tailscale serve --bg --https=443 --set-path=/api/v1/capture http://127.0.0.1:8001/api/v1/capture
tailscale serve --bg --https=443 http://127.0.0.1:8000
```

e `ALLOWED_HOSTS=<host>.<tailnet>.ts.net`, `CSRF_TRUSTED_ORIGINS=https://<host>.<tailnet>.ts.net`, `TLS_MODE=proxy`. (A rota de captura precisa ir para o orchestrator, não para o portal.)

**Host com IP público e domínio:** Caddy no próprio compose, pelo profile `publico`:

```bash
# .env: IA_DOMINIO=ia.exemplo.com.br  ACME_EMAIL=voce@exemplo.com.br
#       ALLOWED_HOSTS=ia.exemplo.com.br  CSRF_TRUSTED_ORIGINS=https://ia.exemplo.com.br
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile publico up -d --build
```

O Caddy obtém o certificado (Let's Encrypt; portas 80/443 abertas para a internet) e publica **apenas** o portal e `/api/v1/capture/*`. Os canais serviço-a-serviço (`/artifacts/api/v1/artefatos/`, `/eventos/api/v1/ingest/`) são bloqueados no proxy com 404. Ver `infra/caddy/Caddyfile`.

## Relação com o cluster

Este documento trata **uma instância completa por host** (stack inteira, dados próprios) — é o único arranjo suportado. O `apps/cluster` (ADR-006, ADR-009, `docs/operacao/escala-multimaquina.md`) é só uma camada por cima: heartbeat e roteamento de LLM entre as máquinas. Replicar **dados** entre instâncias é a federação (ADR-010, ainda em construção).

- Variáveis `CLUSTER_*`, `LLM_GATEWAY_*` e `OLLAMA_*` do `.env.example` pertencem ao cluster e são **opcionais**: vazias, o recurso fica desligado. `LLM_GATEWAY_TOKEN`, quando usado, é segredo (aleatório, **por cluster** — igual nos nós que se falam, ao contrário dos segredos da instância); a validação de segredos de produção não o exige.
- A topologia em que várias máquinas compartilham um banco (modo `compute`, `docker-compose.worker-node.yml`, `docker-compose.no-infraestrutura.yml`, `CLUSTER_VPN_BIND_IP`, `CLUSTER_JOIN_SECRET`) foi **removida**; não há mais nada a configurar para isso.

## Volumes

Tudo em `${DATA_DIR}`: `postgres/`, `garage/` (`meta/` e `data/`), `qdrant/`, `redis/`, `caddy/` (certificados), `fastembed-cache/`. Backup e restore ainda não têm script; até lá, parar a stack e copiar o diretório (`docker compose ... stop`).

## Cadastro de usuários

O superusuário nasce do bootstrap (`DJANGO_SUPERUSER_*`). Depois disso `/registro/` responde 403 e novos usuários são criados pelo admin (`/admin/`). Com `REGISTRO_ABERTO=true` o cadastro volta a ser livre.

Sem as variáveis `DJANGO_SUPERUSER_*`, o primeiro cadastro em `/registro/` vira o dono — uma janela de corrida numa instância exposta. Em produção, sempre defini-las.

## Extensão do Chrome

No popup, campo **Instância (URL)**: `https://ia.exemplo.com.br`. Vazio = ambiente local (`localhost`). O Chrome pede permissão para o domínio no login.

## Atualizar de versão

```bash
git pull
# opcional: IA_VERSION=$(git rev-parse --short HEAD) no .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

O `bootstrap` reaplica as migrations. Os serviços cujas imagens mudaram são recriados; os dados ficam em `DATA_DIR`.

## Ainda não coberto

Backup/restore automatizado, imagens publicadas em registry (hoje cada host faz `build`), storage S3 e Postgres externos, reserva de GPU, rate limit na borda.
