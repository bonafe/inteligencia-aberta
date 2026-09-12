# Guia de Apresentação — Segurança dos Endpoints de Captura/Visualização de MHTML

Os dois endpoints abaixo são as duas pontas do mesmo fluxo: um **recebe** a página capturada (orchestrator, FastAPI), o outro **devolve** essa página pra visualização (portal, Django). Cada um usa o modelo de segurança idiomático do seu framework — é um bom contraste pra apresentação.

---

## Endpoint 1 — `POST /api/v1/capture/mhtml` (Orchestrator, FastAPI)

**Arquivo:** `services/orchestrator/main.py`

### O que ele faz

Recebe da extensão Chrome, via `multipart/form-data`: o arquivo `.mhtml` capturado, a URL da página, título, timestamp e o nível de classificação. Faz três coisas em sequência:

1. Salva os bytes brutos do MHTML no MinIO (S3-compatible)
2. Chama o Portal (`POST /artifacts/api/v1/artefatos/`) pra registrar o artefato no banco — essa chamada dispara um Django *signal* que enfileira o pipeline de extração assíncrono (Celery)
3. Devolve o `artifact_id` pra extensão

```python
@app.post("/api/v1/capture/mhtml")
async def capture_mhtml(
    file: UploadFile = File(...),
    url: str = Form(...),
    ...
    claims: dict = Depends(require_jwt),   # ← autenticação entra aqui
):
    user_id = claims["user_id"]
    tenant_id = claims["tenant_id"]
    ...
```

### Segurança: injeção de dependência do FastAPI

O mecanismo é `Depends(require_jwt)` — uma função declarada como **parâmetro** da assinatura da rota. O FastAPI executa `require_jwt` automaticamente antes do corpo da função rodar; se ela levantar uma exceção, o handler nunca é chamado.

```python
def require_jwt(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token de autenticação ausente")
    token = authorization.split(" ", 1)[1]
    try:
        claims = jwt.decode(token, JWT_SIGNING_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")
    if not claims.get("user_id") or not claims.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Token sem identidade de tenant")
    return claims
```

Pontos pra destacar:

- **Autenticação stateless.** O JWT é assinado com HS256 usando `JWT_SIGNING_KEY`, um segredo compartilhado com o Portal via `.env`. O orchestrator não consulta banco nenhum pra saber quem está chamando — a prova de identidade está *dentro* do token, criptograficamente verificável.
- **A identidade não é mais auto-declarada.** Antes desse trabalho, a extensão mandava `user_id`/`tenant_id` como texto livre no formulário — qualquer um podia se dizer qualquer organização. Agora essas informações vêm de dentro das *claims* do token, que só existem porque o usuário fez login de verdade no Portal.
- **É opt-in, por rota.** Cada endpoint que precisa de proteção declara `Depends(require_jwt)` explicitamente — não existe um middleware global de auth no FastAPI aqui (diferente do Django, ponto de contraste importante — ver seção final). O `/health`, por exemplo, não tem essa dependência e fica público de propósito.

### O segundo salto de autenticação, dentro do mesmo request

Depois de já ter passado pelo JWT, o `capture_mhtml` chama o Portal — e essa chamada usa **outro** mecanismo:

```python
headers={"X-Internal-Token": INTERNAL_API_TOKEN},
```

O JWT prova *quem é o usuário*. O `X-Internal-Token` prova *que o chamador é o próprio orchestrator*, não um script batendo direto na API interna do Portal. São duas perguntas diferentes — por isso dois segredos diferentes (`JWT_SIGNING_KEY` vs `INTERNAL_API_TOKEN`), cada um guardado só pelos dois lados que precisam validá-lo.

---

## Endpoint 2 — `GET /artifacts/<uuid>/mhtml/` (Portal, Django — `ServeMHTMLView`)

**Arquivo:** `services/portal/apps/artifacts/views.py`

### O que ele faz

Pega o MHTML de volta do MinIO e converte pra algo que o navegador consegue exibir direto num `<iframe>`. MHTML é um formato MIME multipart — a página HTML principal e cada recurso dela (imagens, CSS) vêm como partes separadas do arquivo, referenciadas por `Content-Location`/`Content-ID`. O trabalho da view é:

1. Buscar o arquivo bruto no MinIO
2. Percorrer as partes MIME (`msg.walk()`) e separar: a parte `text/html` (a página em si) das demais (imagens, CSS)
3. Cada recurso não-HTML vira uma *data URI* em base64 (`data:image/png;base64,...`)
4. Substituir, dentro do HTML, cada referência ao recurso original pela data URI correspondente
5. Devolver o HTML resultante — um único documento autocontido, sem dependências externas

```python
class ServeMHTMLView(View):
    @method_decorator(xframe_options_sameorigin)
    def get(self, request, artifact_id):
        artifact = get_object_or_404(
            Artifact, id=artifact_id,
            tenant__in=orgs_do_usuario(request.user),   # ← autorização aqui
        )
        ...
```

### Segurança: três camadas, três lugares diferentes

**1. Autenticação — middleware global, não decorator.** Diferente do orchestrator, o Django não exige que cada view declare sua proteção. Existe `LoginRequiredMiddleware` (`apps/accounts/middleware.py`) rodando pra **toda** requisição, com uma lista curta de exceções (`/entrar/`, `/registro/`, etc). Essa view nem aparece nessa lista — então por padrão, sem sessão válida, o middleware já redireciona pra `/entrar/` antes mesmo do código da view rodar.

```python
class LoginRequiredMiddleware:
    EXEMPT_PREFIXES = ("/entrar/", "/sair/", "/registro/", ...)  # ServeMHTMLView NÃO está aqui

    def __call__(self, request):
        if not request.user.is_authenticated and not self._is_exempt(request.path):
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return self.get_response(request)
```

É *secure by default*: pra abrir uma rota, alguém precisa editar a allowlist de propósito. Foi assim, ironicamente, que descobrimos o bug original — três views (essa incluída) tinham ficado sem `@login_required` porque alguém esqueceu de decorar cada uma individualmente. Trocar pra um middleware global resolveu a classe inteira do problema de uma vez.

**2. Autorização — filtro na query, não um `if` depois.** Repare que o `tenant__in=orgs_do_usuario(request.user)` está **dentro** do `get_object_or_404`, não é uma checagem separada depois de já ter buscado o artefato:

```python
artifact = get_object_or_404(Artifact, id=artifact_id, tenant__in=orgs_do_usuario(request.user))
```

Isso é deliberado: o artefato de outra organização simplesmente **não existe** do ponto de vista dessa query — o banco nunca devolve a linha, então não tem como vazar timing, mensagem de erro ou qualquer sinal que revele "esse artefato existe, só que não é seu" vs. "esse UUID não existe". Ambos os casos dão o mesmo 404. Isso fecha um IDOR (Insecure Direct Object Reference) que existia antes: qualquer UUID válido servia o MHTML de qualquer organização, bastava adivinhar ou enumerar o ID.

**3. Isolamento no navegador — `sandbox=""` no iframe.** Aqui tem uma pegadinha boa pra apresentação: o MHTML capturado pode conter **qualquer coisa** — inclusive `<script>` da página original, se ela tiver. A `ServeMHTMLView` **não sanitiza nada** — devolve o HTML capturado praticamente verbatim, só trocando URLs de recurso por data URIs. Então onde está a proteção contra esse HTML rodar JavaScript malicioso dentro do seu portal?

Não está na view — está no `<iframe>` que exibe o resultado, em `gallery.html`:

```html
<iframe id="viewer" src="" style="display:none" sandbox=""></iframe>
```

O atributo `sandbox=""` **vazio** é a forma mais restritiva do sandbox de iframe — desabilita execução de script, formulários, popups, tudo. O navegador simplesmente não executa nada de dentro daquele iframe, não importa o que vier no HTML. É um ótimo exemplo de defesa em profundidade que não é óbvia lendo só o backend: o controle de segurança contra XSS de conteúdo capturado (potencialmente hostil, já que vem de qualquer site que o usuário visitar) é **client-side**, no HTML que serve o iframe — não no código Python.

`@method_decorator(xframe_options_sameorigin)` é outra camada, mas resolve um problema diferente: impede que **outro site** carregue essa página do Portal dentro de um iframe dele (clickjacking) — protege o Portal de ser embutido em terceiros, não o inverso.

---

## Comparação — duas filosofias de segurança

| | Orchestrator (FastAPI) | Portal (Django) |
|---|---|---|
| **Autenticação** | JWT stateless, verificado por assinatura criptográfica | Sessão stateful (cookie + registro no banco) |
| **Onde a checagem é declarada** | Por rota, via `Depends(require_jwt)` — opt-in | Globalmente, via middleware — opt-out (allowlist) |
| **Risco se esquecer** | Uma rota nova sem `Depends()` nasce pública | Praticamente impossível esquecer — o padrão já é fechado |
| **Autorização (tenant)** | Comparação de claims (`tenant_id` do token) | Filtro na *queryset* do ORM (`tenant__in=...`) |
| **Efeito de acesso negado** | 401/403 explícito, HTTP puro | 404 (esconde até a existência do recurso) |

O ponto mais interessante pra levar pra apresentação: o mesmo problema — "como garantir que toda rota sensível esteja protegida" — foi resolvido de dois jeitos opostos, e os dois são válidos dentro do idioma de cada framework. FastAPI tende a favorecer explicitação por rota (você lê a assinatura da função e já sabe o que ela exige); Django tende a favorecer um middleware central quando o *custo de esquecer* é alto — que foi exatamente o que aconteceu aqui: esquecemos de proteger view por view, e a correção não foi "lembrar melhor da próxima vez", foi mudar a estrutura pra que esquecer não seja mais possível.

---

## Outros endpoints que valem menção

Se sobrar tempo ou vier pergunta, esses são bons complementos — mesma lógica, aplicada em outros pontos do sistema:

| Endpoint | Serviço | Mecanismo | Por que vale citar |
|---|---|---|---|
| `POST /api/v1/token/` | Portal (Django/DRF) | Público (`AllowAny`) — usuário + senha no corpo | É a *origem* do JWT que os outros endpoints validam. Emite o token com `user_id`/`tenant_id` já embutidos nas claims (`TenantTokenObtainPairSerializer`), pra não precisar consultar o banco depois. |
| `POST /artifacts/api/v1/artefatos/` | Portal (Django) | Token de serviço (`X-Internal-Token`, `constant_time_compare`) | É pra onde o Endpoint 1 chama por baixo dos panos — o outro lado do "segundo salto" já explicado acima. Boa view pra mostrar o código completo se quiserem ver a validação de `Membership` (usuário realmente pertence ao tenant). |
| `POST /investigar` | Orchestrator (FastAPI) | `Depends(require_jwt)` — mesmo mecanismo do Endpoint 1 | Mostra que o padrão não é um caso isolado — a mesma dependência é reaproveitada em outra rota, o que é justamente a vantagem de ter isolado a checagem numa função. |
| `GET /tools/{cnpj,processos,noticias}` | MCP (FastAPI) | `Depends(require_mcp_token)` — token dedicado (`X-Mcp-Token`, `hmac.compare_digest`) | Mesmo padrão de dependência do FastAPI, mas com um segredo *diferente* do `INTERNAL_API_TOKEN` — bom exemplo de que "parece o mesmo mecanismo" não significa "é a mesma fronteira de confiança". |
| `GET /api/docs/` (Portal) e `GET /docs` (Orchestrator e MCP) | Todos os três serviços | Público, sem autenticação nenhuma | Documentação Swagger interativa — pública de propósito, pra fins didáticos. Bom gancho pra explicar que documentação (descrição do formato da API) e dado real são coisas diferentes: abrir a página não abre a API. |
| `GET /artifacts/gallery/` e `GET /artifacts/<uuid>/content/` | Portal (Django) | Middleware global + `tenant__in=orgs_do_usuario(...)` | Mesmos dois controles do Endpoint 2 (sessão + filtro na query), aplicados a: listagem de artefatos e ao texto/dados extraídos de um artefato específico. Útil pra mostrar que o padrão se repete em vez de ser reinventado em cada view. |
| `GET /admin/` | Portal (Django) | Autenticação nativa do Django Admin (`is_staff`) | Mecanismo próprio do Django, independente do `LoginRequiredMiddleware` — é uma terceira camada de auth dentro do mesmo serviço, útil se a pergunta for "e o admin, como é protegido?". |
