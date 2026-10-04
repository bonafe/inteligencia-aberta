# ADR 011 — Política de replicação: motor de regras, pares e auditoria

**Status:** aceito — 2026-10-04. **Implementado:** a classificação por domínio na captura e a remoção da topologia `compute`. **Não implementado:** o motor de regras, o cadastro de pares, o enrolamento e o restante desta decisão.

## Contexto

A [ADR 010](010-federacao-por-log-assinado.md) definiu o que é a federação (log de eventos assinados, chaveiro, `Space`) e, em emenda, que **as máquinas do mesmo dono replicam pelo mesmo mecanismo**, e não pelo `EventoReplicacao` do cluster. Faltava decidir **quem recebe o quê, sob quais regras, com que nível de confiança e com que registro**: "se é uma máquina minha pode tal coisa; se é de outra pessoa, é outra coisa". As decisões abaixo foram tomadas na conversa de 2026-10-04 (P1 a P8 da seção 16 de `docs/arquitetura/federacao.md`, onde está o raciocínio completo).

O que existe e condiciona a decisão:

- Os quatro níveis de `docs/seguranca/classificacao.md` já impõem limites: `interno` é "restrito à organização"; `restrito` e `confidencial` exigem compartilhamento explícito, e o `confidencial` ainda temporário e revogável. O `policy_engine` decide **uso de LLM** e **não é alterado** por esta ADR.
- `Organization` é um conceito local de cada banco: **não existe identidade de organização entre instâncias**.
- `Artifact.tenant` é obrigatório; o `AuditLog` é a trilha de compliance e o `PipelineEvent` é o diário operacional, e os dois não se fundem.
- Até aqui o nível de classificação vinha de um seletor global na extensão (padrão `restrito`), igual para todas as capturas.

## Decisão

**1. Motor de regras determinístico, em três camadas.**

1. **Pisos (não editáveis)** — o que os níveis já impõem; nenhuma regra os contorna:
   - `interno` **nunca** sai para terceiro;
   - `restrito` e `confidencial` só saem para terceiro por **concessão explícita**, por objeto, com validade e revogável;
   - rótulo de classificação desconhecido vale como `confidencial`;
   - o receptor nunca rebaixa o nível (ADR 010).
2. **Padrões (editáveis)**, todos de efeito *permitir*: **máquina própria** (mesmo dono) recebe todos os níveis; **terceiro** (qualquer outra pessoa ou organização) recebe `público`.
3. **Regras do usuário**, acrescentadas livremente. Exemplos: negar `confidencial` ao par `notebook-trabalho`; permitir `confidencial` ao `notebook-viagem`; negar tudo do espaço `familia` a qualquer par. Uma **concessão** é uma regra *permitir* com escopo de objeto e validade.

**Avaliação**, para cada (objeto, par destino, espaço): aplicam-se os pisos; juntam-se as regras que casam; **se alguma `negar` casa, nega** (negar vence, **sem prioridade nem ordem**); senão, se alguma `permitir` casa, permite; senão **nega** (negação por padrão). Como os padrões são todos *permitir*, as regras de *negar* do usuário sempre prevalecem.

- **Condições da v1:** par (por nome e chave), nível, tipo de objeto, espaço e validade.
- **Função pura**, sem LLM nem heurística, **separada do `policy_engine`**; avaliador próprio e pequeno (Casbin, OPA e Cedar não foram avaliados a fundo). Regras em tabela, editáveis pelo admin no início; toda mudança de regra é auditada. Ponto único, sucessor de `eventos_para_peer`.
- **Cada decisão devolve a regra que decidiu e o motivo.**

**2. Interseção, nos dois lados.** O emissor aplica as suas regras ao enviar e o receptor as dele ao aceitar; nada atravessa sem que o emissor permita enviar **e** o receptor aceite receber, com negação por padrão dos dois. O receptor recusa **em silêncio**, sem devolver o motivo, para não vazar metadados sobre as suas regras. Um notebook pode ter "não aceito `confidencial`" e se protege mesmo que o emissor envie.

**3. Granularidade: tudo é regra.** Para um objeto individual: concessão (*permitir* com escopo de objeto e validade) ou "não federar este objeto" (*negar* com escopo de objeto). Não há mecanismo separado de marcação por objeto.

**4. Cadastro de pares e enrolamento.**

- Tabela **`Par`**, por instância: nome (ex.: `notebook-viagem`), `did:key` da instância remota, endpoint, **tipo** (`próprio` ou `terceiro`), **confiança** e estado (pendente, confirmado, revogado). Referência das regras do motor.
- **`Par` e `Maquina` são uma coisa só:** o par tem, opcionalmente, `gateway_endpoint` e `ollama_endpoint`; o roteador de LLM passa a ler dos pares e o `registrar_maquina` deixa de existir.
- **Enrolamento por convite:** a instância A gera um convite (token de uso único, o seu DID e o endpoint) e o administrador o cola na B. As duas trocam os DIDs e cada uma mostra uma **impressão digital curta** do DID da outra; **o administrador confere que batem e confirma nos dois lados**. Não se confia no primeiro contato, nem dentro da VPN.
- **O tipo é atribuído localmente por cada lado.** "Próprio" é um rótulo do administrador; não há prova criptográfica de "mesmo dono" (não existe identidade de usuário entre instâncias) — a prova é humana, foi a mesma pessoa que executou os dois lados. Cada instância escolhe o tipo que dá à outra, e os dois podem divergir.
- **Revogar** marca o estado e para de enviar; **não recolhe** o que já foi copiado. **Rotação de chave do par:** evento assinado pela chave antiga; se a chave se perdeu, refaz-se o enrolamento.

**5. Confiança do par: o que o receptor faz com o que recebe.** Campo separado do tipo (que define o que *pode sair*):

| Confiança | O que o receptor faz | Padrão para |
|---|---|---|
| ignorar | descarta, não guarda | par pausado ou revogado |
| quarentena | guarda o envelope original **sem projetar**; o administrador revisa antes de aceitar | (escolha do administrador) |
| alegação | projeta como **alegação do autor**, nunca como fato | terceiro |
| aceitar e repassar | aceita e pode reexportar a outros pares | próprio |

O que se repassa continua sujeito aos pisos e às regras, e o nível do objeto viaja junto.

**6. O que se replica.**

- **Eventos:** sempre, a todos que as regras permitem.
- **Texto extraído e dados estruturados:** como **resultados** dentro dos eventos; nada é reprocessado.
- **Blobs (o MHTML):** **espelho em segundo plano entre as máquinas próprias**; **sob demanda por hash com terceiros**, e só de hashes que o próprio par anunciou. O espelho para quando o espaço livre em disco cai abaixo de um **limite configurável**.
- **Embeddings (vetores): não replicam.** Dependem do modelo e da versão; cada instância calcula os seus a partir do texto replicado.

**7. Organização do objeto importado.** **O espaço é o limite**, e cada espaço aponta para uma organização local, escolhida ao entrar nele:

- **espaço de terceiro:** por padrão cria-se uma organização dedicada ("Federado: <espaço>"), para os dados nunca se misturarem com os do dono e o isolamento por organização valer;
- **espaço próprio:** o administrador escolhe em qual organização sua cai, com padrão na principal;
- **objeto que já existe localmente:** permanece no `tenant` em que estava e só ganha a associação ao novo espaço;
- o objeto importado **mantém o UUID de origem** (`urn:uuid:`).

**8. Auditoria.** Cada decisão de enviar ou receber registra quem (par/chave), quando, o objeto (`urn`), a **regra que decidiu** e o motivo — **nunca o conteúdo**.

- **`AuditLog`:** só decisões sobre `restrito` e `confidencial` e as concessões, **permitidas e bloqueadas** (trilha de compliance).
- **`PipelineEvent`:** o fluxo todo, de todos os níveis (diário operacional).
- Os dois **não se fundem**.

**9. Classificação por domínio de origem (já implementada).** O caso "tudo de `bancodobrasil.com.br` é `confidencial`" **não** é condição de replicação: resolve-se **classificando na captura**, e os pisos e as regras por par agem então sobre o nível. A regra (`RegraClassificacaoDominio`, por organização) **só sobe o nível**, casa por sufixo de rótulo, fecha o LLM externo ao elevar para `restrito`/`confidencial`, emite `captura.classificada` e não reclassifica o que já existe. Ver `docs/seguranca/classificacao.md`.

## Alternativas consideradas

- **Tabela fixa de tetos de classificação por tipo de par.** Descartada: o dono precisa de regras por máquina (um notebook recebe `confidencial`, outro não). A tabela virou o conjunto de padrões do motor. A proposta inicial de "terceiro recebe até `interno`" foi **corrigida** por contradizer a definição do nível.
- **Lista ordenada de regras, em que a primeira que casa decide** (estilo firewall). Descartada: mais flexível, porém mais fácil de errar; "negar vence" é previsível e dispensa prioridade.
- **Biblioteca de política (Casbin, OPA, Cedar).** Adiada: domínio pequeno e que precisa ser totalmente determinístico e auditável; reavaliar se crescer.
- **Domínio de origem como condição do motor de replicação.** Descartada: a classificação por domínio na captura cobre o caso e protege também o que nunca será federado.
- **Confiança no primeiro contato (TOFU) no enrolamento**, ao menos dentro da VPN. Descartada: é a brecha que a seção 11 de `federacao.md` só aceita em redes controladas.
- **Tipo do par declarado pelo próprio par.** Descartado: cada lado decide o que o outro é para ele.
- **Tipo e confiança num campo só.** Descartado: quem o par é (o que pode sair) e o que se faz com o que ele envia são questões diferentes.
- **`Par` e `Maquina` separados.** Descartado: dois cadastros do mesmo conceito.
- **Replicar os vetores de embedding.** Descartado: dependem do modelo e da versão; replicam-se o texto e os resultados.
- **Mecanismo próprio de marcação por objeto.** Descartado: concessão e negação com escopo de objeto já cobrem.
- **Tudo no `AuditLog`.** Descartado: afogaria a trilha de compliance em decisões sobre dado `público`.
- **Tenant derivado do espaço, em vez de um mapeamento por espaço.** Descartado em favor de o espaço apontar para uma organização local.
- **`interno` a terceiro por concessão explícita**, como `restrito`. Descartado: seria uma exceção à definição do nível.

## Consequências

- **Equipes entre instâncias ficam fora por enquanto.** Como não existe identidade de organização entre instâncias, `interno` **nunca** sai e qualquer outra pessoa é terceiro: um colega de equipe com instância própria só recebe `público` ou o que lhe for concedido objeto a objeto. O reconhecimento de uma organização comum a várias instâncias é um problema em aberto e a pré-condição para relaxar essa regra.
- **"Próprio" é uma atestação humana.** Quem controla o enrolamento dos dois lados pode rotular qualquer par como próprio; o sistema não prova que é a mesma pessoa.
- **Revogar não recolhe cópias.** Dado `confidencial` enviado a uma máquina própria que depois é perdida continua nela; o espelho entrega o dado a **todas** as máquinas próprias por padrão. O dono controla isso com regras por par e, no próprio receptor, com "não aceito `confidencial`".
- **Custo de CPU por máquina:** cada instância recalcula os próprios embeddings a partir do texto replicado.
- **Organizações dedicadas por espaço** de terceiro podem se multiplicar; o administrador as vê como organizações comuns no portal.
- **O receptor não explica a recusa.** Facilita não vazar metadados, mas dificulta o diagnóstico de "por que não chegou"; o registro local de cada lado (`PipelineEvent`, `AuditLog`) é a fonte.
- **O motor passa a ser peça de segurança:** precisa de testes cobrindo os pisos e a precedência, e nasce com a mesma disciplina do `policy_engine` (determinístico, auditável, sem LLM).
- **A classificação por domínio protege desde já**, sem federação: o que vem de um domínio sensível nasce com o nível certo e fecha o LLM externo. Não reclassifica o que já foi capturado, e uma regra num sufixo público amplo (`com.br`) elevaria todo o domínio (seguro, mas amplo).
- Esta ADR **não altera** o `policy_engine`, o `AuditLog` nem o `PipelineEvent`, nem os quatro níveis.

## Próximos passos

Só a classificação por domínio e a remoção da topologia `compute` estão feitas. Ordem prevista (roadmap da seção 13 de `federacao.md`):

1. **Fechar a F0:** `Claim` e `Evidence` como conceitos de domínio (precisa de conversa de modelagem; hoje o hash, o ID `urn:uuid:` e a chave Ed25519 da instância estão prontos).
2. **F1 — espaço local:** `Space`, `Par` (fundido com `Maquina`) e o motor de regras, com os pisos e a precedência cobertos por teste; regras e pares editáveis no admin.
3. **Enrolamento por convite** com impressão digital, e a exportação JSON-LD de um espaço.
4. **F1b — pacote offline** assinado, passando pelo motor nos dois lados.
5. Remover o `EventoReplicacao` e `signals_replicacao` quando a federação os substituir.
6. Mostrar na extensão o nível efetivo da captura.

## Referências

- `docs/arquitetura/federacao.md` (seção 16 e subseções de decisão)
- [ADR 010](010-federacao-por-log-assinado.md) (federação por log assinado, chaveiro, `Space`) e ADR 009 (Ollama e gateway entre peers)
- `docs/seguranca/classificacao.md` (níveis e classificação por domínio), `docs/seguranca/compartilhamento.md`
- `docs/componentes/observabilidade.md` (`PipelineEvent`, estágio `captura.classificada`)
