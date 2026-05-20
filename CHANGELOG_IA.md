# Changelog das Inteligências Artificiais

Este arquivo documenta as alterações, configurações e implementações feitas por IAs (agentes) neste repositório. O objetivo é manter um histórico unificado e transparente sobre o estado do desenvolvimento, facilitando o onboarding de novas IAs e humanos na base de código.

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
