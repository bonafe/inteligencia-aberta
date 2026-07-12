# Segurança: Autenticação e Controle de Acesso

A autenticação usa **quatro mecanismos**, cada um na fronteira em que é adequado. Não há um único esquema para tudo — um cliente de browser, um cliente externo com identidade de usuário, e uma chamada serviço-a-serviço têm necessidades diferentes.

## As quatro camadas

| Fronteira | Cliente | Mecanismo | Onde é validado |
|---|---|---|---|
| Páginas web do portal | Navegador humano | **Sessão Django** (cookie) | `LoginRequiredMiddleware` (allowlist) |
| API de captura/investigação | Extensão Chrome (usuário) | **JWT** (HS256) | Portal emite; orchestrator valida |
| API interna de criação de artefato | Orchestrator → Portal | **Token de serviço** (`X-Internal-Token`) | `ArtefatoCreateAPIView` |
| Ferramentas do MCP | Orchestrator (futuro) / testes manuais via Swagger | **Token de ferramenta** (`X-Mcp-Token`) | `require_mcp_token` em `services/mcp/main.py` |

### 1. Sessão Django — páginas web

`apps/accounts/middleware.py::LoginRequiredMiddleware` exige `request.user.is_authenticated` para **toda** URL fora de uma allowlist explícita (`/entrar/`, `/registro/`, `/admin/`, `/static/`, `/api/v1/token/`, `/artifacts/api/v1/artefatos/`). URL fora da allowlist sem sessão → redireciona para `/entrar/?next=<path>`.

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

O MCP (`services/mcp/`) é FastAPI, então ganha Swagger UI automático em `/docs`. A porta 8002 é publicada no host **especificamente para deixar essa documentação visível** para fins didáticos (ver seção seguinte) — mas isso não reabre as ferramentas em si. `/tools/cnpj`, `/tools/processos` e `/tools/noticias` exigem o header `X-Mcp-Token: <MCP_API_TOKEN>`, validado com `hmac.compare_digest` (`main.py::require_mcp_token`); sem o token correto → **401**. `/docs`, `/openapi.json` e `/health` continuam abertos, sem token. O header aparece automaticamente na spec OpenAPI (FastAPI o documenta por vir de um parâmetro `Header()`), então quem abre o Swagger já vê que precisa dele para testar.

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

O registro (`/registro/`) é aberto e cria um usuário + organização própria + `Membership(owner)`. **O primeiro usuário a se cadastrar no sistema vira superusuário** (`is_staff=True`, `is_superuser=True`) — decisão de especificação, não bug: o próprio registro bootstrapa o admin, então quem instala o sistema não precisa rodar `manage.py createsuperuser` à parte. Usuários seguintes se registram como contas comuns.

**Trade-off aceito:** como o registro é público, isso abre uma janela de corrida em uma instância recém-implantada — quem chegar primeiro em `/registro/` vira admin. A mitigação é operacional, não de código: crie sua conta imediatamente após subir o ambiente, antes de expor a porta do portal (8000) numa rede não confiável. É o mesmo modelo de bootstrap de vários apps self-hosted (primeiro usuário = admin).

## Fora do escopo desta rodada (hardening pendente)

- CORS do orchestrator ainda é `allow_origins=["*"]` — restringir à origem da extensão.
- `DEBUG=True`/`ALLOWED_HOSTS=["*"]` em uso (roda sempre com `development.py`).
- `SECRET_KEY` com fallback inseguro; `MINIO` com `secure=False` e credenciais default.
- MCP: `/tools/*` agora exigem `X-Mcp-Token`, mas ainda sem rate limit — a porta publicada permite tentativas de força bruta contra o token (mitigável com rate limit no FastAPI ou num proxy reverso, não implementado nesta rodada).
