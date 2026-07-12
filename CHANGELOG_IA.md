# Changelog das Inteligências Artificiais

Este arquivo documenta as alterações, configurações e implementações feitas por IAs (agentes) neste repositório. O objetivo é manter um histórico unificado e transparente sobre o estado do desenvolvimento, facilitando o onboarding de novas IAs e humanos na base de código.

## [12-07-2026] - Camada de autenticação: sessão (web) + JWT (extensão) + token de serviço (inter-serviço) + isolamento de tenant

**Contexto e motivação:**
- Auditoria de segurança revelou que o sistema não tinha autenticação em quase nenhum endpoint. No portal, só `dashboard` e `busca` exigiam login; `ArtifactGalleryView`, `ServeMHTMLView` e `ArtifactContentView` eram acessíveis sem sessão e sem filtro de tenant — IDOR: qualquer UUID servia MHTML/texto de qualquer organização. `ArtefatoCreateAPIView` era `csrf_exempt` sem auth, confiando em `user_id`/`tenant_id` do corpo da requisição. No orchestrator, `/api/v1/capture/mhtml` e `/investigar` não tinham autenticação e a identidade era auto-declarada. A extensão Chrome mandava captura sem nenhuma credencial (user_id/tenant_id eram texto livre no popup).
- Decisão de arquitetura (validada com o usuário): usar cada mecanismo na fronteira adequada, em vez de um esquema único. JWT (tecnologia da disciplina) entra onde se justifica — o cliente externo não confiável (extensão). Hardening de configuração (CORS wildcard, SECRET_KEY, DEBUG) ficou fora desta rodada por decisão explícita.

**O que foi implementado:**

### Portal Django — emissão de JWT + token de serviço + login obrigatório + isolamento de tenant
- **`requirements.txt`:** `djangorestframework==3.15.2`, `djangorestframework-simplejwt==5.3.1`. DRF é usado apenas nas rotas de token; as demais views continuam `django.views.View` puras com sessão.
- **`config/settings/base.py`:** `rest_framework`/`rest_framework_simplejwt` em INSTALLED_APPS; `LoginRequiredMiddleware` na pilha (após AuthenticationMiddleware); blocos `SIMPLE_JWT` (HS256, access 12h/refresh 7d), `REST_FRAMEWORK`, e os segredos `JWT_SIGNING_KEY` (cai para SECRET_KEY) e `INTERNAL_API_TOKEN`.
- **`apps/accounts/serializers.py` (novo):** `TenantTokenObtainPairSerializer` embute claims `tenant_id` (org onde o usuário é OWNER, senão a primeira Membership) e `username` no access token — assim o orchestrator lê a identidade sem tocar o banco.
- **`config/urls.py`:** rotas `/api/v1/token/` e `/api/v1/token/refresh/`.
- **`apps/accounts/middleware.py` (novo):** `LoginRequiredMiddleware` — exige sessão em toda URL fora de uma allowlist explícita (secure-by-default). Substitui o padrão de decorar view a view, cujo esquecimento causou o IDOR. Escrito à mão porque o middleware nativo do Django só existe a partir da 5.1 (projeto está no 5.0.6).
- **`apps/accounts/views.py`:** helper `orgs_do_usuario(user)` (organizações via Membership); removida a auto-promoção do primeiro registro a `is_superuser` (escalada de privilégio por corrida).
- **`apps/artifacts/views.py`:** `ArtefatoCreateAPIView` valida `X-Internal-Token` com `constant_time_compare` (403 sem token), exige `user_id`/`tenant_id` e valida `Membership` (removido o fallback "primeiro usuário" que causou o 400 da sessão anterior e permitia escrita em tenant arbitrário); `gallery`/`mhtml`/`content` filtram por `tenant__in=orgs_do_usuario(...)` (404 no IDOR).

### Orchestrator — validação de JWT + repasse de token de serviço
- **`requirements.txt`:** `pyjwt==2.9.0`.
- **`main.py`:** dependência `require_jwt` (decodifica o Bearer com `JWT_SIGNING_KEY`, HS256; 401 em ausente/inválido/expirado; exige claims de identidade) aplicada em `/api/v1/capture/mhtml` e `/investigar`. `user_id`/`tenant_id` passam a vir das claims (removidos os `Form(None)` e o `InvestigationRequest.tenant_id`/`user_id`). A chamada httpx ao portal envia `X-Internal-Token`.

### Extensão Chrome — login e Bearer
- **`popup.html`/`popup.js`:** seção "Identificação" (UUIDs manuais) substituída por "Conta" com login usuário/senha → `POST /api/v1/token/` → guarda access/refresh em `chrome.storage.local`; indicador "logado como X" + botão Sair; botão Capturar desabilitado sem login.
- **`background.js`:** lê o access token do storage e envia `Authorization: Bearer`; trata 401 como "faça login novamente" (via `NeedsLoginError`); parou de anexar user_id/tenant_id ao FormData.

### Infra e docs
- **`.env.example` / `.env`:** `JWT_SIGNING_KEY` e `INTERNAL_API_TOKEN` (todos os serviços já carregam via `env_file`, não precisou mexer no compose).
- **`docs/seguranca/autenticacao.md` (novo):** documenta as três camadas, o fluxo de token da extensão e os segredos.
- **`docs/componentes/interfaces/web.md` §6:** reescrito para o modelo implementado.

### Correções de UX encontradas testando o fluxo de sessão (mesma sessão)
- **`templates/base.html`:** o link "Sair" era um `<a href="/sair/">` (GET) — desde o Django 4.1, `LogoutView` só aceita POST (proteção contra logout forjado via link/CSRF), então o botão sempre retornou 405, silenciosamente, mesmo antes desta rodada. Trocado por `<form method="post">` com `{% csrf_token %}`, estilizado para se comportar como link.
- **`templates/artifacts/gallery.html`:** página standalone (tema escuro próprio) que não estende `base.html`, logo não herdava a navbar/link "Painel" — não havia como voltar ao dashboard a partir do visualizador. Adicionado botão "← Painel" no cabeçalho.
- Achado ao investigar por que a sessão sobrevivia a fechar o navegador: comportamento esperado do Django (`SESSION_EXPIRE_AT_BROWSER_CLOSE=False` por padrão, cookie válido por 14 dias) — mantido assim por decisão consciente, documentado, não é bug.

**Testes realizados:**
- `python -m py_compile` limpo em todos os arquivos Python alterados (portal + orchestrator).
- Ponta-a-ponta com containers reais (`docker compose build portal orchestrator && ./scripts/subir_containers.sh`, `createsuperuser`): token emite com claims `tenant_id`/`username`; API interna do portal 403 sem `X-Internal-Token`; orchestrator 401 sem `Authorization`, 200 com Bearer válido; captura completa extensão→orchestrator→portal cria artefato no tenant correto; segundo usuário recebe 404 ao tentar acessar artefato de outro tenant (`content` e `gallery`); dono acessa seu próprio artefato normalmente; logs de portal/orchestrator/worker sem exceções inesperadas.
- Correções de UX verificadas via `django.test.Client`: dashboard renderiza o `<form action="/sair/">`; `POST /sair/` retorna 302 e a sessão de fato encerra (`GET /` pós-logout redireciona); galeria contém o link "Painel" apontando para `/`.

**Status Atual:**
- As três fronteiras (páginas web, API da extensão, canal inter-serviço) exigem credencial; IDOR de leitura fechado por filtro de tenant; logout e navegação de volta ao painel funcionando. Verificação ponta-a-ponta completa.

**Próximos Passos Sugeridos:**
- Segunda rodada de hardening: CORS do orchestrator restrito à extensão, rodar em `production.py`, rate-limit no endpoint de token.
- Autorização por papel (`Membership.role`) para operações administrativas — ainda não implementada, mencionada como evolução em `docs/componentes/interfaces/web.md` §6.

---

## [12-07-2026] - Script de subida reinicia worker/beat automaticamente + diagnóstico de 400 na extensão

**Contexto e motivação:**
- Depois de fechar a mudança de "texto sempre via trafilatura" (entrada abaixo, mesma data), surgiu a pergunta natural: `scripts/subir_containers.sh` já garante que o código novo rode? A resposta não era óbvia. O `docker-compose.override.yml` faz bind-mount de `./services/portal:/app` em cinco serviços (`portal`, `orchestrator`, `mcp`, `worker`, `beat`), mas só `portal` (`runserver`) e `orchestrator`/`mcp` (`uvicorn --reload`) de fato observam o filesystem e recarregam sozinhos. O comando do `worker` é só `celery -A config worker -l info` — sem nenhuma flag de observação — e o do `beat` é análogo. Os módulos Python de um processo Celery são importados uma vez na inicialização e ficam em memória até o processo reiniciar.
- Agravante: rodar `subir_containers.sh` de novo com `--build` não garante o restart desses dois serviços. O `docker compose up` só recria um container se a imagem resultante tiver hash diferente da que está rodando; como o código de negócio vem inteiro do bind-mount em dev (não é copiado para a imagem), editar só um `.py` não muda a imagem, e o `worker` continua de pé rodando a versão antiga de `tasks.py`/`extractors/*` mesmo com o arquivo já atualizado no disco.

**O que foi implementado:**
- **`scripts/subir_containers.sh`:** adicionado passo `docker compose -f docker-compose.yml -f docker-compose.override.yml restart worker beat` logo após `manage.py migrate`. Toda subida do ambiente agora garante que os processos Celery carreguem o código atual, sem depender de o `--build` ter gerado uma imagem com hash diferente.

**Diagnóstico registrado (sem alteração de código — ação pendente):**
- Investigado erro reportado pela extensão Chrome: `API retornou 500: {"detail":"Salvo no MinIO, mas erro ao registrar no Portal: Client error '400 Bad Request' for url 'http://portal:8000/artifacts/api/v1/artefatos/'"}`.
- Causa raiz confirmada (consulta direta ao banco + comparação de bytes do erro nos logs do `portal`): a tabela `accounts_user` está vazia (0 usuários) neste ambiente. Em `services/portal/apps/artifacts/views.py:74-77`, `ArtefatoCreateAPIView._resolve_user()` recebe `user_id=None` (a extensão só envia esse campo se preenchido manualmente na seção "Identificação" do popup, vazia por padrão) e cai no fallback `User.objects.order_by("date_joined").first()`, que retorna `None` por falta de registros — disparando o 400 `{"error": "Nenhum usuário encontrado"}` em `views.py:41-43`. Não é um bug de código: é o passo `docker compose exec portal python manage.py createsuperuser` (já documentado no `CLAUDE.md`) que ainda não foi executado neste ambiente.
- Sem mismatch de payload entre orchestrator/extensão/portal — todos os nomes de campo e valores de choice batem.

**Testes realizados:**
- `bash -n scripts/subir_containers.sh` limpo.
- Diagnóstico do 400 confirmado com `docker compose exec portal python manage.py shell` (`User.objects.count()` → 0) e comparação de tamanho em bytes do JSON de erro (`43` bytes) com o log real do `portal` (`"POST ... 400 43"`).

**Status Atual:**
- Script de subida agora é resiliente a mudanças em código executado pelo Celery. O 400 da extensão segue pendente de correção operacional — não requer mudança de código.

**Próximos Passos Sugeridos:**
- Rodar `docker compose exec portal python manage.py createsuperuser` neste ambiente e refazer a captura pela extensão para confirmar que o 400 desaparece.

---

## [12-07-2026] - Texto de busca sempre via trafilatura; LLM e extratores por tipo restritos a structured_data

**Contexto e motivação:**
- Relato de uso real: páginas classificadas como `artigo` (o tipo mais simples do pipeline) ocasionalmente salvavam texto incompleto. Diferente da falha de 03-06-2026/02-07-2026 (esqueleto HTML truncando a tabela de lançamentos antes de chegar ao LLM), aqui a causa era outra: com `allow_external_llm=True`, a primeira captura de qualquer padrão de URL passava pela Estratégia C, e o LLM — além de extrair `structured_data` e gerar o schema — também era responsável por gerar o `text` completo usado para embedding. Para páginas de prosa, isso equivale a pedir a um modelo de linguagem para transcrever um texto inteiro sem resumir, o que é uma tarefa contra a natureza do modelo e falha de forma imprevisível (resumos parciais, truncamento).
- Decisão de arquitetura: o texto usado para busca semântica nunca deveria depender do componente menos previsível do pipeline (LLM) nem de heurísticas por tipo de página. trafilatura já era usada como fallback para `artigo`/`desconhecido` e dentro de vários extratores — a mudança foi promovê-la a fonte única e obrigatória do campo `text`, rodando uma única vez por artefato, antes de qualquer detecção de tipo ou chamada de LLM. Toda a classificação adaptativa (análise estrutural, classificação por LLM, extração+schema por LLM, extratores determinísticos, `schema_driven_extract`) passa a existir só para preencher `structured_data`.

**O que foi implementado:**

- **`services/portal/apps/artifacts/extractors/strategies.py`:**
  - `extract_narrative_text(html)` (novo): chamada única de trafilatura (`include_tables=True`, com fallback `favor_recall=True`) — fonte única do campo `text`.
  - `extract_financial_table`, `extract_generic_table`, `extract_judicial_process`, `extract_company_profile`, `extract_mixed`: removida a geração heurística de texto narrativo (linhas "Transação em...", "Campo: valor" etc.); cada função agora decide sucesso/fallback pela presença de dados estruturados (transações, tabelas, campos) e retorna só `structured_data` + `extractor_version`.
  - `extract_legal_document`: simplificado para um marcador `{"tipo": "documento_juridico"}` sem chamada própria a trafilatura (duplicada — já roda uma vez em `tasks.py`).
  - `extract_fallback`: não chama mais trafilatura; retorna `{"structured_data": None, "extractor_version": "fallback:1.0"}` — o texto já foi resolvido antes, centralmente.
  - `_fmt_brl` removida (só era usada na geração de texto heurístico descontinuada).

- **`services/portal/apps/artifacts/extractors/schema_extractor.py`:**
  - `schema_driven_extract`: parou de concatenar campos/linhas em texto; sucesso/fallback agora decidido diretamente por `extracted_fields`/`extracted_tables` não vazios. Retorna só `structured_data` + `extractor_version`. O sinal usado por `_update_schema_health()` (prefixo `schema_driven:` vs `fallback:`) não muda.

- **`services/portal/apps/artifacts/extractors/llm_classifier.py`:**
  - Prompt `_EXTRACT_SYSTEM`: removido o passo "3. TEXTO" e o campo `"text"` do JSON esperado; adicionada instrução explícita de que o LLM não precisa gerar texto de busca (trafilatura cobre isso independentemente da resposta).
  - `llm_extract_and_schema()`: não lê nem retorna mais `"text"`; retorna `categoria`, `page_type`, `structured_data`, `schema`.

- **`services/portal/apps/artifacts/extractors/__init__.py`:** exporta `extract_narrative_text`.

- **`services/portal/apps/artifacts/tasks.py` (`extract_text_from_mhtml`):**
  - `text = extract_narrative_text(html_content)` roda logo após decodificar o HTML do MHTML, antes de `detect_page_type` — se trafilatura não extrai nada, o artefato é ignorado sem gastar chamadas de LLM em classificação.
  - Bloco de primeira captura com LLM: condição de sucesso trocada de `llm_result.get("text")` para `llm_result.get("structured_data")`; `extracted` passa a conter só `structured_data` + `extractor_version`.
  - `DocumentText.text` é preenchido com o texto extraído no topo da função, não mais com `extracted["text"]`.

- **Spec atualizada** (`docs/componentes/pipeline/extracao-adaptativa.md`): nova seção "Princípio: texto de busca sempre via trafilatura"; diagrama de fluxo, descrição da Estratégia C, tabela de extratores por tipo e critérios de aceitação atualizados para refletir que `text` é sempre trafilatura e todo o resto produz apenas `structured_data`.

**Testes realizados:**
- `python -m py_compile` limpo em `strategies.py`, `schema_extractor.py`, `llm_classifier.py`, `extractors/__init__.py` e `tasks.py`.
- Revisão de código: grep confirmando que nenhum consumidor restante lê `extracted["text"]` ou `llm_result.get("text")`; `_ROUTER`, `route()` e o sinal de saúde do schema (`_update_schema_health`) permanecem funcionalmente idênticos, já que dependiam de `extractor_version`, não de `text`.
- Não testado ainda ponta-a-ponta com captura real (containers não subidos nesta sessão) — validar próxima vez que uma página `artigo` com `allow_external_llm=True` for capturada, conferindo que `DocumentText.text` bate com o artigo completo mesmo quando `structured_data` vem do LLM.

**Status Atual:**
- O texto de busca do pipeline não depende mais de LLM, de extratores heurísticos por tipo de página, nem de schema gerado dinamicamente — é sempre trafilatura, calculado uma vez por artefato. `structured_data` continua vindo do caminho adaptativo (LLM na primeira captura, schema-driven nas seguintes, extrator determinístico como fallback), mas uma falha ali nunca mais deixa o documento sem texto pesquisável.

**Próximos Passos Sugeridos:**
- Validar ponta-a-ponta com captura real de um artigo de notícia com `allow_external_llm=True`: confirmar que o texto salvo é o artigo completo (trafilatura) e que `structured_data`/schema continuam sendo gerados pelo LLM normalmente.
- Retomar a decisão de design registrada em 29-05-2026 sobre roteamento pós-extração por `page_type` — ainda pendente.

---

## [02-07-2026] - Correção do truncamento cego do esqueleto HTML + atualização de modelos LLM

**Contexto e motivação:**
- Limitação registrada na sessão de 03-06-2026: a extração de lançamentos de extratos bancários via LLM saía incompleta ou vazia. Investigação confirmou a causa raiz em `extractors/skeleton.py`: o corte de 20 KB era um truncamento cego em bytes (`encoded[:MAX_SKELETON_BYTES]`), que cortava a tabela de lançamentos no meio de uma `<tr>` qualquer — o LLM recebia o cabeçalho da tabela e só as primeiras linhas que coubessem antes do corte, nunca a tabela completa.
- Os IDs de modelo configurados (`claude-haiku-4-5-20251001`, `claude-sonnet-4-6`) estavam desatualizados frente à geração atual (Haiku 4.5 sem sufixo de data, Sonnet 5 com preço promocional até 31-08-2026).

**O que foi implementado:**

- **`services/portal/apps/artifacts/extractors/skeleton.py`:**
  - `_sample_table_rows(soup)` (novo): antes da serialização final, tabelas com mais de 40 `<tr>` são reduzidas a uma amostra representativa — 20 linhas do início + 15 do fim, com um marcador `"… N linhas omitidas …"` no meio. Nunca corta uma linha ao meio; remove linhas inteiras. Isso garante que uma tabela grande (ex: extrato com centenas de transações) não consuma o orçamento inteiro de 20 KB e "morra" no meio, e que o LLM sempre veja tanto o formato da tabela quanto as transações mais recentes.
  - `_safe_byte_truncate(text, max_bytes)` (novo): substitui o truncamento cego. Usado apenas como última rede de segurança se, mesmo após a amostragem de tabelas, o esqueleto ainda ultrapassar 20 KB (ex: muito texto não-tabular). Corta no último `>` completo dentro do orçamento — nunca deixa uma tag ou atributo pela metade.
  - `compress_html_skeleton()`: chama `_sample_table_rows` antes de serializar; usa `_safe_byte_truncate` no lugar do slice de bytes cru.

- **Modelos LLM atualizados** (`services/portal/config/settings/base.py`, `extractors/llm_classifier.py`, `.env`, `.env.example`, `docs/componentes/pipeline/extracao-adaptativa.md`, `docs/componentes/interfaces/web.md`):
  - `LLM_CLASSIFIER_MODEL`: `claude-haiku-4-5-20251001` → `claude-haiku-4-5`
  - `LLM_EXTRACTOR_MODEL`: `claude-sonnet-4-6` → `claude-sonnet-5`
  - A divisão de custo permanece a mesma (Haiku para classificação barata quando a confiança estrutural é baixa; Sonnet para a extração+schema de primeira captura, cujo custo é amortizado nas capturas seguintes via `schema_driven_extract`).

**Testes realizados:**
- Simulação de extrato com 300 lançamentos (28,9 KB de HTML bruto): esqueleto final caiu para 3,5 KB, preservando a primeira e a última transação e o marcador de omissão — bem dentro do limite de 20 KB, sem cortar nenhuma linha ao meio.
- Caso extremo com ~3000 `<div>`s não-tabulares (182 KB): `_safe_byte_truncate` respeitou o limite de 20 KB e nunca deixou uma tag aberta pendurada no final.
- Tabela pequena (abaixo do limiar de 40 linhas): passa intacta, sem amostragem.
- `python -m py_compile` limpo nos três arquivos Python alterados.

### Ciclo de realimentação do schema de seletores (mesma sessão)

**Contexto e motivação:**
- Auditoria do mecanismo de "a página ainda tem a mesma estrutura?" revelou que a detecção de mudança de layout tinha um buraco: `schema_driven_extract` caía silenciosamente no trafilatura quando os seletores não casavam, sem nenhum sinal de volta ao `URLPatternCache`. O `divergence_count` não cobria esse caso porque (a) cache hits confiantes (confidence ≥ 0.9) retornam cedo sem rodar análise estrutural, e (b) divergência compara `page_type`, não saúde dos seletores. O `structure_fingerprint` também não ajuda: mede conteúdo visível (título/headings/headers), enquanto seletores dependem de classes/IDs — um rebuild de frontend com CSS hasheado quebra todos os seletores sem alterar o fingerprint.
- Além disso, o schema gerado pelo LLM era gravado sem validação — um seletor inventado só era descoberto (silenciosamente) na captura seguinte.

**O que foi implementado:**

- **`models.py` + migration `0007`:** novo campo `URLPatternCache.schema_failure_count` (PositiveIntegerField, default 0) — capturas consecutivas em que o schema não extraiu nada.

- **`tasks.py`:**
  - `_schema_reproduces_data()` (novo): valida o schema recém-gerado pelo LLM rodando `schema_driven_extract` contra o próprio HTML da captura. Se os seletores não reproduzem dados agora, não vão funcionar depois — o schema **não é gravado** (a captura atual usa o resultado direto do LLM; a próxima tenta regenerar).
  - `_update_schema_health()` (novo): chamado após `route()` quando havia schema no cache. O sinal é o `extractor_version` do resultado — `schema_driven:*` = sucesso (zera o contador); qualquer outro = os seletores caíram no fallback (incrementa). Após `SCHEMA_FAILURE_THRESHOLD` (2) falhas consecutivas: `extractor_config` zerado + `needs_review=True` → a próxima captura com LLM regenera o schema pagando o custo uma única vez.
  - Gravação de schema validado agora também zera `schema_failure_count` e limpa `needs_review` — o ciclo é autônomo (falha → invalida → regenera → valida → saudável), sem depender de revisão humana (que ainda não tem interface).

- **Spec atualizada** (`docs/componentes/pipeline/extracao-adaptativa.md`): ciclo de vida do schema reescrito com o loop de realimentação; tabela de comportamento do cache ganhou 4 linhas; seção "Limitação conhecida (2026-06-03)" substituída por "Limitações resolvidas (2026-07-02)".

**Testes realizados:**
- Sinal validado em isolamento: schema com seletores válidos → `extractor_version=schema_driven:1.0` (campos + 2 linhas de tabela extraídos); schema com seletores inexistentes (simulando rebuild de frontend) → `fallback:1.0`. O prefixo distingue os dois caminhos de forma confiável.
- `py_compile` limpo em `tasks.py`, `models.py` e na migration.
- Lógica de contador/invalidação depende do ORM — validação ponta-a-ponta pendente com containers de pé (`docker compose exec portal python manage.py migrate` necessário para aplicar a migration 0007).

**Status Atual:**
- As duas limitações registradas em 03-06-2026 (truncamento do esqueleto; schema correto mas sem dados) estão corrigidas. O sistema agora detecta mudança de estrutura pelo teste mais fiel possível — "os seletores ainda extraem dados?" — sem nenhuma chamada de LLM na verificação. Não testado ainda contra uma captura real do internet banking do BB.

**Próximos Passos Sugeridos:**
- Subir containers, aplicar migration 0007 e validar ponta-a-ponta com uma captura real de extrato bancário: primeira captura gera+valida schema, segunda usa `schema_driven_extract`, e uma mudança simulada de layout dispara a invalidação após 2 falhas.
- Retomar a decisão de design registrada em 29-05-2026 sobre roteamento pós-extração por `page_type` (tabelas → armazenamento relacional, entidades → enriquecimento de Artifact) — ainda pendente, com 3 perguntas em aberto.

---

## [03-06-2026] - Extração adaptativa com LLM + extensão configurável por domínio

**Contexto e motivação:**
- O extrator determinístico (`extract_financial_table`, `extract_company_profile` etc.) falha com frequência em páginas reais porque depende de heurísticas frágeis — headers de tabela com nomes não previstos, layouts incomuns, SPAs que usam `<div>` em vez de `<table>`. A análise estrutural classifica bem o tipo da página mas o extrator não consegue puxar os dados.
- A extensão tinha apenas um botão de captura sem contexto — nenhum controle sobre classificação ou uso de LLM.
- O cache de padrões de URL era ineficaz para SPAs (ex: internet banking do BB) onde todas as telas compartilham a mesma URL.

**O que foi implementado:**

### Pipeline — Extração Adaptativa com LLM (Estratégias A + B + C)

- **`services/portal/apps/artifacts/extractors/skeleton.py`** (novo):
  - `compress_html_skeleton(html)`: remove scripts/styles/SVG/framework attrs, trunca texto a 80 chars, limita output a 20 KB. Redução típica: 60–90%.

- **`services/portal/apps/artifacts/extractors/llm_classifier.py`** (novo/reescrito):
  - `llm_classify(skeleton, url)`: classifica tipo de página quando confiança estrutural < 0.75. Usa `LLM_CLASSIFIER_MODEL` (Haiku por padrão) — barato, só classifica.
  - `llm_extract_and_schema(skeleton, url, hint)` (função principal): na primeira captura com LLM habilitado, faz tudo em uma chamada — categoriza a página em linguagem livre ("Extrato conta corrente BB março 2025"), extrai dados estruturados, gera texto narrativo para embedding, e produz schema CSS para reuso. Usa `LLM_EXTRACTOR_MODEL` (Sonnet por padrão) — qualidade justificada pelo custo único por padrão.
  - Helper `_extract_json()`: tolera respostas com markdown code fences e texto ao redor do JSON.

- **`services/portal/apps/artifacts/extractors/schema_extractor.py`** (novo):
  - `schema_driven_extract(html, url, title, config)`: interpreta o `extractor_config` JSON com BeautifulSoup. **Sem `exec()` nem `eval()`** — o schema é dados, não código. Seletores inválidos geram warning e são pulados; extração continua para os demais campos.

- **`services/portal/apps/artifacts/extractors/detector.py`** (modificado):
  - `compute_structure_fingerprint(html)`: hash MD5 12-char de título + headings + table headers (com números removidos). Distingue telas de SPAs que compartilham URL.
  - `detect_page_type()` atualizado: aceita `allow_external_llm`, usa fingerprint na chave de cache, ativa `llm_classify` quando confiança < 0.75, retorna `cache_obj` como 5º valor.

- **`services/portal/apps/artifacts/extractors/strategies.py`** (modificado):
  - `route()` aceita `cache_obj`: se `extractor_config` presente, usa `schema_driven_extract`; caso contrário, roteamento determinístico.

- **`services/portal/apps/artifacts/tasks.py`** (modificado):
  - Novo fluxo: se `allow_external_llm=True` e sem schema no cache → chama `llm_extract_and_schema` **em vez** do extrator determinístico. Resultado do LLM é usado imediatamente (não só na próxima captura).
  - Schema gerado pelo LLM é gravado em `URLPatternCache.extractor_config`.
  - Capturas seguintes: cache HIT → `schema_driven_extract` sem LLM.

- **`services/portal/apps/artifacts/models.py`** (modificado):
  - `URLPatternCache`: novo campo `structure_fingerprint` (max_length=32, default=""), `unique_together` atualizado para `(tenant, domain, path_pattern, structure_fingerprint)`.

- **Migrations:**
  - `0005_urlpatterncache_detection_source`: campo `detection_source`.
  - `0006_urlpatterncache_structure_fingerprint`: campo `structure_fingerprint` + unique_together.

- **Settings e env:**
  - `ANTHROPIC_API_KEY`, `LLM_CLASSIFIER_MODEL` (Haiku), `LLM_EXTRACTOR_MODEL` (Sonnet) adicionados a `settings/base.py` e `.env`.
  - `anthropic>=0.40.0` adicionado a `requirements.txt`.

### Extensão do Navegador — Configuração por Domínio

- **`clients/browser-extension/popup.html`** (reescrito):
  - UI 320px com: barra de domínio + badge "configurado"/"padrão", grid de classificação 2×2 (público/interno/restrito/confidencial com cores distintas), toggle de LLM com aviso automático para dados restritos/confidenciais, seção colapsável "Identificação" com user_id e tenant_id.

- **`clients/browser-extension/popup.js`** (reescrito):
  - Lê domínio da aba ativa, carrega config do `chrome.storage.local` por chave `config_{domain}`, salva ao clicar "Salvar", passa config ao background.js na captura.

- **`clients/browser-extension/background.js`** (modificado):
  - Inclui `allow_external_llm`, `classification_level`, `user_id`, `tenant_id` no FormData enviado ao Orchestrator.

- **`services/orchestrator/main.py`** (modificado):
  - Novo param `allow_external_llm: bool = Form(False)`, repassado ao Portal.

- **`services/portal/apps/artifacts/views.py`** (modificado):
  - `ArtefatoCreateAPIView` usa `allow_external_llm` do payload. Valida contra `classification_level`: flag ignorada para `restrito`/`confidencial` (espelhando `policy_engine`).

**Problemas identificados durante testes:**

1. **`ImportError` em `__init__.py`**: após remover `llm_generate_schema`, o `__init__.py` ainda importava o nome antigo. Corrigido para `llm_extract_and_schema`.
2. **LLM retornando JSON com markdown**: `llm_generate_schema` e `llm_classify` falhavam silenciosamente quando o LLM embrulhava a resposta em ` ```json ... ``` `. Corrigido com `_extract_json()`.

**Limitação conhecida — não resolvida:**
- A extração de lançamentos de extratos bancários (ex: BB) via LLM está saindo incompleta ou vazia. O esqueleto de 20 KB não cobre todos os lançamentos da tabela — o corte pode eliminar exatamente os dados mais importantes. O schema gerado pelo LLM aparenta estar correto estruturalmente, mas o `schema_driven_extract` não encontra os elementos esperados nas capturas subsequentes. Investigação pendente para a próxima sessão.

**Próximos Passos:**
- Investigar extração incompleta de lançamentos: analisar o esqueleto gerado para uma página de extrato e verificar se as linhas da tabela estão sendo cortadas.
- Testar fingerprint de SPAs: confirmar que telas diferentes do BB geram fingerprints distintos e criam cache entries separados.
- Considerar enviar texto plano das tabelas (sem estrutura HTML) ao LLM para páginas `tabular_*` — evita desperdício do contexto com markup irrelevante.
- Implementar URL fragment na normalização de URL (hash routing como `/#/extrato`).

---

## [02-06-2026] - Correção de encoding no pipeline MHTML + scripts de ambiente

**Contexto e motivação:**
- Páginas capturadas com a extensão exibiam caracteres portugueses corrompidos (`Cart?o`, `Poupan?a`, `Aten??o`) na galeria e no visualizador MHTML. O problema afetava sites que declaram charset incorreto ou inconsistente nos headers MIME — padrão comum em portais bancários e governamentais brasileiros.
- A causa raiz estava em dois lugares com o mesmo padrão: `payload.decode(charset, errors='replace')` usava apenas o charset declarado no header MIME e, ao falhar silenciosamente com `errors='replace'`, gravava U+FFFD no banco ou exibia lixo no preview.

**O que foi implementado:**

- **`services/portal/apps/artifacts/tasks.py`:**
  - Adicionada função `_decode_html_bytes(payload, mime_charset)` com cascade de decodificação em quatro níveis: (1) charset do header MIME; (2) `<meta charset>` extraído por regex nos primeiros 4 KB dos bytes brutos (funciona mesmo quando o charset do MIME está errado); (3) fallbacks explícitos para Europa Ocidental — `utf-8`, `cp1252`, `iso-8859-1` (cobrem todos os sites legados brasileiros); (4) `latin-1` com `errors='replace'` como último recurso absoluto.
  - Removida dependência de `charset-normalizer`: durante testes, a biblioteca identificava incorretamente `cp1250` (Europa Central) em vez de `cp1252` para texto português, produzindo `ă` no lugar de `ã`. Para conteúdo brasileiro, a cascade explícita é mais confiável.
  - Task `reprocess_garbled_documents` adicionada: localiza `DocumentText` com U+FFFD, apaga seus fragmentos e re-enfileira a extração via `extract_text_from_mhtml`.
  - Nota: worker Celery precisa ser reiniciado (`docker compose restart worker`) para registrar novas tasks adicionadas em runtime.

- **`services/portal/apps/artifacts/views.py`:**
  - `ServeMHTMLView` tinha o mesmo bug de encoding que `tasks.py`, mas não havia sido corrigido na sessão anterior — era o único lugar de fato visível para o usuário (o preview na galeria). Corrigido para reutilizar `_decode_html_bytes` importado de `tasks`.
  - Esta foi a causa real dos caracteres corrompidos na interface: o banco armazenava o texto corretamente, mas o viewer renderizava o MHTML com `errors='replace'`.

- **`scripts/subir_containers.sh` e `scripts/limpar_containers.sh` (novos):**
  - `subir_containers.sh`: sobe com `docker-compose.override.yml` (hot-reload), aguarda o portal responder, roda `migrate` automaticamente e imprime as URLs dos serviços.
  - `limpar_containers.sh`: pede confirmação explícita, para containers com `--remove-orphans`, apaga `./data/` (requer `sudo` por volumes Postgres pertencentes a root) e remove imagens buildadas.

**Diagnóstico que enganou:**
- A inspeção inicial do `DocumentText` no banco mostrou texto correto (`Último`, `Sessão`, `Transações`). Isso levou a suspeitar do display, não do armazenamento — o que estava certo, mas direcionou a investigação para o lugar errado inicialmente. O banco estava correto porque o `docker-compose.override.yml` monta o código como volume: a fix em `tasks.py` estava ativa. A corrupção visível vinha de `views.py`, que não havia sido atualizado.

**Status Atual:**
- Encoding robusto em toda a cadeia: extração (tasks) e visualização (views) usam a mesma lógica de decode com fallback. Sites com charset incorreto, ausente ou incompatível com o conteúdo real são tratados corretamente.

**Próximos Passos Sugeridos:**
- NER (Etapa 4): extrair entidades (CPF, CNPJ, nomes, datas) de `DocumentText` e criar `Artifact(tipo=pessoa/empresa)` com `ArtifactLineage`.
- Adicionar `reprocess_garbled_documents` ao `CELERY_BEAT_SCHEDULE` como varredura semanal opcional.

---

## [29-05-2026] - Refatoração: separação de artefatos de inteligência e modelos de pipeline

**Contexto e motivação:**
- O modelo `Artifact` acumulava dois tipos que não são entidades de inteligência: `texto` (texto extraído de MHTML) e `fragmento` (chunk de RAG). Esses tipos violavam o contrato semântico do modelo — campos como `info_type` (`fato/opinião/inferência`) e `sources` independentes não fazem sentido para um chunk de texto.
- Problema de escala: um documento de 50 páginas produzia ~150 fragmentos na tabela `artifacts_artifact`, contaminando queries sobre entidades reais (pessoas, empresas, processos) e inflando o `AuditLog` com eventos de pipeline sem valor de auditoria de negócio.
- `ArtifactLineage` estava sendo usado para rastrear `documento → texto → fragmento`, uma cadeia de pipeline — seu propósito correto é rastrear linhagem entre artefatos de inteligência (ex: NER produzindo `empresa` a partir de `documento`).

**O que foi implementado:**

- **`services/portal/apps/artifacts/models.py`:**
  - Removidos `TEXT = "texto"` e `FRAGMENT = "fragmento"` de `Artifact.Type`. O modelo agora tem exatamente os 6 tipos de inteligência da spec: `pessoa`, `empresa`, `documento`, `processo`, `endereco`, `evento`.
  - Adicionado `DocumentText`: modelo de pipeline com relação OneToOne para `Artifact(tipo=documento)`. Campos próprios: `text`, `title`, `source_url`, `page_type`, `detection_confidence`, `detection_source`, `url_pattern_cache` (FK), `structured_data`, `extractor_version`, `char_count`, `word_count`.
  - Adicionado `DocumentFragment`: modelo de pipeline pertencente a `DocumentText`. Campos: `text`, `fragment_index`, `total_fragments`, `qdrant_point_id`, `qdrant_collection`. Classificação e tenant derivados de `fragment.document_text.document` no momento do embedding.

- **`migrations/0004_pipeline_models.py`:**
  - `RunPython` apaga artefatos existentes do tipo `texto`/`fragmento` e seus registros de `ArtifactLineage` antes de alterar as choices.
  - `AlterField` remove `texto` e `fragmento` das choices de `artifact_type`.
  - `CreateModel` para `DocumentText` e `DocumentFragment`.

- **`services/portal/apps/artifacts/tasks.py`** — reescrito:
  - `extract_text_from_mhtml`: cria `DocumentText` em vez de `Artifact(type=TEXT)`. Não cria mais `ArtifactLineage`. Idempotência via `DocumentText.objects.filter(document=artifact).first()`.
  - `fragment_text(document_text_id)`: recebe ID de `DocumentText` em vez de ID de artefato. Cria `DocumentFragment`. Não usa `ArtifactLineage`.
  - `embed_fragment(fragment_id)`: recebe ID de `DocumentFragment`. Usa `select_related("document_text__document")` para obter tenant/classificação em uma query. Persiste `qdrant_point_id` e `qdrant_collection` direto no `DocumentFragment` em vez do `content` JSON.
  - `scan_unprocessed_documents`: queries simplificadas usando os novos modelos diretamente.

- **`services/portal/apps/artifacts/admin.py`:**
  - Adicionados `DocumentTextAdmin` e `DocumentFragmentAdmin`.

- **Specs atualizadas:**
  - `docs/arquitetura/modelo-de-dados.md`: tipos de `Artifact` reduzidos a 6; nova seção "Modelos de Pipeline" documenta `DocumentText` e `DocumentFragment`.
  - `docs/componentes/pipeline-rag.md`: payload do Qdrant atualizado com `fragment_id`, `document_text_id`, `document_artifact_id` (removidos nomes antigos `artifact_id`, `parent_artifact_id`).
  - `docs/arquitetura/pipeline-transformacao.md`: diagrama de estágios corrigido; seção de modelos reescrita — `ArtifactLineage` declarado como exclusivo para linhagem de inteligência (Fase 2+, NER/correlação); cadeia de derivação documentada como `Artifact(documento) → DocumentText → DocumentFragment[N]`.

**Status Atual:**
- Modelo de dados limpo: `Artifact` representa somente entidades de inteligência. Pipeline de texto é infraestrutura separada.
- `ArtifactLineage` preservado para uso futuro em NER e correlação entre entidades.
- Para aplicar: `docker compose exec portal python manage.py migrate`.

**Próximos Passos Sugeridos:**
- NER (Etapa 4): extrair entidades (CPF, CNPJ, nomes, datas) de `DocumentText` e criar `Artifact(tipo=pessoa/empresa)` com `ArtifactLineage` apontando para o documento de origem — primeiro uso real do lineage entre artefatos de inteligência.

---

## [24-05-2026] - Especificação da Extração Adaptativa de HTML

**O que foi feito:**
- **Identificação de limitação arquitetural:**
  - A task `extract_text_from_mhtml` trata todas as páginas de forma idêntica (trafilatura sobre texto puro), o que é inadequado para extratos bancários, processos judiciais e fichas de CNPJ — tipos de página onde a estrutura tabular é o dado, não o texto narrativo.

- **Spec `docs/componentes/pipeline/extracao-adaptativa.md` (novo):**
  - Define 8 tipos de página: `artigo`, `tabular_financeiro`, `tabular_generico`, `processo_judicial`, `perfil_pessoa_juridica`, `documento_juridico`, `misto`, `desconhecido`.
  - Algoritmo de detecção em duas fases: (1) lookup no `URLPatternCache` por padrão de URL normalizado; (2) análise estrutural do HTML (table_ratio, monetary_count, process_number_count, etc.) com regras de classificação priorizadas.
  - Extratores por tipo: BeautifulSoup + heurística de colunas para `tabular_financeiro`; regex CNJ + extração de partes e movimentações para `processo_judicial`; trafilatura preservado para `artigo` e `desconhecido`.
  - Campo `structured_data` no `content` do Artifact TEXT: extrato financeiro gera JSON com lista de transações `{data, descricao, valor, saldo}`; processo judicial gera JSON com número CNJ, partes e movimentações.
  - Modelo `URLPatternCache` (novo): `tenant`, `domain`, `path_pattern` (URL normalizada com IDs substituídos por `*`), `page_type`, `confidence`, `hit_count`, `divergence_count`, `needs_review`. Isolado por tenant.
  - Lógica de aprendizado: na segunda captura do mesmo padrão, usa tipo cacheado (sem análise estrutural). Após 3 divergências entre cache e análise, marca `needs_review = true` — sinal de que o layout da página mudou (ex: nova versão do internet banking).
  - `ArtifactLineage.processor` passa a identificar extrator e versão: `extractor:tabular_financeiro:1.0`.
  - 10 critérios de aceitação definidos.

**Status Atual:**
- Especificação completa. Nenhum código escrito — objetivo desta sessão foi especificar antes de implementar.

**Próximos Passos:**
- Implementar `URLPatternCache` como modelo Django + migration.
- Implementar `detect_page_type()` e os extratores por tipo em `apps/artifacts/tasks.py` (ou módulo separado `apps/artifacts/extractors/`).
- Integrar o roteamento na task `extract_text_from_mhtml`.

---

## [20-05-2026] - Etapas 2 e 3 do Pipeline + Busca Semântica

**O que foi feito:**
- **Etapa 2 — Fragmentação de texto (`fragment_text`):**
  - Task Celery `fragment_text` em `apps/artifacts/tasks.py`: divide o artefato `texto` em chunks de 1000 chars com overlap de 100, preservando parágrafos e frases.
  - Cada chunk cria um `Artifact(tipo=fragmento)` com `ArtifactLineage(transformation='fragmentation', processor='split_text:chunk=1000,overlap=100')`.
  - Dispatch automático de `embed_fragment.delay()` para cada fragmento ao final.
  - Idempotente: verifica linhagem existente antes de reprocessar.

- **Etapa 3 — Embeddings e indexação vetorial (`embed_fragment`):**
  - Task Celery `embed_fragment` em `apps/artifacts/tasks.py`: gera vetor de 384 dimensões com `fastembed` (modelo `paraphrase-multilingual-MiniLM-L12-v2`, ONNX, CPU-only, multilíngue).
  - Upsert no Qdrant em coleção isolada por tenant: `ia_{tenant_id_sem_hifens}`, distância Cosine.
  - Payload no Qdrant: `title`, `source_url`, `fragment_index`, `text_preview`, `source_artifact_id`.
  - Salva `qdrant_point_id` e `qdrant_collection` no `content` do fragmento para rastreabilidade.
  - Idempotente: pula se `qdrant_point_id` já presente no content.
  - `scan_unprocessed_documents` atualizado com 3 gaps: doc→texto, texto→frag, frag sem qdrant_point_id.

- **Módulo `apps/artifacts/embeddings.py` (novo):**
  - Singletons lazy `get_embedding_model()` e `get_qdrant_client()` — instância única por processo worker.
  - `ensure_collection()`: cria coleção Qdrant se não existir (VectorParams dim=384, Cosine).

- **View de busca semântica (`BuscaSemanticaView`):**
  - `GET /artifacts/busca/`: formulário de busca (requer login).
  - `POST /artifacts/busca/`: gera embedding da query, busca no Qdrant da coleção do tenant do usuário, retorna top-10 por similaridade Cosine. Carrega texto completo do Artifact do banco.
  - URL registrada em `apps/artifacts/urls.py`.

- **Template `templates/artifacts/busca.html` (novo):**
  - Três estados: inicial (sem busca), sem resultados, lista de resultados.
  - Cards `<details>/<summary>` expansíveis — sem JavaScript.
  - Score exibido como porcentagem + barra CSS colorida (verde ≥70%, amarelo ≥40%, cinza <40%).
  - Cada card: score, título, URL, snippet 2 linhas, tag "trecho N"; expandido: texto completo + links "Fonte original" e "Ver captura".

- **Dashboard e navbar atualizados:**
  - Card "Busca Semântica" adicionado ao `dashboard.html`.
  - Link "Busca" adicionado à navbar em `base.html`.

- **Portal reconstruído** para incluir `fastembed==0.3.6` e `qdrant-client==1.9.2` (já estavam no `requirements.txt` mas imagem não havia sido rebuilt).

**Testes realizados:**
- Pipeline ponta-a-ponta: documento sintético criado → signal disparado → `extract_text_from_mhtml` (38ms, 179 palavras) → `fragment_text` (2 fragmentos) → `embed_fragment` × 2 (vetores indexados no Qdrant). Total < 500ms.
- Linhagem completa: `documento → texto → fragmento → Qdrant`. Todos os `ArtifactLineage` criados com transformation/processor corretos.
- Busca semântica: query "irregularidades fiscais empresa investigada" → scores 59% e 58% nos dois fragmentos do documento de teste. Sem resultados falsos positivos.
- View HTTP: `GET /artifacts/busca/` → 200, `POST` com query → 200 com resultados.

**Status Atual:**
- Pipeline completo funcional (Etapas 1–3). Do MHTML capturado até vetores indexados e buscáveis.
- Busca semântica operacional no portal web, integrada ao dashboard.

**Próximos Passos Sugeridos:**
- Etapa 4: NER — extrair entidades (CPF, CNPJ, nomes, datas) dos fragmentos e criar `Artifact(tipo=pessoa/empresa)` com linhagem.
- `SiteProfile`: LLM Discovery para aprender seletores CSS por domínio (reduz custo de extração a zero na segunda visita).
- Grafo de vínculos (Neo4j) — Fase 2 do roadmap.

---

## [20-05-2026] - Implementação da Etapa 1 do Pipeline + Correção de Integração

**O que foi feito:**
- **Implementação completa da Etapa 1 do pipeline de transformação:**
  - Modelo `ArtifactLineage` adicionado em `apps/artifacts/models.py` com tabela `artifacts_lineage`.
  - Novos tipos `texto` e `fragmento` adicionados a `Artifact.Type`.
  - Migration `0002` criada e aplicada.
  - Task Celery `extract_text_from_mhtml` em `apps/artifacts/tasks.py`: lê MHTML do MinIO, desempacota com o módulo `email`, extrai texto com `trafilatura` (modo padrão + fallback `favor_recall`), cria `Artifact(tipo=texto)` e `ArtifactLineage`.
  - Task Celery `scan_unprocessed_documents` em `apps/artifacts/tasks.py`: varre artefatos `documento` sem filho `texto` e enfileira extração — cobre histórico e garante resiliência a falhas.
  - Signal Django `dispatch_extraction_pipeline` em `apps/artifacts/signals.py`: dispara `extract_text_from_mhtml.delay()` automaticamente no `post_save` de qualquer `Artifact(tipo=documento, mhtml_path presente)`.
  - `config/celery.py` criado; `config/__init__.py` expõe `celery_app`; settings com `CELERY_BEAT_SCHEDULE` (scan a cada 2 minutos).
  - Redis 7-alpine + serviços `worker` e `beat` adicionados ao `docker-compose.yml`.

- **Bug identificado e corrigido — orchestrator bypassing Django ORM:**
  - O endpoint `POST /api/v1/capture/mhtml` do orchestrator escrevia direto no PostgreSQL via `psycopg2`, o que fazia o signal `post_save` nunca disparar.
  - Correção: orchestrator substituiu o bloco `psycopg2` por `httpx.post()` ao novo endpoint Django `POST portal:8000/artifacts/api/v1/artefatos/`.
  - Novo endpoint `ArtefatoCreateAPIView` em `apps/artifacts/views.py` encapsula a lógica de fallback (user/org) e cria o artefato via ORM, disparando o signal automaticamente.
  - `psycopg2` e `datetime` removidos das importações do orchestrator (`main.py`).

**Testes realizados:**
- Task disparada manualmente: extraiu 1166 palavras de captura existente (National Geographic Brasil). Linhagem criada corretamente.
- Signal automático: novo artefato criado → filho `texto` apareceu em < 1 segundo.
- Endpoint Django: `POST /artifacts/api/v1/artefatos/` retorna `{"artifact_id": "uuid"}` com status 201.
- Beat catch-up: ao subir, processou todos os artefatos históricos sem filho texto em paralelo.
- Fluxo real via orchestrator: artefato criado via API dispara signal → worker processa → linhagem registrada.

**Status Atual:**
- Pipeline Etapa 1 funcional ponta-a-ponta. Qualquer captura nova via extensão Chrome ou chamada ao orchestrator gera automaticamente um artefato de texto extraído com linhagem completa.

**Próximos Passos Sugeridos:**
- Etapa 2: Fragmentador — dividir o `Artifact(tipo=texto)` em chunks com overlap e criar `Artifact(tipo=fragmento)` com linhagem.
- Etapa 3: Embedder — gerar vetores com `sentence-transformers` local e indexar no Qdrant por tenant.
- Implementar `SiteProfile` (LLM Discovery para seletores CSS por domínio).

---

## [20-05-2026] - Especificação do Pipeline de Transformação de Artefatos

**O que foi feito:**
- **Conversação arquitetural:** Discussão aprofundada sobre o fluxo do dado após a captura — desde a extração de texto até a indexação semântica, NER e futuramente o grafo de vínculos.
- **Novo documento de especificação:** Criado `docs/arquitetura/pipeline-transformacao.md` descrevendo os 5 estágios do pipeline (extração → fragmentação → embedding → NER/sumarização → grafo), os novos modelos de dados e a estratégia de orquestração.
- **Decisões registradas:**
  - *Extração de texto em camadas:* `trafilatura` como extrator genérico padrão; LLM Discovery como fallback que aprende seletores CSS por domínio e salva em `SiteProfile` (LLM roda uma vez por domínio, depois é custo zero).
  - *Linhagem de dados:* Modelo `ArtifactLineage` — todo artefato derivado registra seu pai, a transformação aplicada, o processador e os parâmetros. Forma um DAG auditável de ponta a ponta.
  - *Mensageria:* Celery + Redis (já previsto na arquitetura) é suficiente para as Fases 0–4. Kafka adiado para Fase 5 (Kubernetes). Redis Streams como stepping stone intermediário se necessário.
  - *Registry dinâmico:* Modelo `SkillManifest` — skills e MCPs se auto-registram com input_type, output_type e trigger_rules. Quando uma skill nova chega, catch-up automático reprocessa artefatos históricos compatíveis.
  - *Embeddings locais:* `sentence-transformers` com `paraphrase-multilingual-mpnet-base-v2` para não enviar dados a APIs externas, compatível com a política de LLM local para dados `restrito`/`confidencial`.
- **Roadmap atualizado:** Fase 1 renomeada para "Pipeline de Transformação e RAG" com os entregáveis refinados para refletir o pipeline especificado.

**Status Atual:**
- Especificação da Fase 1 completa e coerente com a arquitetura existente. Nenhum código foi escrito nesta sessão — deliberadamente, o objetivo foi especificar antes de implementar.

**Próximos Passos Sugeridos:**
- Implementar `ArtifactLineage` e `SiteProfile` como modelos Django e gerar migrações.
- Adicionar Celery + Redis ao `docker-compose.yml`.
- Implementar a Etapa 1 do pipeline: task Celery que lê o MHTML do MinIO, extrai texto com `trafilatura` e cria um novo `Artifact` do tipo "texto" com a linhagem registrada.

---

## [19-05-2026] - Extensão de Captura e Armazenamento MHTML

**O que foi feito:**
- **Extensão do Navegador:**
  - Especificação e desenvolvimento de uma extensão Chrome (Manifest V3) capaz de capturar a página atual como um arquivo MHTML único (`chrome.pageCapture`), com o objetivo de preservar o layout e possibilitar acesso offline (cadeia de custódia).
  - Estrutura base criada em `clients/browser-extension/` (`manifest.json`, `background.js`, `popup.html`, `popup.js`).
- **Orquestrador e Armazenamento (MinIO/PostgreSQL):**
  - Implementação do endpoint `POST /api/v1/capture/mhtml` no serviço Orquestrador (FastAPI) com suporte a requisições CORS da extensão.
  - Conexão e armazenamento direto do arquivo bruto (Blob) em um bucket do MinIO.
  - Conexão banco de dados via `psycopg2` para registro automático do artefato na tabela `artifacts_artifact` do Portal (Django).
  - Implementação de fallback no backend: caso a extensão não envie dados de contexto (tenant/user), o sistema mapeia o artefato para o primeiro usuário administrador do portal e auto-cria uma organização tipo `individual` para satisfazer a constraint do banco.
- **Visualizador de Artefatos (Portal Django):**
  - Interface construída com HTML puro (Vanilla CSS Dark Mode) e JavaScript para visualização de capturas MHTML como uma SPA simples (Galeria e iframe).
  - Escrita de um Parser/Proxy Backend no Django (`ServeMHTMLView`): lê o arquivo binário do MinIO, utiliza o módulo `email` nativo do Python para descompactar o `.mhtml` dinamicamente, converte todos os *assets* (imagens, estilos) em Base64 `data:URIs` e devolve ao navegador como puro `text/html`. Isso contornou uma trava de segurança moderna do Chrome que força downloads em arquivos `multipart/related`.
  - **Sandboxing de Evidências OSINT:** Adição do atributo `sandbox=""` no iframe para desabilitar JavaScript da página capturada. Isso congela a evidência, bloqueando pop-ups e *Deep Links* maliciosos (como chamadas a `xdg-open` disparadas por *adwares* dos sites capturados) protegendo a segurança e estabilidade da aplicação.

**Status Atual:**
- Fluxo de OSINT Web ponta-a-ponta finalizado: A aba capturada é processada, atrelada a uma Organização automaticamente (Fallback MVP), salva no MinIO e listada no Visualizador Django. O arquivo é reproduzido perfeitamente offline em ambiente enclausurado (sandbox).

## [10-05-2026] - Implementação da Camada Base do Portal

**O que foi feito:**
- **Ambiente de Desenvolvimento:** 
  - Criação do `.gitignore` mapeando caches Python, ambientes virtuais (`.venv`, `.env`) e IDEs.
  - Exposição da porta `5432` do PostgreSQL no `docker-compose.override.yml` para habilitar desenvolvimento híbrido (Django local conectando no DB containerizado).
  - Configuração do `python-dotenv` em `services/portal/config/settings/base.py` para carregar as variáveis locais automaticamente.

- **Modelos do Portal (Django):**
  - Identificado que os apps `accounts` e `artifacts` já possuíam modelos criados em alinhamento estrito com a especificação da Fase 0 (User, Organization, Artifact, AuditLog, etc).
  - Criado o novo app `infrastructure` (`apps/infrastructure`) implementando todos os modelos de provedores LLM, servidores MCP e repositórios de imagens Docker, conforme definido em `docs/componentes/interfaces/web.md`.
  - Registrados os modelos recém-criados do `infrastructure` no painel de administração (`admin.py`).

**Status Atual:**
- Toda a estrutura de banco de dados (Models) do Portal web estipulada para a Fase 0 está 100% pronta e com as migrações geradas. O usuário pode rodar a aplicação via Docker ou localmente com virtualenv.

**Próximos Passos Sugeridos:**
- Implementação do frontend no Portal: Configurar a UI baseada em Tailwind CSS e HTMX (`templates/base.html`).
- Implementar as views básicas de Autenticação (Login) e Dashboard do Portal.
