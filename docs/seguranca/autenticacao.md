# Segurança: Autenticação e Controle de Acesso

A autenticação usa **três mecanismos**, cada um na fronteira em que é adequado. Não há um único esquema para tudo — um cliente de browser, um cliente externo com identidade de usuário, e uma chamada serviço-a-serviço têm necessidades diferentes.

## As três camadas

| Fronteira | Cliente | Mecanismo | Onde é validado |
|---|---|---|---|
| Páginas web do portal | Navegador humano | **Sessão Django** (cookie) | `LoginRequiredMiddleware` (allowlist) |
| API de captura/investigação | Extensão Chrome (usuário) | **JWT** (HS256) | Portal emite; orchestrator valida |
| API interna de criação de artefato | Orchestrator → Portal | **Token de serviço** (`X-Internal-Token`) | `ArtefatoCreateAPIView` |

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

## Segredos compartilhados (env)

| Variável | Quem usa | Papel |
|---|---|---|
| `JWT_SIGNING_KEY` | portal (assina) + orchestrator (valida) | **Deve ser idêntico** nos dois serviços. No portal, cai para `SECRET_KEY` se ausente. |
| `INTERNAL_API_TOKEN` | orchestrator (envia) + portal (valida) | Segredo do canal serviço-a-serviço. |

Ambos vêm do `.env` (via `env_file` no `docker-compose.yml`, que todos os serviços já carregam). Ver `.env.example`.

## Registro e superusuário

O registro (`/registro/`) é aberto e cria um usuário **comum** + organização própria + `Membership(owner)`. Superusuário/staff é provisionado apenas via `manage.py createsuperuser` — a auto-promoção do primeiro cadastro a `is_superuser` foi removida (era escalada de privilégio por corrida num registro aberto).

## Fora do escopo desta rodada (hardening pendente)

- CORS do orchestrator ainda é `allow_origins=["*"]` — restringir à origem da extensão.
- `DEBUG=True`/`ALLOWED_HOSTS=["*"]` em uso (roda sempre com `development.py`).
- `SECRET_KEY` com fallback inseguro; `MINIO` com `secure=False` e credenciais default.
- MCP sem autenticação (mitigado por não estar publicado no host).
