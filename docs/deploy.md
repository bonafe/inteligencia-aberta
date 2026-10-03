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

O passo `bootstrap` roda a cada `up`: aplica migrations, garante os buckets do Garage (S3) e cria o superusuário inicial. É idempotente e o portal, o worker e o beat só sobem depois dele terminar com sucesso. Não há passo manual.

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

`ANTHROPIC_API_KEY` é **opcional**: a stack sobe sem ela.

**Atenção:** `POSTGRES_PASSWORD` só vale na criação dos dados. Trocá-lo depois num `DATA_DIR` existente não altera a senha já gravada. Os formatos de `S3_ACCESS_KEY`, `S3_SECRET_KEY` e `GARAGE_RPC_SECRET` não são livres (hex de tamanho fixo): o `garage` recusa subir com outro formato, e o `token_urlsafe` indicado acima **não serve** para eles.

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
| `GARAGE_CAPACITY` | `100GB` | Capacidade declarada do nó único; não reserva disco |
| `CORS_ALLOWED_ORIGINS` | vazio em prod | Origens web do orchestrator; a extensão não precisa |
| `DJANGO_SUPERUSER_USERNAME` / `_EMAIL` | vazio | Dono inicial; sem as três, nenhum é criado |

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

## Relação com o cluster multi-máquina

Este documento trata **uma instância completa por host** (stack inteira, dados próprios). O projeto também tem um cluster multi-máquina (ADR-006, `docs/operacao/escala-multimaquina.md`) com dois arranjos: *pool de processamento* (`docker-compose.worker-node.yml`, só worker/beat apontando para a infraestrutura de outro nó) e *réplica*. Os dois são camadas por cima da instância descrita aqui, não alternativas a ela.

- `docker-compose.no-infraestrutura.yml` (publica Postgres/Redis/Garage/Qdrant na interface da VPN, `CLUSTER_VPN_BIND_IP`) é um overlay opcional para o nó que hospeda a infra; combina com `docker-compose.prod.yml` (`-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.no-infraestrutura.yml`).
- Variáveis `CLUSTER_*`, `CELERY_QUEUES`, `LLM_GATEWAY_TOKEN` e `OLLAMA_*` do `.env.example` pertencem ao cluster e são **opcionais**: vazias, o recurso fica desligado. `CLUSTER_JOIN_SECRET` e `LLM_GATEWAY_TOKEN`, quando usados, são segredos (aleatórios, por cluster); a validação de segredos de produção não os exige.
- Um nó `worker-node` em produção roda `config.settings.production` e, por isso, também precisa dos segredos obrigatórios no seu `.env` (os mesmos valores do nó que hospeda a infra, não novos).

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
