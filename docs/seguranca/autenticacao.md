# Segurança: Autenticação e Controle de Acesso

A autenticação usa **cinco mecanismos**, cada um na fronteira em que é adequado. Não há um único esquema para tudo — um cliente de browser, um cliente externo com identidade de usuário, e uma chamada serviço-a-serviço têm necessidades diferentes.

## As cinco camadas

| Fronteira | Cliente | Mecanismo | Onde é validado |
|---|---|---|---|
| Páginas web do portal | Navegador humano | **Sessão Django** (cookie) | `LoginRequiredMiddleware` (allowlist) |
| API de captura/investigação | Extensão Chrome (usuário) | **JWT** (HS256) | Portal emite; orchestrator valida |
| API interna de criação de artefato | Orchestrator → Portal | **Token de serviço** (`X-Internal-Token`) | `ArtefatoCreateAPIView` |
| Ferramentas do MCP | Orchestrator (futuro) / testes manuais via Swagger | **Token de ferramenta** (`X-Mcp-Token`) | `require_mcp_token` em `services/mcp/main.py` |
| Canal entre instâncias (convite e controle) | Outra instância do Inteligência Aberta (um **par**) | **Assinatura Ed25519** (`X-IA-*`) da chave da instância | `apps/federacao/canal.py` e `views_controle.py` |

### 1. Sessão Django — páginas web

`apps/accounts/middleware.py::LoginRequiredMiddleware` exige `request.user.is_authenticated` para **toda** URL fora de uma allowlist explícita (`/entrar/`, `/registro/`, `/admin/`, `/static/`, `/federacao/convite/aceitar/` e `/federacao/controle/` — que se autenticam pela assinatura —, `/api/v1/token/`, `/artifacts/api/v1/artefatos/`). URL fora da allowlist sem sessão → redireciona para `/entrar/?next=<path>`.

É *secure-by-default*: o padrão é "protegido", e abrir uma rota ao público é uma decisão explícita (editar `EXEMPT_PREFIXES`). Isso substitui o padrão anterior de decorar cada view — cujo esquecimento em três views (`gallery`, `mhtml`, `content`) abriu um IDOR.

**Isolamento de tenant:** as views que servem dados (`ArtifactGalleryView`, `ServeMHTMLView`, `ArtifactContentView`) filtram por `tenant__in=orgs_do_usuario(request.user)` (`apps/accounts/views.py::orgs_do_usuario`, deriva as organizações via `Membership`). Artefato de outra organização retorna **404** (não 403 — não vaza existência).

### 2. JWT — extensão Chrome → orchestrator

A extensão não tem sessão de browser com o orchestrator, e a identidade não pode ser auto-declarada (era o buraco anterior: `user_id`/`tenant_id` eram texto livre no popup). O fluxo:

```
1. Usuário faz login no popup (usuário + senha)
2. POST portal:8000/api/v1/token/  → { access, refresh }
   (djangorestframework-simplejwt; TenantTokenObtainPairSerializer
    embute as claims tenant_id + username no access token)
3. Extensão guarda access/refresh em chrome.storage.local
4. Captura: POST orchestrator:8001/api/v1/capture/mhtml
   com header Authorization: Bearer <access>
5. Orchestrator valida a assinatura (jwt.decode com JWT_SIGNING_KEY)
   e lê user_id/tenant_id das claims — sem consultar o banco,
   sem confiar em nada que o cliente declare fora do token
```

Access token expira em 12h, refresh em 7 dias (`SIMPLE_JWT` em `config/settings/base.py`).

### 3. Token de serviço — orchestrator → portal

A criação de artefato (`POST /artifacts/api/v1/artefatos/`) é uma chamada **serviço-a-serviço** dentro da rede Docker — quem chama é o orchestrator, não o usuário. JWT de usuário seria inadequado aqui. Em vez disso, o orchestrator envia o header `X-Internal-Token: <INTERNAL_API_TOKEN>`; o portal valida com `constant_time_compare`. Sem o token válido → **403**.

O `user_id`/`tenant_id` no corpo dessa chamada agora são **confiáveis**, porque o orchestrator só os extrai de um JWT que ele mesmo já validou. O portal ainda faz uma segunda checagem: exige os dois campos e valida que o usuário pertence ao tenant (`Membership`) — defesa em profundidade. O fallback "primeiro usuário" (que mascarava erros e permitia escrita em tenant arbitrário) foi removido.

### 4. Token de ferramenta — Swagger do MCP publicado

O MCP (`services/mcp/`) é FastAPI, então ganha Swagger UI automático em `/docs`. Em **desenvolvimento** a porta 8002 é publicada no host **especificamente para deixar essa documentação visível** para fins didáticos (ver seção seguinte) — mas isso não reabre as ferramentas em si. Em **produção** (`docker-compose.prod.yml`) a porta não é publicada: só o orchestrator alcança o MCP, pela rede interna do compose. `/tools/cnpj`, `/tools/processos` e `/tools/noticias` exigem o header `X-Mcp-Token: <MCP_API_TOKEN>`, validado com `hmac.compare_digest` (`main.py::require_mcp_token`); sem o token correto → **401**. `/docs`, `/openapi.json` e `/health` continuam abertos, sem token. O header aparece automaticamente na spec OpenAPI (FastAPI o documenta por vir de um parâmetro `Header()`), então quem abre o Swagger já vê que precisa dele para testar.

### 5. Assinatura Ed25519 — entre instâncias

Entre instâncias **não há segredo compartilhado**: cada instância tem um par de chaves (`ChaveInstancia`, `did:key`) e cada requisição leva a assinatura sobre uma mensagem `ia-ctrl-v1` que cobre **método, rota+query, timestamp, nonce, DID de origem, DID de destino e o hash do corpo** (cabeçalhos `X-IA-DID`, `X-IA-Timestamp`, `X-IA-Nonce`, `X-IA-Destino`, `X-IA-Assinatura`). Janela de ±60 s (exige NTP); o nonce fica no cache Redis e só é queimado por requisição autêntica; a resposta de sucesso também é assinada e presa ao nonce.

Depois da assinatura, o servidor confere que o DID é de um **par `confirmado` e ativo** (o enrolamento exige conferir a impressão digital por fora do canal), aplica um limite de taxa por par e exige o **tipo** certo: o inventário e os comandos só valem para par **próprio**, e um comando também exige um **ator afirmado dono ou administrador**. Toda recusa de quem não é par confirmado devolve o **mesmo 404**; o motivo vai só ao log local (`federacao.canal`).

**Limite assumido:** o receptor **não consegue verificar** o papel que a origem afirma — quem controla a chave de um par próprio manda comandos. É o preço do rótulo "próprio" (atribuído por cada lado, sem prova de mesmo dono); o ator afirmado e o par de origem ficam no `AuditLog`. As rotas `/federacao/*` são de VPN/LAN e o Caddy as bloqueia. Detalhes e alternativas: [ADR 012](../arquitetura/decisoes/012-controle-de-instancias-pares.md).

### Autorização por papel (organização)

Pela primeira vez o portal usa `Membership.role`: `apps/accounts/permissoes.py` (`eh_admin`, `exige_admin`, `orgs_onde_e_admin`) — só dono e administrador **vigentes** (`expires_at`); `is_staff`/superusuário **não** administra organização alheia. Hoje vale para `/cluster/pares/` e para instalar/remover modelos do Ollama; `orgs_do_usuario` (isolamento por organização) não mudou.

## Documentação da API (Swagger) — pública por decisão

Portal e MCP expõem documentação interativa da API, e as duas ficam **públicas, sem exigir login/token para ver** — decisão deliberada, não descuido:

| Serviço | URL | Gerador |
|---|---|---|
| Portal | `http://localhost:8000/api/docs/` (Swagger UI), `/api/redoc/` (ReDoc), `/api/schema/` (OpenAPI cru) | `drf-spectacular` |
| Orchestrator | `http://localhost:8001/docs` | Automático do FastAPI |
| MCP | `http://localhost:8002/docs` | Automático do FastAPI |

**Por que é seguro deixar público:** a documentação descreve *o formato* dos endpoints (rotas, parâmetros, exemplos de request/response) — não expõe dado nenhum de usuário ou artefato. É o mesmo modelo que APIs públicas conhecidas usam (Stripe, GitHub): docs abertas, chamadas autenticadas. Nenhum dos mecanismos de autenticação das seções 1–4 muda por causa disso — abrir a página de documentação não abre a API. No Portal, isso é feito com `SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"] = ["AllowAny"]` mais três prefixos na allowlist do `LoginRequiredMiddleware` (`/api/schema/`, `/api/docs/`, `/api/redoc/`); no MCP, a página `/docs` do FastAPI nunca teve proteção — o que mudou foi só publicar a porta e proteger as ferramentas chamadas de verdade.

**O que não aparece no Swagger do Portal:** `ArtefatoCreateAPIView` é uma `django.views.View` pura (não DRF), então o `drf-spectacular` não a introspecciona — só os endpoints de token (`/api/v1/token/`, `/api/v1/token/refresh/`) aparecem. Isso é intencional: é um canal interno orchestrator→portal, não uma rota pensada para uso interativo externo.

## Segredos compartilhados (env)

| Variável | Quem usa | Papel |
|---|---|---|
| `JWT_SIGNING_KEY` | portal (assina) + orchestrator (valida) | **Deve ser idêntico** nos dois serviços. No portal, cai para `SECRET_KEY` se ausente. |
| `INTERNAL_API_TOKEN` | orchestrator (envia) + portal (valida) | Segredo do canal serviço-a-serviço (criação de artefato). |
| `MCP_API_TOKEN` | quem chama as ferramentas do MCP (validado pelo próprio MCP) | Segredo do canal de chamada das ferramentas (`/tools/*`); distinto do `INTERNAL_API_TOKEN` — fronteira diferente. |

Todos vêm do `.env` (via `env_file` no `docker-compose.yml`, que todos os serviços já carregam). Ver `.env.example`.

## Registro e superusuário

O registro (`/registro/`) cria um usuário + organização própria + `Membership(owner)` (via `apps/accounts/services.criar_organizacao_individual`). **O primeiro usuário do sistema vira superusuário** (`is_staff=True`, `is_superuser=True`) — decisão de especificação, não bug: quem instala não precisa rodar `manage.py createsuperuser` à parte.

**O cadastro é aberto** (`REGISTRO_ABERTO=true`, o padrão em qualquer ambiente; ADR 007, emenda de 2026-10-04). Quem se cadastra depois do primeiro ganha uma conta e uma organização **só sua** e não vê os dados de ninguém até receber permissão — o isolamento é por organização (`orgs_do_usuario`). Numa instância **sem nenhum usuário**, `/entrar/` e qualquer página protegida levam **direto a `/registro/`**, que avisa que a primeira conta será o administrador. `REGISTRO_ABERTO=false` é um travão opcional: `/registro/` passa a responder **403** (GET e POST) assim que existe usuário.

**Riscos do cadastro aberto.** (1) *Janela de corrida no primeiro boot:* quem chegar primeiro vira administrador — faça o primeiro cadastro **antes** de expor a porta. O `bootstrap_instancia` só cria o dono se `DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD` estiverem definidas (opcionais). (2) *Qualquer pessoa que alcance a porta* cria conta e organização e passa a consumir armazenamento e processamento — inclusive o LLM, se houver chave global e o usuário permitir LLM externo para um dado que não seja `restrito`. A tailnet contém isso; num host público use `REGISTRO_ABERTO=false`. Ver [`../deploy.md`](../deploy.md).

## Segredos em produção

Em produção portal, orchestrator, MCP, worker e beat se recusam a iniciar se `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY`, `INTERNAL_API_TOKEN`, `MCP_API_TOKEN`, `POSTGRES_PASSWORD` ou `S3_SECRET_KEY` estiverem vazios ou com o placeholder `CHANGE_ME` (`config/segredos.py` no portal; cópias em orchestrator e MCP). `ANTHROPIC_API_KEY` (global) e `FIELD_ENCRYPTION_KEY` são opcionais. As chaves de LLM cadastradas por organização (`LLMProvider`) são cifradas em repouso com Fernet (`apps/infrastructure/crypto.py`; a cifra deriva de `DJANGO_SECRET_KEY` se `FIELD_ENCRYPTION_KEY` estiver vazia), nunca voltam ao navegador (campo só de escrita no admin) e a chave da organização vence a global. Em desenvolvimento a checagem não se aplica.

## Transporte e exposição (produção)

`config/settings/production.py` deriva os flags de TLS de `TLS_MODE` (`proxy`, padrão: redirect para HTTPS, cookies `Secure`; `none`: HTTP puro). `ALLOWED_HOSTS` e `CSRF_TRUSTED_ORIGINS` vêm do ambiente. Num host com domínio público o Caddy (profile `publico`) termina o TLS e publica **apenas** o portal e `/api/v1/capture/*`; os canais serviço-a-serviço (`/artifacts/api/v1/artefatos/`, `/eventos/api/v1/ingest/`) são bloqueados no proxy com 404, além da exigência de token. As portas do portal e do orchestrator ficam presas a `BIND_ADDR` (padrão `127.0.0.1`).

## Fora do escopo desta rodada (hardening pendente)

- CORS do orchestrator: em produção é restrito por `CORS_ALLOWED_ORIGINS` (vazio no compose de prod = nenhuma origem); sem a variável (desenvolvimento) continua `["*"]`.
- `DEBUG`/`ALLOWED_HOSTS`: `development.py` mantém `DEBUG=True`/`["*"]`; produção usa `production.py` (ver acima).
- `SECRET_KEY` mantém fallback inseguro em `base.py` (só dev; produção valida); o cliente S3 (Garage) com `secure=False` (tráfego interno do compose).
- MCP: `/tools/*` exigem `X-Mcp-Token`, mas sem rate limit. Em produção a porta não é publicada, o que reduz a superfície; rate limit na borda (login e captura no Caddy) segue não implementado.
