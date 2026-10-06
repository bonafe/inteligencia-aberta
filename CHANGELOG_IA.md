# Changelog das Inteligências Artificiais

Este arquivo documenta as alterações, configurações e implementações feitas por IAs (agentes) neste repositório. O objetivo é manter um histórico unificado e transparente sobre o estado do desenvolvimento, facilitando o onboarding de novas IAs e humanos na base de código.

## [05-10-2026] - Tela `/membros/`: adicionar pessoa à organização e a um workspace

**Motivação:** para dois usuários entrarem no mesmo workspace do Agora eles precisam ser membros da mesma organização, e não havia tela para isso (só o `/admin/`; `Membership.invited_by` existia sem uso).

**O que foi implementado:**
- **`apps/accounts/membros.py`** (`adicionar_membro`): dono/administrador vigente informa o nome de usuário de quem já tem conta; papéis `admin`/`member`/`guest` (nunca `owner`); quem já é membro vigente mantém o papel; vínculo vencido é reativado; grava `invited_by`. Opcionalmente cria o `WorkspaceMember` (workspace da mesma organização, não arquivado; papel efetivo limitado pelo teto da organização) e avisa o `agora-sync`. Eventos `accounts.membro_adicionado` e `agora.papel_alterado`.
- **View `membros`** e rotas `/membros/` e `/membros/<org_id>/`, `templates/accounts/membros.html`, card no painel e link no menu.
- Testes: `tests/test_accounts_membros.py` (8); com `test_agora_api` e `test_cadastro_aberto`, 42 passando. Não rodei a suíte completa nem abri a tela no navegador.

**Documentação atualizada:** `docs/seguranca/autenticacao.md`, `docs/deploy.md` (Cadastro de usuários), `docs/componentes/interfaces/agora.md` e `web.md`, `CLAUDE.md`, `AGENTS.md`.

**Limites:** sem convite para quem ainda não tem conta; sem remover/rebaixar membro (segue no `/admin/`); o link do menu aparece para todos (quem não administra vê acesso negado). Não invalida dados existentes.

## [05-10-2026] - Ultima Agora no portal (ADR 013 a 016) e atualização geral da documentação

**O que existe:** o **Ultima Agora** (`/agora/`), ambiente de workspaces colaborativos, integrado ao portal. `apps/agora` (modelos `Workspace` e `WorkspaceMember`, papel efetivo com teto na organização, tokens do `agora-sync`, leitura de domínio isolada por organização); pacote de domínio `static/agora-ia/pack/` (`ia-search`, `ia-entity`, `ia-news`); front do Agora em `static/agora/` e serviço `services/agora-sync/` (**cópias** do projeto `ultima-agora`, via `scripts/sincronizar_agora.sh`; não editar). Offline-first com service worker, catálogo local e política de cache por classificação (teto da instância `AGORA_OFFLINE_CACHE_NIVEL`, nível de cada pessoa em ⚙ Configurações). Chat dos workspaces no `agora-sync` (log por workspace, retenção `CHAT_RETENTION_DAYS`). Testes: `tests/test_agora_*.py` e `scripts/testar_agora_ia.sh`.

**Documentação atualizada nesta rodada:**
- **`docs/deploy.md`:** seção nova "Ultima Agora" (profile `agora`, segredos, `AGORA_SYNC_URL` com e sem Caddy, volume `agora-sync/`, teto do cache offline, o front é cópia), mais o contrato para automação, segredos, variáveis, verificação de saúde (porta 8787), volumes e atualização. O comando do `tailscale serve` para a 8787 está marcado como **não testado**.
- **`CLAUDE.md`:** `agora-sync` na tabela de serviços e nas portas, 16 ADRs (eram 11), referências a `deploy.md` e `agora.md`, Caddy publica também `/agora-sync/*`.
- **`AGENTS.md`:** mapa dos arquivos do Agora, regra de **não editar as cópias**, comandos de teste do Agora. **`README.md`:** seção do Agora, volume `agora-sync/`, linha na stack.
- **`docs/seguranca/autenticacao.md`** (sexta camada: token curto do `agora-sync`; dois segredos novos), **`docs/componentes/interfaces/web.md`** (§3.6 e rotas `/agora/`), **`docs/arquitetura/visao-geral.md`** e **`docs/componentes/interfaces/agora.md`** (ponteiro para a implantação).
- **Site:** `index.html` (Agora em *Funciona hoje*, com o limite dito na linha), `jornada.html` e `sobre.html`. **`diario.html`:** entrada nova como **rascunho** para o dono revisar.

**Não verifiquei:** o `agora-sync` não estava rodando nesta sessão (profile `agora` desligado); os textos sobre tempo real e chat vêm das ADRs e do código, não de uma execução ao vivo. A suíte de testes não foi rodada.

## [04-10-2026] - F0 da federação, passo 3: ID global `urn:uuid:` (ADR 010)

**Decisão de implementação:** os modelos já usam UUID como PK, então o identificador global **é o próprio PK** escrito como URN — sem coluna nova nem tabela de mapeamento. Objetos que passarem entre instâncias **mantêm** o UUID de origem; a origem é dita pelo `author` e pelo espaço no envelope, não pelo ID.

**O que foi implementado:**
- **`apps/federacao/ids.py`:** `urn_de`, `uuid_de_urn`, `eh_urn_valida`, `normalizar`. Emissão sempre canônica (minúscula, com hífens), porque IDs entram em bytes assinados; leitura aceita `URN:UUID:` em maiúsculas (RFC 8141) e normaliza. Rejeita ausência de hífens, chaves, `urn:uuid:` duplicado, espaços/quebra de linha, UUID nulo, máximo e variantes não RFC 4122.
- **`Artifact.urn`** (propriedade). Os demais modelos (`DocumentText`, fragmentos, `ArtifactLineage`) ganham a propriedade quando um evento precisar citá-los: o helper aceita qualquer UUID.
- Testes: `tests/test_federacao_ids.py` (27); suíte completa com 204 passando.

**Falta da F0:** `Claim`/`Evidence` como conceitos de domínio (precisa de conversa de modelagem antes). Com isso a F0 fecha: hash do MHTML (feito), ID global (feito), chave Ed25519 da instância (feito).

## [04-10-2026] - F1, etapa 2: motor de regras de replicação (ADR 011)

**Escopo (decidido com o usuário):** implementar direto o motor puro com os testes dos pisos e da precedência, mais a tabela de regras. **Não está ligado a nenhum fluxo de envio** — não existe envio entre instâncias ainda.

**O que foi implementado:**
- **`apps/federacao/regras.py`**, função pura: pisos (`interno` nunca sai a terceiro; `restrito`/`confidencial` só por concessão — regra de *permitir* para aquele objeto e par, com validade vigente; rótulo desconhecido = `confidencial`), "negar vence", negação por padrão; a `Decisao` traz a regra que decidiu e as que casaram. Pisos só ao enviar.
- **`RegraReplicacao`** (migration `federacao.0003`), por organização, validada também fora do admin; regra com objeto exige validade (também `CHECK` no banco) e, sendo *permitir*, um par.
- **`apps/federacao/politica.py`:** regras padrão semeadas por organização (4, todas *permitir*; desativar, não apagar; não ressurgem sozinhas), `decidir`/`decidir_artefato` com as regras da organização dona do dado, `conceder`/`revogar`, e `registrar_decisao` (`PipelineEvent` `federacao.decisao` para toda decisão; `AuditLog` só para `restrito`/`confidencial`; sem conteúdo).
- **Admin** (`RegraReplicacaoAdmin`; regra padrão não se apaga) e **`manage.py regras_replicacao`** (`--semear`, `--listar`, `--decidir` para simular e explicar).
- Testes: `tests/test_federacao_regras.py` (62, incluindo o cenário dos dois notebooks e a ordem das regras embaralhada); suíte completa com 404 passando. Documentado em `federacao.md`, `observabilidade.md` e `CLAUDE.md`.

**Limites conhecidos:** nada chama o motor ainda; o par é um texto (`par_ref`) e um tipo, sem o cadastro `Par`; `decidir` carrega todas as regras ativas da organização a cada chamada (um envio em lote deveria carregá-las uma vez); a primeira `decidir` de uma organização escreve as regras padrão no banco, até em simulação; não há tela no portal, só o admin; a condição de domínio de origem não existe (decidido: a classificação por domínio cobre); o motor não decide o que fazer com o que se recebe (`quarentena`/`alegação` é a confiança do `Par`, ainda inexistente).

**Próximo na F1:** `Par` fundido com `Maquina` e o enrolamento; depois ligar o motor ao pacote offline (F1b).

## [04-10-2026] - F1, etapa 1: `Space` (só estrutura)

**Decidido com o usuário:** (1) o `Space` é só estrutura — **não concede acesso** (continua por organização, em 12+ pontos das views) e **não tem membros** ainda; (2) o **espaço padrão de cada organização é implícito, sem linhas**.

**O que foi implementado:**
- **`Space`** e **`EspacoArtefato`** em `apps/federacao` (migration `federacao.0002`): nome normalizado e único por organização, organização local (P7), `arquivado`, `criado_por`; um artefato em vários espaços; apagar o artefato desfaz o vínculo, não o espaço.
- **Espaço padrão** (`EspacoPadrao`): sem linha no banco, ID **derivado** da organização por `uuid5` (espaço de nomes fixo, coberto por teste), "todos os artefatos da organização".
- **`apps/federacao/espacos.py`:** `criar_espaco`, `arquivar`/`desarquivar`, `incluir_artefato` (mesma organização; `importado=True` mantém o `tenant` de origem, **sem uso ainda**) e `remover_artefato` (idempotentes), `artefatos_do_espaco`, `espacos_do_artefato`, `resolver_urn`.
- **Admin** (`SpaceAdmin` com inline de artefatos; o `clean` recusa artefato de outra organização).
- Testes: `tests/test_federacao_espacos.py` (27, incluindo "estar num espaço não dá acesso": o dono de B recebe 404 num artefato de A mesmo que ele esteja num espaço de B); suíte completa com 342 passando. Documentado em `federacao.md` e `CLAUDE.md`.

**Limites conhecidos:** `resolver_urn` percorre as organizações para achar um espaço padrão (barato com poucas organizações); sem membros, sem acesso por espaço, sem eventos; o espaço padrão não aparece no admin nem pode receber inclusões; a tela do portal não lista espaços.

**Próximo na F1:** `Par` fundido com `Maquina` e o motor de regras.

## [04-10-2026] - `Claim` e `Evidence` implementados, com o produtor `extruct` (fecha a F0)

**O que foi implementado** (decisões da entrada abaixo):
- **Modelos `Claim` e `Evidence`** (migration `artifacts.0017`). `Claim`: sujeito, predicado, objeto (referência **ou** literal, garantido por `CHECK`), autor, produtor e versão, modelo, `extractor_confidence` (`CHECK` em [0, 1]), classificação herdada do artefato, estado (ativa/retratada), `revisa` e `chave`. `Evidence`: `blob_hash` (`ni:`), tipo e valor do localizador e trecho de até 500 caracteres. Imutáveis no modelo (só `estado`/`retratada_em` mudam; `delete()` recusado; `Artifact` com alegações é `PROTECT`).
- **`apps/artifacts/alegacoes.py`:** `registrar_alegacao` (valida tudo antes de gravar; alegação e evidências numa transação; idempotente por `(artefato, chave)`; resolve corrida por `IntegrityError`), `retratar`, `truncar_trecho`. **`referencias.py`:** referências tipadas `urn:uuid:`, `cnpj:` (DV validado), `url:`, `dominio:`, `mencao:`.
- **`extractors/claims_extruct.py`:** primeiro produtor, só JSON-LD, lista curta e explícita de tipos/propriedades (organização, pessoa, artigo); relações `author`, `publisher` e `worksFor`; sujeito = CNPJ válido > URL > menção ancorada no blob; autor = domínio da captura. Ligado à extração como a etapa `extracao.alegacoes` (`ok`/`vazio`), **sem nunca derrubá-la**; calcula e grava `blob_hash` se o artefato não o tinha.
- Documentado em `federacao.md` e `observabilidade.md`; `CLAUDE.md` ganhou o parágrafo. Testes: `tests/test_claims.py` (64, incluindo a task de extração ponta a ponta com S3 e detecção falsos); suíte completa com 315 passando.

**Limites conhecidos:** **não há bloqueio de `UPDATE`/`DELETE` direto no banco** (só no modelo); só JSON-LD gera alegações (microdata e OpenGraph não); o perfil de empresa e as partes do processo ainda **não** geram alegações (exigem localizador nos extratores); o trecho é a representação JSON do valor declarado, não texto literal da página; entidades continuam não existindo (referências tipadas, sem resolução); capturas anteriores só ganham alegações se forem reprocessadas (`reprocessar_captura --forcar`); a tela do visualizador não mostra alegações; nenhum evento federado é emitido (não existe o log ainda).

**Estado da F0:** hash do MHTML, chave Ed25519 da instância, ID `urn:uuid:` e `Claim`/`Evidence` — **todos implementados**. Próximo: F1 (`Space`, `Par` + `Maquina`, motor de regras).

## [04-10-2026] - Federação: `Claim` e `Evidence` decididos (só documento)

**Levantamento no código:** não existe alegação como objeto; quatro extratores gravam blocos de dados no `DocumentText` (`structured_data`, o vencedor, é sempre `None`); nada cria `Artifact` de pessoa/empresa; nenhum extrator registra a origem do valor na página (exceto o `ParserSpec` do `dom2parser`); sem autoria nem confiança por item.

**Decidido (subseção "`Claim` e `Evidence`" de `federacao.md`):** `Claim` (sujeito/objeto por identificador tipado, predicado, autor da alegação, artefato de origem, produtor, `extractor_confidence` nula, classificação herdada, estado ativa/retratada, revisão opcional) e `Evidence` (hash `ni:` do blob, localizador, trecho citado de até 500 caracteres sob a classificação do objeto); alegação nunca se edita e nasce sempre com evidência, numa transação; `info_type` do artefato não muda; granularidade por atributo/relação de entidade e **não** por linha de tabela; entidades não são criadas ainda; **primeiro produtor: `extruct`**; em `apps/artifacts`.

**Pendente:** implementar; o perfil de empresa e as partes do processo exigem que os extratores registrem localizador. Nenhum código alterado. Com isso a F0 só falta de código: hash, chave e ID prontos; `Claim`/`Evidence` decididos.

## [04-10-2026] - ADR 011: política de replicação (só documento)

- Escrita `docs/arquitetura/decisoes/011-politica-de-replicacao.md`, consolidando as decisões P1–P8 da seção 16 de `federacao.md` (motor de regras em três camadas com "negar vence", interseção emissor × receptor, tudo é regra, `Par` fundido com `Maquina` e enrolamento por convite com impressão digital, tipo × confiança, o que replica e o que não replica, organização por espaço, auditoria em `AuditLog`/`PipelineEvent`) e a classificação por domínio. O status registra o que já está implementado (classificação por domínio, remoção do `compute`) e o que não está.
- **Consequência que a ADR torna explícita:** sem identidade de organização entre instâncias, `interno` nunca sai e um colega de equipe com instância própria é terceiro (só `público` ou concessões). Outras consequências: "próprio" é atestação humana; revogar não recolhe cópias; o espelho entrega `confidencial` a todas as máquinas próprias por padrão.
- Ponteiros acrescentados em `federacao.md`, na emenda da ADR 010 e na contagem de ADRs do `CLAUDE.md`. Nenhum código alterado.

## [04-10-2026] - Classificação por domínio de origem na captura

**Contexto:** o usuário quer que tudo capturado de `bancodobrasil.com.br` nasça `confidencial`. Até aqui o nível vinha de um seletor global no popup da extensão (padrão `restrito`), igual para todas as capturas. É útil por si só, sem federação, e depois alimenta as regras de replicação (o nível viaja com o objeto).

**O que foi implementado:**
- **`RegraClassificacaoDominio`** (por organização; `dominio`, `nivel`, `ativa`; único por organização+domínio; migration `artifacts.0016`) e registro no admin. O domínio é normalizado ao salvar (minúsculas, sem esquema/caminho/porta/ponto final, punycode; IPs e domínios de um rótulo são recusados).
- **`apps/artifacts/classificacao_dominio.py`** (funções puras + `aplicar_regras`): **só sobe o nível**, nunca rebaixa; casa por **sufixo de rótulo** (`bancodobrasil.com.br` cobre `www.`/`login.`, não `meubancodobrasil.com.br` nem `…com.br.outro.com`); várias regras → a de maior nível; nível pedido desconhecido conta como `restrito` na comparação; sem regra, devolve o nível pedido intacto.
- **`ArtefatoCreateAPIView`** aplica a regra **antes** de calcular `allow_external_llm`: subir para `restrito`/`confidencial` fecha o LLM externo mesmo que a extensão o tenha pedido. Emite `captura.classificada` (só quando eleva) e a resposta traz `classification_level` e `classificacao_elevada`; o orchestrator repassa os dois na resposta da captura.
- Documentado em `docs/seguranca/classificacao.md` (nova seção) e `docs/componentes/observabilidade.md` (estágio novo). O `policy_engine` não foi tocado.
- Testes: `tests/test_classificacao_dominio.py` (54); suíte completa com 251 passando.

**Limites conhecidos:** **não reclassifica o que já foi capturado** (a reclassificação é ato explícito; não há relatório do que ficou abaixo da regra — no banco de desenvolvimento só há uma captura, de `localhost`). A regra **não foi cadastrada** no banco: o domínio do Banco do Brasil precisa ser criado no admin (modelo `RegraClassificacaoDominio`, no app Artefatos). Não escreve `AuditLog` (nenhum código escreve nele hoje; o rastro é o evento). A extensão não mostra o nível efetivo; só recebe o dado. Domínios públicos de sufixo (ex.: `com.br`) não são tratados à parte: uma regra em `com.br` elevaria todo o `.com.br`, o que é seguro (só sobe) mas amplo. Sem regra por IP.

**Pendente:** ADR da política de replicação; `Claim`/`Evidence` (fecha a F0).

## [04-10-2026] - Federação: P1, P2, P4, P5, P7 e P8 fechadas — política de replicação completa (só documento)

**Decidido (seção 16 e subseção "Política de replicação: decisões P1, P2, P4, P5, P7 e P8"):** (P1) `Par` com dois campos — tipo (próprio/terceiro) e confiança (ignorar, quarentena, alegação, aceitar e repassar; padrão alegação para terceiro e repassar para próprio); (P2) interseção emissor × receptor, negação por padrão, receptor recusa em silêncio; (P4) tudo é regra, objeto individual só por concessão ou negação com escopo de objeto; (P5) eventos sempre, texto e dados estruturados como resultados nos eventos, blobs em espelho entre máquinas próprias (com limite de disco) e sob demanda por hash com terceiros, **vetores não replicam** (recalculados localmente); (P7) o espaço é o limite e aponta para uma organização local (dedicada por padrão para terceiros), objeto que já existe mantém o `tenant`, UUID de origem preservado; (P8) `AuditLog` só para `restrito`/`confidencial` e concessões (permitidas e bloqueadas), `PipelineEvent` para o fluxo todo, sem conteúdo, motor num ponto único.

**Com isso, as oito decisões da seção 16 (P1–P8) estão fechadas.** Falta consolidar numa ADR (011) e implementar; nenhum código alterado. **Pendente:** ADR da política de replicação, classificação por domínio, `Claim`/`Evidence` (fecha a F0).

## [04-10-2026] - Federação: P6 fechada, cadastro de pares e enrolamento (só documento)

**Decidido (seção 16, P6, e subseção "Cadastro de pares e enrolamento"):** tabela `Par` (nome, `did:key`, endpoint, tipo próprio/terceiro, estado) fundida com `Maquina` (o roteador de LLM lê dos pares; `registrar_maquina` some); **enrolamento por convite** (token de uso único + DID + endpoint) com **conferência da impressão digital pelo administrador nos dois lados**, sem confiar no primeiro contato; **tipo atribuído localmente por cada lado** (a prova de "mesmo dono" é humana); revogar não recolhe cópias; rotação por evento assinado pela chave antiga. Alcançabilidade entre instâncias fica para a F2.

**Também registrado (P3):** o domínio de origem **não** é condição de replicação na v1; o caso "tudo de `bancodobrasil.com.br` é `confidencial`" vira uma **regra de classificação por domínio na captura** (só sobe o nível, casa por sufixo, não reclassifica o existente), funcionalidade à parte **ainda não implementada**.

**Pendente:** P1, P2, P4, P5, P7, P8; classificação por domínio; nenhum código alterado.

## [04-10-2026] - Federação: P3 fechada, motor de regras de replicação (só documento)

**Contexto:** a proposta de uma tabela de tetos de classificação por tipo de par foi corrigida (mandar `interno` a terceiro contradiz a definição do nível) e, a pedido do usuário, substituída por um **motor de regras** onde se acrescentam regras (ex.: um notebook recebe `confidencial` e outro não).

**Decidido (seção 16, P3, e subseção "Motor de regras de replicação"):** três camadas — pisos não editáveis (`interno` nunca sai para terceiro; `restrito`/`confidencial` a terceiro só por concessão explícita; rótulo desconhecido = `confidencial`; receptor não rebaixa), padrões editáveis (própria recebe tudo; terceiro recebe `público`) e regras do usuário; **negar vence**, sem prioridade, e negação por padrão; regras valem nos dois lados; condições da v1: par, nível, tipo de objeto, espaço e validade (domínio de origem fica como extensão não decidida); função pura separada do `policy_engine`, avaliador próprio, regras em tabela auditada. Depende do cadastro de pares (P6).

**Pendente:** P1, P2, P4–P8; nenhum código alterado.

## [04-10-2026] - Remoção da topologia `compute` do cluster

**Contexto:** o usuário não usa nenhuma máquina como `compute` e decidiu que toda máquina é uma instância completa (dado só se move pela federação, então não há duplicação cega); máquinas muito fracas (VPS de 1 GB) ficam como borda, a definir depois.

**Removido:** `docker-compose.worker-node.yml`, `docker-compose.no-infraestrutura.yml`, `scripts/entrar_no_cluster.py`; os endpoints `GET /cluster/api/v1/status/` e `POST /cluster/api/v1/join/`; as configurações `CLUSTER_JOIN_SECRET`, `CLUSTER_HOSPEDA_INFRA` e `CLUSTER_VPN_BIND_IP`; os campos `Maquina.modo` e `Maquina.hospeda_infra_compartilhada` (migration `cluster.0007`); `--modo` do `registrar_maquina`; sete testes (suíte com 197 passando).

**Mudou de comportamento:** o catch-up do pipeline (`scan_unprocessed_documents`) agora roda sempre (antes só com `CLUSTER_HOSPEDA_INFRA=true`); o autorregistro da instância não depende mais de hospedar infra; `_eh_local` do roteador identifica a máquina local só pelo apelido (`CLUSTER_LOCAL_APELIDO`) ou por `CLUSTER_MACHINE_ID`.

**Docs:** `escala-multimaquina.md` reescrito (só roteamento de LLM, heartbeat, registro manual de peers), `deploy.md`, `.env.example`, roadmap, ADR 006 (emenda), ADR 008, ADR 010, `federacao.md`, `perfis-de-implantacao.md`. O `diario.html` (histórico) não foi alterado.

**Fica:** o `EventoReplicacao`, o endpoint `/cluster/api/v1/replicacao/eventos/` e `signals_replicacao` (superados, a remover quando a federação os substituir); `CELERY_QUEUES` ainda é lida pelo heartbeat (`filas`), mas nada mais a define; `CLUSTER_MACHINE_TOKEN` só serve ao endpoint de replicação. **Invalida dados existentes:** nenhum além das colunas removidas de `Maquina` (migration aplicada no banco de desenvolvimento).

**Pendente:** cadastro de pares para o roteamento de LLM é manual em cada instância (`registrar_maquina`) até haver o cadastro de pares da federação; teste real do gateway entre duas máquinas continua em aberto.

## [04-10-2026] - Documentação e site revisados depois do cadastro aberto (só documento e HTML)

**Contexto:** o dono pediu a documentação necessária, incluindo o site, antes do commit e do push.

**Documentação:** o **contrato para automação** do `deploy.md` estava incompleto e foi corrigido — agora lista `FIELD_ENCRYPTION_KEY` (definir **antes do primeiro `up`**), as duas formas de ter o administrador (o deploy cria o dono com os valores que o dono escolher, ou o primeiro cadastro), `FEDERACAO_ENDPOINT_ANUNCIADO`, `CLUSTER_LOCAL_APELIDO`, `OLLAMA_DATA_DIR_HOST`, o `worker-ollama` na verificação e o aviso de atualização (as migrations removem tabelas antigas do cluster; as máquinas só se enxergam de novo depois de enroladas como pares). **`README.md`:** passo novo "crie a sua conta (a primeira vira administradora)", o "zerar" e a dica do superusuário deixaram de mandar rodar `createsuperuser`. **`AGENTS.md`**, que descreve o repositório para outros agentes e estava sem tudo o que foi construído hoje, ganhou os módulos novos no mapa, quatro regras novas (não criar `Claim` direto, não expor o Ollama, não afirmar no site mais do que o código faz, compatibilidade não é requisito) e a dica do container desatualizado.

**Site:** `index.html` ganhou em *Funciona hoje* o item do cadastro aberto (com o limite dito); `diario.html` ganhou a **entrada 36** ("O cadastro que fechava a porta para o próprio dono"), também como rascunho; `manifesto.html` foi lido por inteiro e só tem valores e visão, sem afirmação de status — **nada a corrigir**.

**Limites:** o visual do site **continua sem ser verificado num navegador** (só o balanceamento das tags). As **sete** entradas novas do diário (30 a 36) são rascunhos escritos por mim a partir do histórico e levam a assinatura do dono; o push as publica como estão, com os comentários `RASCUNHO` (invisíveis na página) ainda no HTML.

## [04-10-2026] - Cadastro aberto, e o primeiro usuário vai direto ao cadastro e vira o administrador (reverte a regra 4 da ADR 007)

**Contexto:** nos hosts persas, antares e celtas o dono não conseguia criar usuário: o cadastro respondia "não aceita novos cadastros". A causa era o desenho da ADR 007: a role de deploy (fora deste repositório) preenche `DJANGO_SUPERUSER_*`, o `bootstrap_instancia` criou o usuário `bonafe`, e o `/registro/` fecha (403) depois do primeiro usuário. O dono disse que isso estava errado em três pontos: **o `bonafe` não deveria existir** (ele mesmo faria o primeiro cadastro), **o primeiro usuário deve ir direto ao cadastro** sem clicar em "cadastro", e **o cadastro não deve fechar** — outra pessoa que entrar se cadastra e não vê as coisas dos outros até ter permissão.

**O que mudou**
- **`REGISTRO_ABERTO` é `true` por padrão em qualquer ambiente** (`config/settings/base.py`). `false` continua existindo como **travão opcional**: fecha o cadastro assim que existe o primeiro usuário (o primeiro cadastro passa mesmo com o travão ligado).
- **`EntrarView`** (`apps/accounts/views.py`): numa instância **sem nenhum usuário**, `/entrar/` — e, por redirecionamento, qualquer página protegida — vai direto a `/registro/`. A tela de cadastro avisa que a primeira conta será o **administrador** (e esconde o "Já tem conta?"); depois do primeiro usuário mostra que a conta tem uma organização só dela e que ela não vê os dados dos outros até receber permissão.
- **`bootstrap_instancia`:** `DJANGO_SUPERUSER_*` é **opcional**; sem as três nada é criado e o comando só informa que o primeiro cadastro será o administrador (antes era um aviso de erro). `.env.example` deixa as três vazias (a senha não é mais `CHANGE_ME`).
- **Cluster com várias organizações:** com o cadastro aberto, uma instância passa a ter uma organização por pessoa, e o autorregistro da máquina local só funcionava com **exatamente uma**. Agora a máquina vai para a organização do **administrador da instância** (o superusuário mais antigo, `pares.organizacao_da_instancia`); sem superusuário e com várias organizações continua exigindo `CLUSTER_MACHINE_ID`. Sem isso, o controle dos modelos do Ollama e os pares teriam parado de funcionar assim que alguém se cadastrasse.
- **Documentação:** emenda na **ADR 007** (a regra 4 original ficou riscada, com o histórico), `deploy.md` (seção "Cadastro de usuários" reescrita, com os riscos e como corrigir um usuário que não deveria existir), `autenticacao.md`, `web.md`, `CLAUDE.md`, `.env.example` e `escala-multimaquina.md`.
- **Testes:** `tests/test_cadastro_aberto.py` (13: redirecionamento ao cadastro, aviso do primeiro usuário, primeiro cadastro superusuário, segundo cadastro com organização própria e **sem acesso** ao artefato, às telas do cluster e às rotas de modelos do primeiro, travão, bootstrap sem variáveis) e um teste novo de autorregistro com várias organizações.

**Riscos assumidos (ditos na ADR e no `deploy.md`)**
- **Janela de corrida:** quem abrir a instância primeiro vira administrador; o primeiro cadastro deve ser feito **antes** de expor a porta.
- **Cadastro aberto = qualquer pessoa que alcance a porta cria conta e organização** e usa armazenamento e processamento, **inclusive o LLM**, se houver chave global (`ANTHROPIC_API_KEY`) e o usuário marcar o dado como público e permitir LLM externo (o padrão `restrito` bloqueia). Na tailnet isso é contido; **num host público, use `REGISTRO_ABERTO=false`**.
- O isolamento entre os usuários é o que já existia (por organização, `orgs_do_usuario`); este trabalho **não** o auditou de novo além dos testes acima.

**O que NÃO foi feito**
- **Os hosts persas, antares e celtas não foram tocados.** Neles o `bonafe` continua existindo, e o código novo ainda não está lá. Para o dono ser o administrador: atualizar o código; e **ou** entrar como `bonafe` e usar essa conta, **ou** apagar o `bonafe` (apaga também a organização e os dados dele) e fazer o primeiro cadastro, **ou** cadastrar a conta certa e promovê-la a superusuário. Os comandos estão no `deploy.md`. **A role de deploy (fora deste repositório) pode criar o dono ou não — é decisão do dono, e o código suporta as duas formas:** definindo as três `DJANGO_SUPERUSER_*` com o que o dono escolher, o bootstrap cria o administrador; deixando-as vazias, o primeiro cadastro é o administrador. Se o dono quer ser o primeiro cadastro, a role **não** deve defini-las (foi o que criou o `bonafe` com senha aleatória que ele não conhecia).
- A corrida entre dois "primeiros cadastros" simultâneos não foi tratada (cada um veria `User.objects.exists()` falso); é a mesma janela de antes, só que agora aceita.

## [04-10-2026] - Site atualizado contra o código (só HTML estático; nada publicado)

**Contexto:** o dono pediu para atualizar o site com base nas decisões tomadas. A verificação mostrou que o site não refletia o que existe e, na jornada, afirmava coisas que o código não sustenta. Escopo escolhido por ele: alinhar à realidade; página inicial, jornada, diário (rascunhos, uma entrada por tema), sobre e contribua.

**Achados**
- **`jornada.html`** tinha dois trechos falsos: um marco "✓ Concluído" dizia que buscar processos e buscar notícias estavam operacionais e que o ciclo de inteligência funcionava de ponta a ponta (os agentes têm 6 linhas cada, essas duas ferramentas 3, e `/investigar` ainda responde "em desenvolvimento"); e a Fase 0 falava em MinIO (hoje é Garage, ADR 008) e em "agentes operacionais".
- **`index.html`:** *não* afirmava nada falso — os 8 itens usavam a classe `ri-check`, que desenha um **círculo vazio**. Eu cheguei a dizer ao dono que a página os marcava como concluídos; era erro meu (concluí pelo nome da classe, sem ver como renderizava) e foi corrigido na hora. O problema era outro: a lista não distinguia o que existe do que é meta e não mostrava nada do que existe de fato.
- **Não há código de chat nem de voz** (conferido por busca; só existem as especificações). O `diario.html` tinha 29 entradas e a última era de 17/09, com 36 commits sem registro desde então.

**O que mudou**
- **`index.html`:** "Onde estamos" em três grupos — *Funciona hoje* (marcador cheio com ✓), *Em construção* (meio cheio) e *Planejado* (círculo vazio) —, cada item com seus limites na própria linha; nota explicando a regra; nota no "Como funciona" de que os sete agentes são o **desenho**. Estilos novos `.ri-done`, `.ri-wip`.
- **`jornada.html`:** o marco "Primeiros componentes" virou "Esqueleto do sistema e primeira ferramenta" (sem as afirmações falsas); a Fase 0 foi reescrita (Garage; agentes e ferramentas ditos como esqueletos); dois marcos novos — *Maio a setembro de 2026: captura, extração e observabilidade* (primeira versão concluída) e *Outubro de 2026: várias máquinas e federação* (em andamento, com o que **não** existe dito às claras); a Fase 2 deixou de prometer Neo4j.
- **`sobre.html`:** o "Onde estamos" deixou de falar em "primeiros agentes... operacionais".
- **`contribua.html`:** revisada; nada falso encontrado, **não alterada**.
- **`diario.html`:** 6 entradas novas (30 a 35), de 3 e 4 de outubro: implantação por instância (ADR 007 a 009); federação por log assinado (ADR 010); motor de regras e classificação por domínio (ADR 011); remoção do modo `compute` e o que ele escondia; controle do Ollama pela tela e o que "próprio" significa (ADR 012); e a verificação do site, incluindo o erro acima. **Cada uma tem um comentário `RASCUNHO` e leva a assinatura "Bonafé & Claude Code": o texto é para o dono revisar, ajustar e remover o aviso antes de publicar.**

**Regra adotada para o site:** um item só entra em "Funciona hoje" quando existe e roda; o resto é meta e diz que é (registrada no `CLAUDE.md`).

**Limites:** não abri as páginas num navegador — o HTML foi conferido só por balanceamento de tags e o CSS novo por leitura, então o **visual não foi verificado**. As datas do marco "Maio a setembro" vêm das entradas do próprio diário. O que o texto afirma sobre o controle de pares repete os limites já conhecidos (nunca testado entre máquinas reais). `manifesto.html` não foi lido por completo; a busca por afirmações de status nele não achou nada. O site **não foi publicado** (não há deploy aqui; os arquivos só mudaram no repositório).

## [04-10-2026] - Documentação do repositório atualizada (só documento)

**Contexto:** o usuário perguntou se toda a documentação estava em dia. Verificado com buscas, **não estava**: o índice, o modelo de dados, a visão geral, a spec do portal, a autenticação e o README não refletiam nada do que foi construído (ADR 010 a 012, `Claim`/`Evidence`, `Space`, motor de regras, pares, controle dos modelos do Ollama).

**Atualizado:** `docs/especificacao.md` (índice: ADR 006 a 012, `federacao.md`, nova seção "Operação e implantação"); `docs/arquitetura/modelo-de-dados.md` (nova seção com os modelos do conhecimento, da federação e do cluster; campos novos do `DocumentText`; o grafo como **projeção** e o Neo4j como **previsto**; invariantes 6 a 10); `docs/arquitetura/visao-geral.md` (camada transversal "Federação e Pares"; tabela de infraestrutura com o `worker-ollama` e o Neo4j como previsto); `docs/componentes/interfaces/web.md` (apps `cluster` e `federacao`, telas `/cluster/` e `/cluster/pares/`, rotas do canal, papéis já em uso, critérios de aceitação de pares); `docs/seguranca/autenticacao.md` (quinta camada: assinatura Ed25519 entre instâncias, e a autorização por papel com o limite do papel afirmado); `docs/arquitetura/federacao.md` (aviso de que a seção 2 é um instantâneo de 03/10 e o que mudou desde então); `README.md` (Neo4j previsto, roadmap com a federação, seção "Várias máquinas", aviso de que zerar muda o `did:key`).

**Não alterado:** o site (`index.html`, `sobre.html`, `jornada.html`, `contribua.html`, `manifesto.html`), que não é atualizado desde 16/05/2026, e o `diario.html` (histórico do dono, que ainda descreve o modelo `compute` removido). Fica para decisão do dono.

**Limites:** `docs/roadmap.md`, `deploy.md`, `escala-multimaquina.md`, as ADR e o `CLAUDE.md` já tinham sido atualizados junto com o código. Os links relativos dos arquivos tocados foram conferidos por script (nenhum quebrado); o **conteúdo** foi conferido contra o código só por leitura, não por teste.

## [04-10-2026] - Controle de instâncias, Marcos B a F: canal assinado, pull de pares e modelos do Ollama pela tela

**Contexto:** continuação do Marco A (plano aprovado em `.claude/plans/`). O dono quer **ver as máquinas e suas capacidades** e **listar, instalar e remover os modelos do Ollama** (nativo ou container) pelo `/cluster/`, e controlar as outras instâncias do mesmo dono a partir de qualquer uma. Decisões dele: tudo depois do `Par`; só dono/administrador instala e remove; autorização remota automática para pares **próprios**; cada instância **puxa** o estado dos pares; **máquina offline falha na hora**, e a tela a mostra **Offline com as ações desabilitadas**.

**Descoberta no caminho (consequência da remoção do `compute`):** o heartbeat só era emitido e aplicado para a máquina **local**, no banco **local**. Com bancos separados, as capacidades dos pares nunca chegavam ao banco: nem a tela nem o **roteador de LLM** enxergavam os pares. O pull (Marco C) resolve as duas coisas.

**O que foi implementado**
- **B — canal de controle** (`apps/federacao/views_controle.py`, `canal.py`): rotas `ping`, `estado`, e os comandos em `/federacao/controle/v1/`; decorador `par_assinado` (assinatura → par `confirmado`/ativo → limite de taxa → tipo exigido → método → corpo); recusas **uniformes** (404), 403 só a par autenticado sem permissão, 405, 429; respostas assinadas e presas ao nonce. Cliente `chamar_par`: revalida o endereço (anti-SSRF), **não segue redirects**, limita a resposta, exige a assinatura do DID cadastrado.
- **D1 — `ollama_admin`** (`apps/cluster/ollama_admin.py`): listar com detalhes, `ps`, versão, `show`, `pull` em stream **sem timeout total** (só por pedaço lido) e `delete` (manda `model` e `name`, para versões antigas); nomes validados (só o registry oficial por padrão); erros tipados (`ModeloNaoEncontrado`, `DiscoCheio`, `OllamaIndisponivel`).
- **C — inventário e pull dos pares** (`projecao.aplicar_inventario/aplicar_estado_remoto`, `pull.py`, tasks): `MaquinaModeloOllama` ganhou tamanho, digest, família, parâmetros, quantização e `carregado`; `MaquinaStatus`, `ollama_disponivel/versao` e `disco_ollama_livre_gb`; `Maquina`, `pull_falhas` e `ultima_tentativa_em`. O inventário é um **snapshot** (o que sumiu sai; a velocidade aprendida de quem fica é preservada; Ollama fora do ar **não apaga** o que se sabia). Tudo que um par manda é **saneado**; o gateway que ele anuncia só vale se o **host** for o do endpoint conferido. Pull a cada 30 s só de pares **próprios e confirmados**, backoff até 5 min, eventos só nas transições (`cluster.par`).
- **D2 — operações** (`OperacaoModeloOllama`, `operacoes.py`, rotas do canal, serviço `worker-ollama`): **uma ativa por (máquina, modelo)** por índice parcial, teto de 3 por máquina; progresso agregado por camada com throttling; cancelamento cooperativo + `revoke`; varredura de operações travadas; **espelho** com progresso por pull para pares; espaço em disco medido por volume somente leitura (**desconhecido não bloqueia**).
- **E — tela** (`views_modelos.py`, `painel.html`): inventário, instalar, remover, cancelar, tentar de novo; selos Offline / Ollama indisponível / sem Ollama; botões desabilitados com o motivo; catálogo curado + entrada livre; atualização sozinha (3 s com operação ativa, 30 s sem). DOM por `textContent`, nunca `innerHTML`.
- **F — segurança e docs:** `tests/test_seguranca_controle.py` (revogar/rebaixar valem na hora, replay entre rotas, janela, **teste que documenta o limite**: um par próprio comprometido pode afirmar qualquer ator, com rastro no `AuditLog`); **ADR 012**; `escala-multimaquina.md`, `deploy.md`, `.env.example`, `observabilidade.md` (`cluster.par`, `cluster.modelo`), `federacao.md`, `CLAUDE.md`.
- **Correção de defeitos meus** achados ao escrever os testes: a recusa `400` do canal respondia "muitas requisições"; as rotas não restringiam o método HTTP (agora cada rota declara os seus); a auditoria colidia um argumento com uma chave de metadado.
- Testes: `test_canal_controle.py` (27), `test_ollama_admin.py` (47), `test_pull_pares.py` (33), `test_operacoes_modelo.py` (79), `test_cluster_modelos_views.py` (33), `test_seguranca_controle.py` (11). O `conftest` passou a bloquear a rede para o Ollama em todos os testes (menos os do próprio cliente).

**Limites conhecidos**
- **Não testado com duas instâncias reais nem com um Ollama real:** a outra ponta e o Ollama foram simulados nos testes. A tela foi exercitada só com um **DOM simulado** (Node), não num navegador real.
- O papel afirmado pela origem **não é verificável** pelo receptor; "próprio" dá poder (ADR 012).
- Não há aviso de "modelo padrão do projeto" ao remover (nenhuma configuração identifica um) nem como saber se há chamada em voo; só avisa "carregado" e "está em `OLLAMA_MODELOS`".
- O catálogo é curado e **pode estar desatualizado**; tamanhos aproximados.
- Só pares **próprios** são consultados no pull; terceiros não têm nem `ping` agendado.
- `reconstruir_status_maquinas` apaga o estado dos pares (volta no próximo pull).
- O catch-up de espelhos tem um intervalo de 5 s mesmo sem nada ativo (a task sai cedo).
- Compose: o serviço `worker-ollama` é novo e **precisa estar de pé**; os dois workers montam `${DATA_DIR}/ollama` somente leitura.

**Incidente anterior que reapareceu como risco:** o container do portal já enxergou uma cópia antiga do código (ver Marco A); a recriação de `portal`, `worker` e `beat` resolveu. O `worker-ollama` é um container novo que ainda **não foi levantado** nesta máquina de desenvolvimento.

## [04-10-2026] - Controle de instâncias, Marco A: `Par` e enrolamento por convite

**Contexto:** o dono quer controlar os modelos do Ollama das máquinas (nativo ou container) pelos painéis, inclusive de uma instância sobre as outras do mesmo dono. Plano aprovado em `.claude/plans/` (marcos A–F): `Par` + enrolamento → canal assinado → cliente Ollama de administração → pull dos pares → operações de modelo → tela. Este é o **Marco A**.

**O que foi implementado:**
- **`Maquina` vira `Par`** (migrations `cluster.0008–0010`): `eh_local`, `did`, `tipo`, `estado`, `impressao_digital_conferida_*`, `endpoint_controle`, `capacidades_json`, `ultimo_pull_*`; `CHECK`s de uma `Maquina` local por organização e `did` único por organização. `garantir_maquina_local` substitui o autorregistro antigo. O **roteador de LLM só usa pares `confirmado`**.
- **Convite e enrolamento sem TOFU** (`apps/cluster/pares.py`): token de uso único (hash), 24 h, código `ia1.…` mostrado uma vez; B aceita com `POST /federacao/convite/aceitar/` assinado, A responde assinando; cada lado cria o outro `pendente`; **só "Confirmar" depois de conferir a impressão digital leva a `confirmado`**. Mudar tipo/revogar com `AuditLog`. Marcar **próprio** exige confirmação explícita (dá poder sobre os modelos).
- **Primitivas do canal assinado** (`apps/federacao/canal.py`): mensagem `ia-ctrl-v1` com método, rota, timestamp, nonce, origem, **destino** e hash do corpo; janela ±60 s; nonce anti-replay no cache Redis (`CACHES` novo, banco 2), queimado só por requisição autêntica; resposta assinada e presa ao nonce.
- **Anti-SSRF** (`apps/federacao/endpoints.py`): link-local sempre recusado, HTTP só em rede privada/VPN (CGNAT do Tailscale incluso), sem usuário/senha/caminho.
- **Papéis** (`apps/accounts/permissoes.py`): primeiro uso real de `Membership.role` — só OWNER/ADMIN vigentes administram; qualquer membro vê. **Telas** `/cluster/pares/`.
- **Removidos:** `EventoReplicacao` (tabela inclusa), o endpoint de replicação, `signals_replicacao`, `replicacao.py`, `provisionamento.py`, o comando `registrar_maquina`, o `token_hash` e as rotas mortas de `EXEMPT_PREFIXES`.
- **Infra/config:** `infra/caddy/Caddyfile` bloqueia `/federacao/*`; `.env.example` e `docs/deploy.md` (`FEDERACAO_ENDPOINT_ANUNCIADO`, janela de relógio, `CACHE_URL`).
- Testes: `tests/test_pares_enrolamento.py` (93, incluindo "todas as recusas dizem a mesma coisa", replay, token de uso único, destino trocado, SSRF, matriz de papéis, CSRF, isolamento por organização) e `tests/test_permissoes_e_canal.py` (34); suíte completa com **526 passando**.

**Incidente no caminho:** o container do portal passou a enxergar uma cópia **antiga** do código (arquivo de 17:37, ainda com `registrar_maquina.py`), o que fez `makemigrations` dizer "sem mudanças" e um teste de WebSocket falhar. Resolvido recriando `portal`, `worker` e `beat` (`docker compose up -d --force-recreate`). Se acontecer de novo, é o primeiro suspeito.

**Limites conhecidos:** o enrolamento só foi exercitado com a "outra instância" simulada no mesmo processo (chaves distintas, resposta assinada à mão); **não foi testado com duas instâncias reais**. Não existe ainda rota de controle, pull periódico nem cliente de administração do Ollama (Marcos B a F). O nome do par é checado no cadastro, não na conexão. O roteador ainda não tem dados de pares (isso é o Marco C).

**Próximo:** Marco B (rotas de controle assinadas + autorização por tipo e papel) e D1 (cliente Ollama de administração).

## [04-10-2026] - Federação: as máquinas do mesmo dono replicam pelo mecanismo da federação (só documento)

**Contexto:** o usuário perguntou o que já existe para várias máquinas e concluiu que o próximo passo são as regras de replicação. Verificado no código: o cluster (A) tem topologia `compute`, heartbeat e roteamento de LLM pelo gateway (sem teste real entre duas máquinas); a replicação por `EventoReplicacao` só tem o lado que envia, sem filtro, e ninguém consome; instâncias de bancos separados não se conhecem.

**Decidido:** as outras máquinas do mesmo dono replicam **pelo mesmo mecanismo da federação** (par de nível "próprio", espaço próprio). Supera o `EventoReplicacao` (código **não removido**); `apps/cluster` fica com `compute`, heartbeat e roteamento de LLM. Emenda na ADR 010, revisão da ADR 006, e ajustes em `compartilhamento.md`, `escala-multimaquina.md` e `roadmap.md`. `federacao.md` ganhou a subseção "Replicação entre as máquinas do mesmo dono" e a **seção 16** com oito decisões pendentes (P1–P8: níveis de confiança por par, quem decide o que sai, tetos de classificação, granularidade, blobs, enrolamento de máquina própria, organização do objeto importado, auditoria).

**Pendente:** P1–P8; nenhum código alterado.

## [04-10-2026] - Deploy: `FIELD_ENCRYPTION_KEY` e a chave da instância

- `docs/deploy.md` ganhou a seção "Chave da instância (federação)": o `bootstrap` agora cria a chave Ed25519; `FIELD_ENCRYPTION_KEY` deve ser definida **antes do primeiro `up`** (se a cifra mudar depois, a privada fica ilegível e não há comando para recifrá-la ou recriá-la sem perder o DID), perder a privada significa perder a identidade da instância (diferente das chaves de LLM, que se recadastram) e o backup precisa de `FIELD_ENCRYPTION_KEY` **e** do banco. O passo 4 do `bootstrap` foi acrescentado à descrição e `.env.example` ganhou o aviso.
- **Ainda em aberto:** a validação de segredos de produção (`config/segredos.py`) **não** exige `FIELD_ENCRYPTION_KEY`; exigi-la seria o jeito de impedir o erro, mas muda o comportamento de instâncias existentes e não foi feito.

## [04-10-2026] - F0 da federação, passo 2: chave Ed25519 da instância (ADR 010)

**Contexto e motivação:**
- Segundo item da F0: a instância precisa de um par de chaves para, mais adiante, assinar o log de eventos federados. Pela decisão 6 da ADR 010, vive num **app novo**, `apps/federacao`, separado de `apps/cluster`.

**O que foi implementado:**
- **`apps/federacao/did.py`:** `did:key` Ed25519 (multicodec `0xed01` + base58btc), sem dependências além da stdlib. Verificado contra o `did:key` conhecido da chave pública do vetor 1 da RFC 8032 e contra o exemplo do W3C.
- **`ChaveInstancia`** (`federacao_chave_instancia`): `did` (contém a pública, por isso não há campo à parte), `privada_cifrada` (semente de 32 bytes, Fernet de `apps.infrastructure.crypto`), `estado` (ativa/aposentada/comprometida), `criada_em`. Restrição parcial: no máximo **uma** chave `ativa`. Migration `federacao.0001`.
- **`chaves.py`:** `garantir_chave_ativa()` (idempotente; a corrida é resolvida pela restrição e o perdedor relê), `ChaveInstancia.assinar()`, `verificar_assinatura(did, msg, assinatura)` (só com o DID; nunca levanta).
- **`manage.py chave_instancia [--criar]`:** mostra o `did:key`; a privada nunca é exibida. **`bootstrap_instancia`** ganhou o passo 4/4 (cria a chave se faltar; idempotente).
- Testes: `tests/test_federacao_chave.py` (24); suíte completa com 180 passando. Chave criada no banco de desenvolvimento.

**Limites conhecidos (por desenho, ver ADR 010):** a privada está no servidor, cifrada com a chave derivada de `DJANGO_SECRET_KEY` (ou `FIELD_ENCRYPTION_KEY`); **trocar `DJANGO_SECRET_KEY` sem definir `FIELD_ENCRYPTION_KEY` torna a chave ilegível** (`ChaveIlegivel`) — em produção, fixar `FIELD_ENCRYPTION_KEY`. Não há rotação nem eventos `key.*` (dependem do log de eventos, F2); a criação da chave ainda não gera evento nem entrada de auditoria. O `did` não é exposto por HTTP (nenhum `/.well-known` ainda). Instâncias existentes: rodar `bootstrap_instancia` ou `chave_instancia --criar` após aplicar a migration.

**Falta da F0:** ID global e `Claim`/`Evidence` como conceitos de domínio.

## [04-10-2026] - F0 da federação, passo 1: hash de conteúdo do MHTML (ADR 010)

**Contexto e motivação:**
- Primeira entrega de código da federação: o MHTML não tinha identidade de conteúdo. Formato decidido na ADR 010: RFC 6920, `ni:///sha-256;<base64url>`.

**O que foi implementado:**
- **`config/conteudo_hash.py`** (portal) e cópia em **`orchestrator/conteudo_hash.py`** (cada serviço tem seu contexto de build, como `segredos.py`): `hash_ni`, `formato_valido`, `verificar`. Testado com o vetor do próprio RFC.
- **Orchestrator** (`/api/v1/capture/mhtml`): calcula o hash sobre os bytes recebidos, antes de gravar; vai no payload dos eventos `captura.recebida`, `captura.armazenada` e `captura.orfa` (`hash_ni`) e no POST ao portal (`blob_hash`).
- **Portal:** `Artifact.blob_hash` (nulo, indexado, **não único** — duas capturas com bytes idênticos geram dois artefatos com o mesmo hash), migration `0015_artifact_blob_hash`; `ArtefatoCreateAPIView` valida o formato (400 se malformado) e grava. O hash replica aos peers do cluster porque `signals_replicacao` serializa todos os campos.
- **`manage.py calcular_hashes`** (backfill): `--simular`, `--limite`, `--verificar` (reconfere contra o blob atual e relata divergência sem sobrescrever; blob ausente é relatado e não interrompe). Grava com `save(update_fields=["blob_hash"])`, sem alterar `updated_at`.
- Testes: `tests/test_conteudo_hash.py` (21); suíte completa com 156 passando. Migration aplicada e backfill rodado no banco de desenvolvimento (1 artefato, conferido com `--verificar`).

**Limites conhecidos:** o hash é calculado no orchestrator, não na extensão; não cobre a integridade extensão → orchestrator. `orchestrator/sync_minio_postgres.py` (recuperação de órfãos) insere artefatos por SQL sem hash; o `calcular_hashes` os cobre depois. O portal não verifica o hash recebido (não tem os bytes na API); a conferência é o `--verificar`. Não foi feita uma captura real ponta a ponta pela extensão: o orchestrator só foi verificado importando o módulo no container.

**Falta da F0:** ID global, `Claim`/`Evidence` como conceitos de domínio, chave Ed25519 por instância. Rodar `calcular_hashes` em cada instância existente depois de aplicar a migration.

## [04-10-2026] - Federação: as oito decisões da seção 14 fechadas, fonte de verdade = log assinado (só documento)

**Contexto e motivação:**
- Retomada da discussão da seção 14 de `docs/arquitetura/federacao.md`. O usuário questionou se a fonte de verdade não deveria ser um serviço de mensageria (Kafka ou similar) em vez de um log; os motivos dele são replay e verdade imutável.

**Decidido (registrado no documento):** (1) um objeto pode estar em vários espaços; (3) o grafo é projeção, tecnologia (Neo4j ou Postgres) adiada para a Fase 2; fonte de verdade = log de eventos assinados com cadeia de hash por autor, guardado em tabela do Postgres (outbox, `UPDATE`/`DELETE` bloqueados no banco), com projeções reconstruíveis por replay e eventos que guardam resultados; broker só como transporte futuro. Nova subseção "Fonte de verdade e replay". (2) Chaveiro por usuário com chave escolhida por espaço, ligação entre chaves privada por padrão (revelável por opção), revogação por posição na cadeia, chaves privadas e chave de recuperação custodiadas no servidor por enquanto (limitação registrada; evolução na F4). Nova subseção "Chaveiro do usuário". (5) Apagamento só por tombstone; cifra por objeto adiada, com a possibilidade de zerar tudo enquanto o sistema não estiver em produção e nada tiver saído da instância (reavaliar antes do primeiro intercâmbio real). Nova subseção "Apagamento". (8) IDs `urn:uuid:` (origem pelo `author`, não pelo ID) e hash RFC 6920 `ni:` com SHA-256. Nova subseção "IDs e hash". (4) Logs por autor na v1, sem DAG, e (6) app novo para a federação separado de `apps/cluster`, com revisão da ADR 006: recomendações da análise adotadas. (7) `Space`/"Espaço" e namespace `ia:` = `https://w3id.org/inteligencia-aberta/v1#` via w3id.org, contexto embutido nas instâncias, registro no w3id adiado. Nova subseção "Nome e namespace"; URI do exemplo da seção 8 atualizada.

**ADR:** escrita em `docs/arquitetura/decisoes/010-federacao-por-log-assinado.md`.

**Revisões:** ADR 006 ganhou status "parcialmente revisado" e a seção "Revisão de 2026-10-04" (`Projeto` vira `Space`; dado entre donos vai pela federação; motor de posicionamento e roteador de LLM ficam no cluster; reciprocidade de LLM entre donos segue em aberto). `docs/roadmap.md`: Neo4j na Fase 2 virou "grafo como projeção, tecnologia a decidir"; `Projeto` virou `Space`; novo item de federação F0–F5 na Fase 4.

**Também corrigido:** `docs/seguranca/compartilhamento.md`, `docs/visao/perfis-de-implantacao.md` (D5) e uma frase de `docs/operacao/escala-multimaquina.md` trocaram `Projeto` por `Space`.

**Pendente:** registrar o namespace no w3id.org perto da F1. Nenhum código alterado.

## [03-10-2026] - Análise arquitetural da federação entre instâncias (só documento)

**Contexto e motivação:**
- O usuário pediu uma análise (sem implementar) de como instâncias independentes do Inteligência Aberta podem se federar: por contexto/regras, com protocolo desacoplado da implementação, RDF/JSON-LD, PROV-O, identidade federada, blobs por hash, eventos, conflitos e segurança. Resultado em `docs/arquitetura/federacao.md`.

**O que contém:** inventário do que existe e do que acopla (incluindo três premissas que o repositório contradiz: Neo4j só existe em documentação, o object store já é o Garage e o MHTML não tem hash), avaliação dos padrões (adotar/adiar/descartar), arquitetura em 10 camadas, conceito de espaço, menor núcleo da v1, exemplo completo MHTML→notícia→alegação em JSON-LD/PROV-O, descoberta por hash, tratamento de conceitos desconhecidos, segurança, conflitos e roadmap F0–F5.

**Pendente:** oito decisões em aberto (seção 14) — granularidade do espaço, identidade do usuário, papel do Neo4j, vários escritores, apagamento legal, separação cluster×federação, nomes e formato de IDs/hash. Nenhum código foi alterado; a avaliação dos padrões usou conhecimento prévio, sem consulta à web.

## [03-10-2026] - Chave de LLM (Claude) por organização, cifrada em repouso

**Contexto e motivação:**
- A única chave existente era `ANTHROPIC_API_KEY` no `.env`, global à instância: todas as organizações gastavam a mesma conta. O `LLMProvider` (infrastructure) existia só no admin, sem uso, e o `api_key_encrypted` era um `CharField` sem cifra, contrariando a spec (`web.md` §4.8). Escopo escolhido pelo usuário: uma chave por organização (sem registro completo de providers/ChatGPT).

**O que foi implementado:**
- **`apps/infrastructure/crypto.py`:** Fernet; chave `FIELD_ENCRYPTION_KEY` (opcional) ou derivada de `DJANGO_SECRET_KEY`. `cryptography` explícito no `requirements.txt`.
- **`LLMProvider`** (migration `0002_vendor_e_chave_cifrada`): campo `vendor` (só `anthropic`), `api_key_encrypted` vira `TextField` (o token Fernet estourava 255), `set_api_key`/`get_api_key`/`tem_api_key` e `clean()` que recusa `restrito`/`confidencial` em provider externo.
- **Admin:** formulário com chave só de escrita (em branco mantém, caixa remove); coluna "Chave".
- **Resolução:** `llm_common._chave_anthropic(tenant_id)` — provider ativo/externo/anthropic da organização, senão `ANTHROPIC_API_KEY`; `gerar_texto` e `llm_classify` passam o `tenant_id`.
- **Docs/config:** `web.md` §4.8, `autenticacao.md`, `deploy.md` (inclui `FIELD_ENCRYPTION_KEY` para a automação e o backup), `.env.example`.

**Como foi validado:** suíte do portal completa no container (135 testes), com `tests/test_chaves_llm.py` cobrindo cifra, chave de cifra trocada, invariante do externo, formulário do admin (grava cifrado, mantém, remove), precedência organização > `.env`, provider inativo/de outra organização, chave ilegível e `gerar_texto` com o cliente criado com a chave da organização.

**Não testado / pendente:** tela do admin no navegador; chamada real à API da Anthropic com a chave da organização; `model_name` do provider ainda não define o modelo (só a chave é usada); sem rotação de `FIELD_ENCRYPTION_KEY` (trocar exige recadastrar); `save()` direto não roda `clean()`; sem cota/limite de custo por organização; sem adaptador OpenAI/ChatGPT; linhas antigas de `api_key_encrypted` (texto puro, nunca usadas) ficam ilegíveis e caem na chave global.

## [03-10-2026] - Chamada entre nós pelo gateway autenticado (pendência do ADR 009)

**Contexto e motivação:**
- O roteador de LLM chamava o `ollama_endpoint` do peer diretamente, o que obrigava a expor um Ollama sem autenticação na VPN. A pendência estava registrada no ADR 009 ("Chamada entre nós pelo gateway autenticado"); decisão do usuário: implementá-la, em quatro passos (campo, roteador, cliente, documentação).

**O que foi implementado:**
- **Modelo/config:** `Maquina.gateway_endpoint` (migration `0006_maquina_gateway_endpoint`), setting `LLM_GATEWAY_ENDPOINT_ANUNCIADO` (`.env.example`); anunciado no autorregistro e aceito em `criar_maquina`, no join, em `registrar_maquina --gateway-endpoint` e em `scripts/entrar_no_cluster.py --gateway-endpoint`; visível no admin.
- **Roteador (`apps/cluster/llm_router.py`):** `ExecucaoOllama.gateway`, preenchido só para peer com gateway quando esta instância tem `LLM_GATEWAY_TOKEN`; máquina local e instalações sem token seguem direto no Ollama. Peer que só anuncia gateway também é candidato. `escolher_execucao(..., permitir_gateway=False)` zera o campo.
- **Cliente (`ollama_client.py`):** `_chamar(gateway=...)` faz `POST <gateway>/v1/chat/completions` com `Bearer` e converte a resposta ao formato do Ollama; `llm_common.gerar_texto` e `gateway.py` passam o gateway escolhido. Sem fallback para o Ollama direto do peer.
- **Sem laço:** o cliente envia `X-Cluster-Encaminhado`; o gateway que o recebe executa localmente e não reencaminha (um salto só).
- **Velocidade aprendida:** o gateway devolve `x_ollama.eval_duration_ns` (extensão fora da API OpenAI) para o tokens/segundo do peer continuar sendo aprendido.
- **Caddy:** `infra/caddy/Caddyfile` passa a responder 404 para `/v1/*` — antes o gateway ficava alcançável pela internet no host público (só o token o protegia), porque o Caddy mandava todo o resto ao portal.
- **Docs:** ADR 009 (pendência virou seção), `docs/operacao/escala-multimaquina.md`, `docs/deploy.md` (nova seção "LLM por nó e entre nós": modos do Ollama por host; token do gateway por cluster, endpoint e `BIND_ADDR` por host).

**Como foi validado:** suíte do portal completa no container (122 testes), incluindo novos testes de roteador (peer com/sem token, máquina local, peer só com gateway), cliente (conversão, falha → `OllamaIndisponivel`, telemetria), gateway (pedido encaminhado não reencaminha; sem cabeçalho encaminha) e `gerar_texto`.

**Não testado / pendente:** chamada real entre duas máquinas pela VPN; `gateway_endpoint` só é gravado na criação da `Maquina` (em uma já registrada, editar no admin — o heartbeat não sincroniza); a mesma chamada gera telemetria no chamador e no gateway do peer (finalidade `GATEWAY_EXTERNO`); `num_thread`/`num_ctx` ficam a cargo do peer; política de classificação no destino; streaming; registro de providers de LLM.

## [03-10-2026] - Implantação por instância: compose de produção, HTTPS/Caddy, registro fechado, bootstrap e segredos

**Contexto e motivação:**
- O dono implanta o projeto na "rede_papagaio" (5 hosts numa tailnet, Ansible em outro repositório, uma instância por host com segredos próprios; um host com IP público e domínio). O repositório só tinha modo de desenvolvimento. Decisões e alternativas em `docs/arquitetura/decisoes/007-implantacao-por-instancia.md`.
- Decisões validadas com o usuário: HTTPS em todos os hosts (`tailscale serve` nos só-tailnet, Caddy no host público), captura remota pela extensão necessária, registro fechado após o primeiro usuário.

**O que foi implementado:**
- **Compose:** `docker-compose.prod.yml` (restart, `BIND_ADDR`, `DATA_DIR`, serviço `bootstrap`, serviço `caddy` no profile `publico`); healthchecks e `service_healthy` no base; portas movidas do base para o override (dev) e o prod; `MINIO_IMAGE`.
- **Portal:** `/health` (checa o banco); `production.py` com `TLS_MODE`, `ALLOWED_HOSTS` multi-valor (inclui `portal`/`localhost`, antes o orchestrator e o MCP levariam 400) e `CSRF_TRUSTED_ORIGINS`; `config/segredos.py` valida segredos na subida; registro fecha após o primeiro usuário (`REGISTRO_ABERTO`); comando `bootstrap_instancia`; `apps/accounts/services.criar_organizacao_individual`.
- **Orchestrator/MCP:** `/health` com `INSTANCIA_NOME`/`IA_VERSION`; validação de segredos (só em produção); CORS do orchestrator por `CORS_ALLOWED_ORIGINS`.
- **Extensão:** campo "Instância (URL)" no popup, `optional_host_permissions`.
- **Caddy:** `infra/caddy/Caddyfile` publica só o portal e `/api/v1/capture/*`; bloqueia `/artifacts/api/v1/artefatos/` e `/eventos/api/v1/ingest/`.
- **Docs:** `docs/deploy.md` (inclui contrato para automação), ADR 006; atualizados `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/seguranca/autenticacao.md`, `docs/componentes/extensao-navegador.md` e `docs/componentes/interfaces/web.md`.

**Como foi validado:** stack de produção subida do zero numa cópia isolada (tudo `healthy`, `bootstrap` com saída 0, segunda execução sem alterações, registro 403 após o primeiro usuário, `.env.example` cru recusado com mensagem clara, Caddy testado com `localhost`); suíte do portal com 33 testes passando.

**Não testado / pendente:** Let's Encrypt com domínio real, `tailscale serve`, extensão no Chrome real, macOS/Colima e Rocky; rate limit na borda, backup/restore, imagens em registry, S3/Postgres externos, GPU, healthcheck de worker e beat.

## [12-07-2026] - Documentação Swagger no Portal + MCP publicado para fins didáticos

**Contexto e motivação:**
- Orchestrator e MCP são FastAPI e ganham Swagger UI automaticamente; o Portal (Django) não tinha nenhuma documentação interativa dos endpoints REST (`/api/v1/token/`, `/api/v1/token/refresh/`). Para a entrega da matéria de API e para onboarding de desenvolvedores, faltava paridade.
- O Swagger do MCP também não era alcançável do host — a porta 8002 tinha sido deliberadamente deixada não-publicada na sessão de autenticação anterior (mitigação para "MCP sem autenticação"). Publicar a porta só para mostrar `/docs` reabriria as ferramentas (`/tools/cnpj`, `/tools/processos`, `/tools/noticias`) sem proteção nenhuma se nada mais mudasse.
- Decisão validada com o usuário (duas perguntas explícitas): (1) documentação pública, sem exigir login/token — só descreve o formato da API, não expõe dado; chamadas reais continuam autenticadas; (2) publicar a porta do MCP **e** adicionar um token dedicado às ferramentas, para que expor `/docs` não signifique expor as ferramentas.

**O que foi implementado:**

### Portal — `drf-spectacular`
- **`requirements.txt`:** `drf-spectacular==0.27.2`.
- **`config/settings/base.py`:** `drf_spectacular` em INSTALLED_APPS; `REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"]`; bloco `SPECTACULAR_SETTINGS` com `SERVE_PERMISSIONS=[AllowAny]` e `SERVE_AUTHENTICATION=[]` (documentação pública, independente do `DEFAULT_PERMISSION_CLASSES=IsAuthenticated` global do DRF).
- **`config/urls.py`:** rotas `/api/schema/` (OpenAPI cru), `/api/docs/` (Swagger UI), `/api/redoc/` (ReDoc).
- **`apps/accounts/middleware.py`:** as três rotas entram na allowlist do `LoginRequiredMiddleware` (documentação não exige sessão).
- **`templates/accounts/dashboard.html`:** novo card "API do Portal" apontando para o Swagger; cards de Orquestrador e MCP mantidos/adicionados.
- Nota técnica: `ArtefatoCreateAPIView` é `django.views.View` puro (não DRF), então não aparece no schema — só os dois endpoints de token são introspeccionados automaticamente.

### MCP — porta publicada + token de ferramenta
- **`services/mcp/main.py`:** `require_mcp_token` (dependência FastAPI, `hmac.compare_digest` contra `MCP_API_TOKEN`) aplicada via `dependencies=[Depends(...)]` nas três rotas `/tools/*`; sem token válido → 401. `/docs`, `/openapi.json` e `/health` continuam sem proteção. FastAPI documenta o header `X-Mcp-Token` automaticamente na spec (por vir de um parâmetro `Header()`), então o Swagger já mostra que ele é obrigatório.
- **`docker-compose.yml`:** `mcp` ganha `ports: ["8002:8002"]`.
- **`.env.example` / `.env`:** `MCP_API_TOKEN` (segredo distinto do `INTERNAL_API_TOKEN` — fronteira diferente: chamada de ferramenta, não criação de artefato).

### Docs
- **`docs/seguranca/autenticacao.md`:** nova seção 4 (token de ferramenta do MCP); nova seção "Documentação da API (Swagger) — pública por decisão" explicando o porquê é seguro; tabela de segredos com `MCP_API_TOKEN`; item de hardening pendente da seção do MCP atualizado (token existe agora, falta rate limit).
- **`docs/componentes/interfaces/web.md`:** §5 lista as novas rotas; §6 ganha uma linha sobre o token do MCP e sobre a documentação pública.

**Testes realizados:**
- Rebuild de `portal`, `worker`, `beat` (mesma imagem/`requirements.txt` — Celery também carrega `INSTALLED_APPS` no boot) e `mcp`; `up -d` para recriar com a nova porta publicada.
- `curl`: `/api/docs/`, `/api/schema/`, `/api/redoc/` no portal → 200 sem cookie de sessão; `/docs` do MCP → 200 sem token; `/tools/cnpj/...` sem token → 401; com token errado → 401; com `MCP_API_TOKEN` correto → 200 (chamada real à BrasilAPI funcionou); `/health` do MCP → 200 sempre.
- Schema do portal (`Accept: application/json`) lista exatamente `/api/schema/`, `/api/v1/token/`, `/api/v1/token/refresh/` — confirma que `ArtefatoCreateAPIView` não aparece, como esperado.
- `openapi.json` do MCP confirma que `x-mcp-token` aparece como parâmetro nas três rotas de ferramenta e não aparece em `/health`.
- Logs de `worker`/`beat`/`mcp` sem erro de import após o rebuild.

**Status Atual:**
- Portal, orchestrator e MCP têm documentação interativa acessível sem autenticação; as chamadas reais de cada API continuam exigindo seu mecanismo próprio (sessão, JWT, token de serviço, token de ferramenta).

**Próximos Passos Sugeridos:**
- Rate limit no `/tools/*` do MCP (porta agora exposta ao host permite tentativas de força bruta contra `MCP_API_TOKEN`, sem limite hoje).
- Quando o orchestrator implementar as chamadas reais ao MCP (hoje é só um TODO em `coletor.py`), usar `MCP_API_TOKEN` no header `X-Mcp-Token`.

---

## [12-07-2026] - Correção: primeiro usuário volta a virar superusuário (era especificação, não bug)

**Contexto e motivação:**
- Na sessão da camada de autenticação (entrada abaixo, mesma data), a auto-promoção do primeiro cadastro a `is_superuser`/`is_staff` foi removida por interpretação equivocada: parecia escalada de privilégio por corrida num registro aberto. O usuário corrigiu — essa promoção **é especificação intencional**: o primeiro usuário virar superusuário é o mecanismo de bootstrap do admin, para que quem instale o sistema não precise rodar `manage.py createsuperuser` separadamente. É um trade-off aceito (janela de corrida numa instância recém-implantada), não um descuido.
- Importante distinguir de um mecanismo diferente que **não** foi restaurado: o fallback "primeiro usuário" que existia em `ArtefatoCreateAPIView` (`_resolve_user`/`_resolve_org`, removido na mesma sessão anterior) atribuía a dono de artefato ao usuário mais antigo do banco quando `user_id` vinha vazio — isso é resolução silenciosa de identidade numa API de escrita, não bootstrap de admin via formulário de registro. Esse permanece removido: contradiz o fluxo de JWT (a extensão não envia mais `user_id`/`tenant_id`; a identidade vem das claims do token validado pelo orchestrator).

**O que foi implementado:**
- **`apps/accounts/views.py` `registro()`:** restaurado — `if not User.objects.exists(): user.is_staff = True; user.is_superuser = True` antes de salvar o usuário. Comentário no código explica o trade-off.
- **`docs/seguranca/autenticacao.md` § Registro e superusuário:** reescrito para descrever o comportamento como intencional, com o trade-off e a mitigação operacional (criar a conta admin antes de expor a porta do portal publicamente).
- **`docs/componentes/interfaces/web.md` §7.1:** fluxo de registro passou a mencionar explicitamente a promoção do primeiro usuário.

**Testes realizados:**
- `python -m py_compile` limpo em `apps/accounts/views.py`.
- Não recriei o banco do zero para testar a promoção do primeiro usuário — faria isso apagar o usuário real já cadastrado no ambiente (`bonafe`). A lógica restaurada é idêntica à que existia antes da remoção (mesma condição, mesmo efeito) — revisão de código considerada suficiente.
- Confirmado via logs do `portal` que o `runserver` recarregou `apps/accounts/views.py` automaticamente (hot-reload).

**Status Atual:**
- Especificação e código alinhados: primeiro usuário do sistema vira superusuário via `/registro/`; fallback de identidade não verificada na API de artefatos continua removido.

---

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
