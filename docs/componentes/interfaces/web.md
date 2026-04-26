# Especificação: Portal Web (Django)

## 1. Visão Geral

O Portal é a interface operacional do Inteligência Aberta. É o ponto de entrada para usuários com maior letramento digital — analistas, advogados, auditores, profissionais de saúde — que precisam de controle fino sobre configurações, relatórios completos, gestão de organizações e auditoria.

O Portal **não executa inteligência**. Ele recebe entrada do usuário, envia ao Orquestrador via HTTP interno e exibe o resultado. Toda a lógica de agentes, LLMs e coleta de dados é responsabilidade do Orquestrador e da camada MCP.

### Posição na arquitetura

```
Usuário (navegador)
        ↓
   Portal (Django :8000)
        ↓  HTTP interno (rede Docker)
   Orquestrador (FastAPI :8001)
        ↓
   Agentes → MCP → Infraestrutura
```

O Portal também se comunica diretamente com o PostgreSQL para persistir usuários, organizações, artefatos e registros de auditoria. Não acessa Qdrant, Neo4j ou MinIO diretamente — isso é responsabilidade do Orquestrador.

---

## 2. Responsabilidades

| Responsabilidade | Detalhes |
|---|---|
| Autenticação | Login, logout, registro, troca de senha |
| Gestão de usuários | Perfil, preferências |
| Gestão de organizações | Criação, tipo, configuração |
| Gestão de membros | Convite, papel, expiração, remoção |
| Gestão de artefatos | Cadastro, classificação, visualização |
| Compartilhamento | Criar, visualizar, revogar |
| Auditoria | Visualizar registros (somente leitura) |
| Investigações | Submeter consulta, acompanhar progresso, visualizar relatório |
| **Infraestrutura** | Registrar provedores de LLM, servidores MCP e repositórios de imagens |
| Administração | Painel Django Admin para operações privilegiadas |

### Fora do escopo do Portal

- Execução de agentes ou LLMs
- Indexação de embeddings
- Operações no grafo Neo4j
- Armazenamento de arquivos binários (MinIO)
- Aplicação do Motor de Políticas (responsabilidade do Orquestrador)
- Gerenciamento do ciclo de vida de contêineres (start/stop/scale — responsabilidade do orquestrador de contêineres)

---

## 3. Apps Django

O projeto Django é estruturado em três apps dentro de `services/portal/apps/`:

### 3.1 `accounts` — Contas e Organizações

Gerencia identidade e estrutura organizacional. Toda questão de **quem é quem** e **quem pertence a quê** vive aqui.

### 3.2 `artifacts` — Artefatos e Auditoria

Gerencia os dados produzidos e consumidos pelo sistema. Toda questão de **o que existe**, **com qual classificação** e **quem pode ver** vive aqui.

### 3.3 `infrastructure` — Infraestrutura Configurável

Gerencia os componentes runtime que o Orquestrador utiliza para executar investigações: provedores de LLM, servidores MCP e repositórios de imagens de contêiner. É o **plano de controle** da infraestrutura de IA — o portal registra e configura, o Orquestrador executa.

Todas as credenciais neste app são armazenadas cifradas (campo `*_encrypted`). A chave de cifração vem de variável de ambiente; nunca do banco de dados.

---

## 4. Modelo de Dados

### 4.1 `User` (accounts)

Estende `AbstractUser` do Django. UUID como chave primária para evitar enumeração sequencial.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | Gerado automaticamente |
| `username` | string | Herdado do AbstractUser |
| `email` | string | Herdado do AbstractUser |
| `password` | hash | Gerenciado pelo Django |
| *(demais campos)* | — | Herdados do AbstractUser |

**Invariantes:**
- Nenhum usuário existe sem pertencer a pelo menos uma organização após o fluxo de registro.
- UUID impede enumeração sequencial de usuários via URL.

---

### 4.2 `Organization` (accounts)

A unidade fundamental de isolamento (tenant). Dados de organizações diferentes são invisíveis entre si.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | Gerado automaticamente |
| `name` | string(255) | Nome exibido |
| `slug` | slug único | Identificador para URLs |
| `org_type` | enum | `individual` \| `team` \| `institutional` |
| `owner` | FK → User | Usuário dono; protegido contra exclusão |
| `created_at` | datetime | Imutável após criação |

**Tipos de organização:**

| Tipo | Exemplo | Características |
|---|---|---|
| `individual` | Uso pessoal de um cidadão | 1–2 usuários, dados pessoais |
| `team` | Escritório de advocacia, defensoria | N usuários, dados da equipe |
| `institutional` | Receita Federal, hospital | N usuários, Times internos, políticas próprias |

**Invariantes:**
- `owner` não pode ser removido como membro enquanto for o único `owner`.
- `slug` é imutável após criação para estabilidade de URLs.
- Dado de uma organização nunca aparece em listagens de outra — isolamento garantido em nível de query (filtro por `tenant`).

---

### 4.3 `Membership` (accounts)

Relação entre usuário e organização. Carrega papel e condições de acesso.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `user` | FK → User | |
| `organization` | FK → Organization | |
| `role` | enum | `owner` \| `admin` \| `member` \| `guest` |
| `invited_by` | FK → User (nullable) | Quem convidou; `null` para o fundador |
| `joined_at` | datetime | Imutável |
| `expires_at` | datetime (nullable) | Para convidados temporários |

**Papéis e permissões:**

| Papel | Convidar membros | Gerenciar artefatos | Ver auditoria | Excluir organização |
|---|---|---|---|---|
| `owner` | Sim | Sim | Sim | Sim |
| `admin` | Sim | Sim | Sim | Não |
| `member` | Não | Próprios | Não | Não |
| `guest` | Não | Somente leitura do compartilhado | Não | Não |

**Invariantes:**
- Um usuário pode ter memberships em múltiplas organizações simultaneamente (ex: uso pessoal + uso institucional).
- `unique_together(user, organization)` — um papel por usuário por organização.
- Membership expirada (`expires_at` no passado) bloqueia acesso como se não existisse.

---

### 4.4 `Team` (accounts)

Subgrupo dentro de uma organização. Relevante principalmente para o tipo `institutional`.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `name` | string(255) | |
| `organization` | FK → Organization | |
| `created_at` | datetime | |

*Nota: Team é modelado na Fase 0 mas só entra em uso ativo na Fase 4 (múltiplas organizações).*

---

### 4.5 `Artifact` (artifacts)

Unidade fundamental de dado no sistema. Todo dado coletado ou produzido é um artefato — a classificação de confidencialidade é parte intrínseca, não metadado opcional.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `artifact_type` | enum | `pessoa` \| `empresa` \| `documento` \| `processo` \| `endereco` \| `evento` |
| `content` | JSONField | Conteúdo estruturado específico do tipo |
| `classification_level` | enum | `publico` \| `interno` \| `restrito` \| `confidencial` |
| `tenant` | FK → Organization | Dono do artefato; nunca compartilhado implicitamente |
| `allow_external_llm` | boolean | Derivado da classificação; `false` por padrão |
| `classified_by` | FK → User (nullable) | Quem classificou; `null` = classificado pelo sistema |
| `classified_at` | datetime | Imutável |
| `expires_at` | datetime (nullable) | |
| `info_type` | enum | `fato` \| `opiniao` \| `inferencia` — obrigatório |
| `sources` | JSONField (lista) | Fontes com origem, URL, data, agente, confiança |
| `created_at` | datetime | Imutável |
| `updated_at` | datetime | Atualizado automaticamente |

**Níveis de classificação e consequências:**

| Nível | LLM externo | Embedding externo | Compartilhamento | Padrão se omitido |
|---|---|---|---|---|
| `publico` | Permitido | Permitido | Livre | Não |
| `interno` | Permitido com auditoria | Permitido com auditoria | Restrito à organização | Não |
| `restrito` | Bloqueado | Bloqueado | Explícito e limitado | **Sim** |
| `confidencial` | Bloqueado | Bloqueado | Explícito, temporário, revogável | Não |

**Invariantes:**
- `info_type` é obrigatório. Nunca omitido. Distingue fato de inferência — regra de integridade intelectual do sistema.
- Classificação é imutável após criação. Reclassificar = criar novo artefato.
- Dado sem classificação explícita recebe `restrito` por padrão (fail-safe).
- Nenhum artefato existe sem pelo menos uma fonte em `sources`.

---

### 4.6 `AuditLog` (artifacts)

Registro imutável de operações. Representa o compromisso de rastreabilidade do sistema.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `artifact` | FK → Artifact (nullable) | Artefato envolvido; `null` para operações de sistema |
| `user` | FK → User (nullable) | Usuário que originou a operação |
| `organization` | FK → Organization (nullable) | Organização no contexto da operação |
| `operation` | string(100) | Ex: `chamar_llm_externo`, `compartilhar`, `revogar_acesso` |
| `outcome` | string(20) | `permitido` \| `bloqueado` |
| `reason` | text | Justificativa (obrigatória quando bloqueado) |
| `timestamp` | datetime | Imutável; gerado automaticamente |
| `metadata` | JSONField | Contexto adicional da operação |

**Invariantes:**
- Somente inserção. Nenhum processo tem permissão de `UPDATE` ou `DELETE` nessa tabela.
- O admin Django bloqueia edição e exclusão no `AuditLogAdmin`.
- Retenção mínima: 2 anos.
- Registros de acesso a dado `confidencial` são gerados a cada acesso, não apenas na criação.

---

### 4.7 `Sharing` (artifacts)

Concessão explícita e temporária de acesso a um artefato para um destinatário fora da organização dona.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `artifact` | FK → Artifact | |
| `shared_by` | FK → User | Dono que criou o compartilhamento |
| `recipient_type` | enum | `usuario` \| `organizacao` |
| `recipient_id` | UUID | ID do destinatário |
| `reason` | text | Justificativa obrigatória |
| `status` | enum | `ativo` \| `expirado` \| `revogado` |
| `created_at` | datetime | Imutável |
| `expires_at` | datetime (nullable) | Ausência exige confirmação explícita do usuário |
| `revoked_at` | datetime (nullable) | Preenchido na revogação |
| `revoked_by` | FK → User (nullable) | |

**Invariantes:**
- Compartilhamento só pode ser criado pelo usuário com `role >= member` na organização dona.
- Destinatário só pode ler (`ler`). Não pode recompartilhar — apenas o dono pode.
- Revogação bloqueia acesso em menos de 5 segundos. Sem janela de graça.
- `expires_at` ausente exige confirmação explícita do usuário no formulário.

---

### 4.8 `LLMProvider` (infrastructure)

Provedor de LLM registrado para a organização. Define onde a inferência acontece e quais dados podem ser enviados a cada provedor.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `organization` | FK → Organization | |
| `name` | string(255) | Nome legível: ex. "Claude 3.5 Sonnet", "Ollama local" |
| `provider_type` | enum | `external` \| `local` |
| `endpoint_url` | string (nullable) | Obrigatório para `local`; omitido para externos padronizados |
| `api_key_encrypted` | string (nullable) | Obrigatório para `external`; cifrado em repouso |
| `model_name` | string(255) | Ex: `claude-sonnet-4-6`, `llama3.2:latest` |
| `allowed_classifications` | JSONField (lista) | Níveis que podem ser enviados a este provedor |
| `is_active` | boolean | Provedor inativo não é oferecido ao Orquestrador |
| `created_at` | datetime | |
| `updated_at` | datetime | |

**Invariantes:**
- `allowed_classifications` para `external` nunca pode incluir `restrito` ou `confidencial` — validação de modelo impede.
- `api_key_encrypted` nunca é serializado em resposta de API ou exibido após salvo; apenas substituível.
- Provedor `local` requer `endpoint_url` acessível na rede Docker interna.

**Exemplo de configuração:**

| name | provider_type | model_name | allowed_classifications |
|---|---|---|---|
| Claude Sonnet | external | claude-sonnet-4-6 | `["publico", "interno"]` |
| Ollama RFB | local | llama3.2:latest | `["publico", "interno", "restrito", "confidencial"]` |

---

### 4.9 `MCPServer` (infrastructure)

Servidor MCP registrado para a organização. O Orquestrador consulta esta lista para descobrir quais ferramentas estão disponíveis.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `organization` | FK → Organization | |
| `name` | string(255) | Ex: "MCP Receita Federal", "MCP interno RFB" |
| `endpoint_url` | string | URL base do servidor MCP |
| `auth_token_encrypted` | string (nullable) | Token de autenticação, cifrado em repouso |
| `is_active` | boolean | |
| `health_status` | enum | `ok` \| `degraded` \| `unavailable` \| `unknown` |
| `last_health_check` | datetime (nullable) | Atualizado pelo verificador periódico |
| `created_at` | datetime | |

---

### 4.10 `MCPTool` (infrastructure)

Ferramenta descoberta em um servidor MCP. Populada automaticamente via introspection do servidor; editável pelo admin para habilitar/desabilitar por organização.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `server` | FK → MCPServer | |
| `tool_name` | string(255) | Nome canônico: ex. `consultar_cnpj`, `buscar_processos` |
| `description` | text | Descrição retornada pelo servidor MCP |
| `input_schema` | JSONField | Schema JSON dos parâmetros de entrada |
| `is_enabled` | boolean | Organização pode desabilitar ferramentas individualmente |
| `last_seen` | datetime | Última vez que o servidor reportou esta ferramenta |

**Invariantes:**
- `tool_name` é único por servidor (`unique_together(server, tool_name)`).
- Ferramenta não vista há mais de 24h é marcada com alerta no painel (ferramenta pode ter sido removida do servidor).
- Organização pode desabilitar mas não pode remover ferramentas — remoção é responsabilidade do servidor.

---

### 4.11 `ImageRegistry` (infrastructure)

Repositório de imagens Docker de onde agentes e workers são obtidos.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `organization` | FK → Organization | |
| `name` | string(255) | Ex: "GitHub Container Registry", "Registry RFB" |
| `registry_url` | string | Ex: `ghcr.io/inteligencia-aberta`, `registry.rfb.gov.br` |
| `username` | string (nullable) | Credencial de pull |
| `password_encrypted` | string (nullable) | Cifrado em repouso |
| `is_active` | boolean | |
| `created_at` | datetime | |

---

### 4.12 `ContainerImage` (infrastructure)

Imagem registrada como agente ou worker disponível para a organização. O Orquestrador usa esta lista para saber quais imagens pode instanciar.

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID (PK) | |
| `registry` | FK → ImageRegistry | |
| `image_name` | string(255) | Ex: `inteligencia-aberta/agent-extrator` |
| `tag` | string(100) | Ex: `latest`, `v1.2.0` |
| `image_type` | enum | `agent` \| `tool` \| `worker` |
| `is_active` | boolean | |
| `pull_status` | enum | `ok` \| `failed` \| `unknown` |
| `last_pull_attempt` | datetime (nullable) | |

**Invariantes:**
- `unique_together(registry, image_name, tag)` — sem duplicatas.
- Imagem com `pull_status=failed` é exibida com alerta; Orquestrador não a usa até ser resolvida.

---

## 5. Estrutura de URLs

```
/                               Painel principal (requer login)

# Autenticação
/accounts/login/                Formulário de login
/accounts/logout/               Encerrar sessão
/accounts/registro/             Criar conta + organização inicial

# Investigações
/investigacoes/                 Lista de investigações
/investigacoes/nova/            Formulário de nova investigação
/investigacoes/<id>/            Visualizar relatório

# Artefatos
/artefatos/                     Lista de artefatos da organização ativa
/artefatos/<id>/                Detalhe do artefato
/artefatos/<id>/compartilhar/   Formulário de compartilhamento
/artefatos/<id>/revogar/<sharing_id>/   Revogação de compartilhamento

# Organização
/organizacao/                   Configurações da organização ativa
/organizacao/membros/           Lista de membros
/organizacao/membros/convidar/  Formulário de convite
/organizacao/membros/<id>/      Detalhe do membro (editar papel, expiração)
/organizacao/auditoria/         Registros de auditoria (owner/admin)

# Perfil
/perfil/                        Configurações do usuário

# Infraestrutura (role >= admin)
/infra/llm/                          Lista de provedores de LLM
/infra/llm/novo/                     Adicionar provedor
/infra/llm/<id>/                     Editar provedor
/infra/llm/<id>/excluir/             Remover provedor

/infra/mcp/                          Lista de servidores MCP
/infra/mcp/novo/                     Adicionar servidor
/infra/mcp/<id>/                     Editar servidor
/infra/mcp/<id>/sincronizar/         Disparar redescoberta de ferramentas
/infra/mcp/<id>/ferramentas/         Listar e habilitar/desabilitar ferramentas

/infra/registries/                   Lista de repositórios de imagens
/infra/registries/novo/              Adicionar repositório
/infra/registries/<id>/              Editar repositório
/infra/registries/<id>/imagens/      Listar imagens do repositório
/infra/registries/<id>/imagens/nova/ Registrar imagem

# Admin Django
/admin/                         Painel administrativo (staff only)
```

---

## 6. Controle de Acesso

O controle de acesso é aplicado em dois níveis:

**Nível 1 — Autenticação:** Todas as URLs exceto `/accounts/login/` e `/accounts/registro/` exigem sessão autenticada. Redireciona para login em caso negativo.

**Nível 2 — Autorização:** Views verificam o papel do usuário na organização ativa.

```python
# Hierarquia de acesso nas views
def requer_papel(papel_minimo):
    """Decorador que verifica papel mínimo na organização ativa."""
    ...

# Exemplos de aplicação
@requer_papel("member")   # ver artefatos da própria org
@requer_papel("admin")    # convidar membros
@requer_papel("owner")    # ver auditoria completa, deletar org
```

**Isolamento de tenant:** Toda query a `Artifact`, `AuditLog`, `Sharing` e `Team` é filtrada por `tenant=request.user.organizacao_ativa`. Impossível acessar dado de outra organização por manipulação de URL.

---

## 7. Fluxos Principais

### 7.1 Registro e criação de organização

```
1. Usuário acessa /accounts/registro/
2. Preenche: nome, e-mail, senha, nome da organização, tipo da organização
3. Sistema cria: User + Organization + Membership(role=owner)
4. Redireciona para o painel
```

### 7.2 Nova investigação

```
1. Usuário acessa /investigacoes/nova/
2. Preenche: consulta em texto livre, classificação dos dados
3. Portal valida e envia POST para Orquestrador:
   { query, tenant_id, classification, user_id }
4. Orquestrador responde com investigation_id
5. Portal redireciona para /investigacoes/<id>/ com polling de progresso
6. Quando concluído, exibe relatório com fontes
```

### 7.3 Compartilhamento de artefato

```
1. Usuário acessa /artefatos/<id>/compartilhar/
2. Preenche: tipo do destinatário, ID do destinatário, motivo, validade
3. Se validade ausente: confirmação explícita
4. Sistema cria Sharing e gera AuditLog
5. Destinatário recebe notificação (Fase 4)
```

### 7.4 Revogação de compartilhamento

```
1. Usuário acessa /artefatos/<id>/ e vê lista de compartilhamentos ativos
2. Clica em revogar
3. Sistema atualiza Sharing(status=revogado, revoked_at=agora)
4. Acesso do destinatário bloqueado imediatamente
5. AuditLog registra a revogação
```

---

## 8. Pilha Tecnológica

| Camada | Tecnologia | Justificativa |
|---|---|---|
| Framework web | Django 5 | ORM maduro, admin gerado, autenticação built-in, ecossistema Python do projeto |
| Cifração de credenciais | `cryptography` (Fernet) | Cifração simétrica determinística; chave em variável de ambiente, nunca no banco |
| Banco de dados | PostgreSQL 16 | Suporte a UUID, JSONField eficiente, mesmo banco do restante do sistema |
| Templates | Django Templates | Server-side rendering; sem JavaScript obrigatório para funcionalidade básica |
| Interatividade | HTMX | Atualizações parciais de página sem framework SPA; alinhado com simplicidade |
| Comportamentos UI | Alpine.js | Pequenos comportamentos locais (modais, toggles) sem overhead de React/Vue |
| Estilização | Tailwind CSS | Utilitário; não requer CSS customizado para a maioria das telas |
| Servidor WSGI | Gunicorn (produção) | Padrão de mercado para Django em produção |
| Servidor de dev | `runserver` | Built-in do Django, suficiente para desenvolvimento local |

---

## 9. Critérios de Aceitação

### Autenticação e Registro
- [ ] Login com e-mail e senha funciona e redireciona ao painel.
- [ ] Registro cria usuário, organização e membership(owner) em uma transação atômica.
- [ ] Senha com menos de 8 caracteres é rejeitada com mensagem explicativa.
- [ ] Sessão encerrada redireciona todas as URLs para login.

### Isolamento de Tenant
- [ ] Usuário de organização A não vê artefatos de organização B por manipulação de URL.
- [ ] Query de artefatos sem filtro de tenant falha no CI (teste de regressão).

### Artefatos
- [ ] Artefato criado sem classificação explícita recebe `restrito` por padrão.
- [ ] Campo `info_type` é obrigatório; formulário rejeita submissão sem ele.
- [ ] Artefato criado sem `sources` é rejeitado pela validação do modelo.

### Compartilhamento
- [ ] Compartilhamento sem `expires_at` exige confirmação explícita antes de salvar.
- [ ] Após revogação, destinatário não consegue acessar o artefato em menos de 5 segundos.
- [ ] AuditLog é criado automaticamente a cada criação e revogação de compartilhamento.

### AuditLog
- [ ] Nenhuma view permite `UPDATE` ou `DELETE` em `AuditLog`.
- [ ] Admin Django bloqueia edição e exclusão de registros de auditoria.
- [ ] Usuário com `role=member` não acessa `/organizacao/auditoria/`.

### Investigações
- [ ] Formulário de nova investigação envia para o Orquestrador e exibe `investigation_id`.
- [ ] Relatório exibe classificação de cada dado (público, restrito, etc.).
- [ ] Erro do Orquestrador é exibido ao usuário com mensagem legível (não stack trace).

### Infraestrutura
- [ ] API key e tokens nunca são retornados em texto claro após salvos — apenas substituíveis.
- [ ] Provedor LLM `external` com `allowed_classifications` contendo `restrito` ou `confidencial` é rejeitado na validação.
- [ ] Sincronização de ferramentas MCP atualiza `MCPTool` sem apagar ferramentas existentes — apenas marca `last_seen`.
- [ ] Ferramenta MCP desabilitada pelo admin não aparece na lista enviada ao Orquestrador.
- [ ] Imagem com `pull_status=failed` exibe alerta visível no painel de infraestrutura.
- [ ] Remoção de servidor MCP ativo exige confirmação explícita e lista as ferramentas que serão perdidas.
- [ ] Provedor LLM inativo (`is_active=false`) não aparece nas opções de nova investigação.

### Desempenho
- [ ] Painel principal carrega em menos de 2 segundos com até 100 artefatos.
- [ ] Listagem de membros com até 500 registros carrega em menos de 3 segundos.

### Responsividade
- [ ] Interface funciona em telas a partir de 375px de largura (smartphone).

---

## 10. Dependências

| Dependência | Tipo | Motivo |
|---|---|---|
| Orquestrador (`:8001`) | HTTP interno | Submissão de investigações e sincronização de ferramentas MCP |
| PostgreSQL (`:5432`) | Banco de dados | Persistência de todos os modelos |
| Servidores MCP registrados | HTTP externo/interno | Health check e descoberta de ferramentas |
| MinIO | Futura (Fase 1) | Upload de documentos — não acessado diretamente pelo Portal |
| Qdrant | Futura (Fase 1) | Busca semântica — não acessado diretamente pelo Portal |

---

## 11. O que não está no escopo desta especificação

Os itens abaixo estão previstos no roadmap mas **não fazem parte da Fase 0**:

- Upload e gestão de documentos (Fase 1)
- Visualização de grafo de vínculos (Fase 2)
- Exportação em PDF (Fase 2)
- Alertas de movimentação processual (Fase 2)
- Notificações de compartilhamento (Fase 4)
- Políticas de acesso configuráveis por organização (Fase 4)
