# ADR 018 — Federação: o que os casos de uso reais pedem além do motor atual

**Status:** **proposta** — 2026-10-11, com o **item 2 aceito e implementado em 2026-10-11** (concessão em lote por espaço). Os demais itens seguem sem decisão; cada item traz uma recomendação para o dono aceitar, mudar ou recusar. (A ADR 017 está reservada em `docs/arquitetura/publicacao-estatica.md`.)

## Contexto

Ao descrever os casos de uso (máquinas de casa, irmão, tio, órgãos públicos, fotos), conferiu-se o motor de regras da [ADR 011](011-politica-de-replicacao.md) contra eles. O que cabe hoje: máquinas próprias recebem todos os níveis. O que **não** cabe, e por quê, está abaixo. Fotos **não existem** como tipo de dado: o sistema captura MHTML (`Artifact.Type`: pessoa, empresa, documento, processo, endereço, evento) e não há tags.

Referência dos níveis: `docs/seguranca/classificacao.md`. Nota de vocabulário: o nível que "fica dentro da organização" é o `interno`; `restrito` (dados pessoais) e `confidencial` (altamente sensível) podem sair, mas só por decisão explícita.

## Questões e recomendações

**1. Tag/etiqueta como condição de regra.** Hoje as condições são par, tipo de par, nível, tipo de objeto, espaço, objeto e validade.
*Recomendação:* **não criar condição "tag" na v1.** Usar o `Space` como a etiqueta ("família", "família-tio"): um objeto em vários espaços já é suportado e a condição `espaco_urn` já existe. A tag da interface seria um nome amigável de um espaço. Evita um segundo mecanismo de agrupamento.

**2. Piso de `restrito`/`confidencial` para terceiro exige concessão por objeto.** Isso impede "todas as fotos `restrito` do espaço família vão para o irmão, sempre".
**Aceito e implementado (2026-10-11).** *Recomendação adotada:* **concessão em lote por espaço**: uma regra *permitir* com `espaco_urn` + `par_ref` + `valida_ate` obrigatórios (validade longa, p. ex. 1 ano, renovável) conta como concessão para todo objeto **que estiver no espaço**. Mantém os três elementos do piso (explícita, com par, com validade, revogável) e continua sendo ato humano. **Decisão de segurança do dono**: afrouxa "por objeto" para "por espaço".

**3. Exceções por pessoa ("sem as fotos da tia").** Sem condição por pessoa.
*Recomendação:* resolver **pela composição dos espaços** (espaço curado; negar vence com `objeto_urn` para o caso pontual). Condição por pessoa/entidade só quando houver entidades resolvidas (correlacionador).

**4. Órgãos públicos.** Faltam (a) identidade de organização entre instâncias e (b) condição por assunto/entidade. A concessão humana e revogável já existe.
*Recomendação:* **adiar**; é a pré-condição já listada na ADR 011 ("equipes entre instâncias ficam fora por enquanto"). Não desenhar agora.

**5. Política de réplicas (disco desigual: 15 TB × máquinas pequenas).** A ADR 011 só tem "espelho em segundo plano entre as próprias, até o limite de disco".
*Recomendação:* separar **o que pode ir** (regras, já existem) de **onde fica** (nova política de posicionamento, só entre pares próprios): por espaço, um **fator de réplica** N e uma **lista de "essenciais"** que vão a todos; cada par declara a **quota** de disco; a máquina grande é destino padrão. Determinística, auditável, sem LLM. Desenhar junto com o espelho da F2, não antes.

**6. Fotos como tipo de dado.** Recomendação: novo tipo de artefato com blob próprio (hash `ni:` já existe), EXIF extraído como dado estruturado — **com a geolocalização tratada como dado sensível** (classificação própria e opção de removê-la ao federar) —, entrando no pacote offline como mais um blob. **Depois** do F1b com MHTML.

## Ordem sugerida

F1b (pacote offline do MHTML e dados estruturados) → F2 (pull assinado, espelho entre próprias) → itens 1–2 (espaço como tag + concessão em lote) → fotos (6) → réplicas (5) → órgãos (4).

## O que esta ADR não altera

`policy_engine`, `AuditLog`, `PipelineEvent`, os quatro níveis e os pisos de `interno` (nunca sai). O item 2 é o único que mexe num piso (de "por objeto" para "por objeto ou por espaço") e por isso exige decisão explícita.

## Implementação do item 2

`politica.conceder_espaco`: regra *permitir* com `espaco_urn` + `par_ref` + `valida_ate` (no máximo 366 dias, renovável) para um `Space` **explícito** da organização; vale para o que estiver no espaço **no momento do envio**. No motor puro, `Contexto.espaco_explicito` só é verdadeiro quando o espaço é explícito e contém o objeto — o espaço padrão ("tudo") **nunca** serve de concessão em lote. `interno` continua sem sair, e uma regra *negar* (inclusive por objeto, para o caso "sem a foto da tia") ainda vence a concessão.
