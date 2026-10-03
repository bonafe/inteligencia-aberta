# Decisão-006: Cluster adaptativo multi-proprietário, não uma primary fixa

**Status:** Aceito
**Data:** 2026-09-16

## Contexto

A Fase 1 do cluster multi-máquina (`apps/cluster/`, `docs/operacao/escala-multimaquina.md`) foi desenhada assumindo dono único: todas as máquinas pertencem à mesma pessoa, existe uma "primária" que hospeda Postgres/Redis/MinIO/Qdrant compartilhados, e cada máquina extra é `compute` (banco compartilhado) ou `replica` (stack própria). Essa fundação existe e funciona — registro de máquina, heartbeat de recursos, roteador de LLM entre Ollamas locais, replicação por log de eventos — mas cenários reais de uso do dono do projeto expuseram o limite desse modelo:

- **Multi-proprietário, não só multi-máquina.** Um familiar, um colega de trabalho, cada um roda a própria instância do sistema, com confiança parcial e deliberada entre eles — não é "minha máquina a mais", é gente diferente, organizações diferentes.
- **Heterogeneidade real de papel entre máquinas do mesmo dono.** Uma máquina com muito disco e pouca RAM/CPU (referência real: 16GB RAM, mais de 15TB de disco, conexão de fibra residencial) serve bem como alvo de réplica; um notebook com pouco disco mas boa CPU serve bem para rodar modelos; máquinas de baixíssimo recurso (1GB RAM) mas com IP público servem um papel de alcançabilidade — nenhum processamento, só ponte. Três papéis distintos, não uma escala de "primária → secundária".
- **Offline-first.** Um notebook viaja, perde conectividade, precisa continuar capturando e operando sozinho, e sincronizar quando a rede volta — sem que isso seja tratado como uma falha do cluster.
- **Compartilhar computação de LLM entre donos diferentes.** Usar o Ollama da máquina de um colega e vice-versa, distribuindo requisição entre máquinas de proprietários distintos, não só entre as próprias.
- **Colaboração organizada por projeto, não por organização inteira.** Usuários criam um projeto, adicionam colaboradores (potencialmente de organizações diferentes), e é essa participação — não o fato de pertencer à mesma `Organization` — que decide o que fica elegível a ser replicado ou compartilhado entre eles.
- **Replicação com critério, não tudo-ou-nada.** Se uma máquina deve ou não receber cópia de um dado depende de quem é o dono do destino, se há espaço disponível ali, e de uma decisão explícita por dado/categoria (ex.: fotos de família sempre têm réplica, mas só em máquina do próprio dono).
- **Proveniência visível.** O grafo do sistema (Mapa Vivo) precisa mostrar onde um dado tem cópia (uma ou mais máquinas) e, para conteúdo gerado por LLM, em que máquina, com que modelo, configuração e duração cada geração aconteceu.

Três abordagens foram consideradas:

- **Manter o modelo Fase 1 e adiar tudo para depois:** mais simples de continuar, mas não cobre nenhum dos cenários de multi-proprietário nem de papel especializado por máquina — exigiria retrofit completo do zero quando esses cenários chegassem, não uma extensão.
- **Migrar para Kubernetes ou Docker Swarm** (a opção que o roadmap original da Fase 5 previa): dá orquestração genérica de containers e, no caso do Swarm, eleição de líder pronta via Raft. Mas não resolve nada específico do domínio — quem pode replicar para quem, sob qual critério de sensibilidade/espaço/propriedade, proveniência de geração de LLM — e exigiria trocar toda a base operacional (docker-compose por máquina) já construída e testada. Descartada numa conversa anterior desta mesma sessão de trabalho, quando ficou claro que o Tailscale/Headscale que o dono do projeto já usa resolve a parte de descoberta autenticada sem esse custo.
- **Malha adaptativa própria, com um motor de posicionamento e um novo conceito de colaboração (`Projeto`)**: mais trabalho de desenho, mas nasce cobrindo os cenários reais e reaproveita quase toda a infraestrutura já construída na Fase 1 (heartbeat, `Maquina`, `MaquinaStatus`, `EventoReplicacao`, `apps/cluster/replicacao.py::eventos_para_peer`, `apps/cluster/llm_router.py`). Escolhida.

## Decisão

Adotamos um modelo de malha adaptativa em vez de uma topologia fixa de primária/secundária. Cinco peças, cada uma evoluindo algo que já existe:

**Perfis de nó por capacidade, não por rótulo único.** Uma máquina deixa de ser só `compute` ou `replica` e passa a acumular capacidades independentes: contribui armazenamento (aceita réplica de dado de terceiros), contribui computação (participa do roteamento de LLM — já existe via `Maquina.ollama_endpoint`), é borda pública (tem IP alcançável de fora, útil como ponte), funciona offline (stack completa própria, opera sem depender de nenhuma outra máquina). Uma máquina pode ter mais de uma capacidade ao mesmo tempo. Isto ainda não está implementado — é o rumo; `Maquina.modo` (`compute`/`replica`) continua valendo como está até essa evolução ser feita.

**`Projeto` como unidade de colaboração entre organizações.** Um conceito novo, que não existe hoje em nenhum model nem doc: membros de um `Projeto` são usuários individuais, não organizações inteiras — é assim que pessoas de organizações diferentes decidem, juntas, o que compartilham entre si. `Projeto` é complementar, não substituto, de dois mecanismos que já existem: `Organization` continua sendo o limite de confiança e isolamento de dados por padrão (`docs/seguranca/compartilhamento.md`); `Sharing` (`apps/artifacts/models.py`) continua sendo o grant pontual, por artefato específico, revogável, sempre iniciado pelo dono. `Projeto` resolve um problema que nenhum dos dois resolve: uma relação de colaboração contínua e estrutural, não um grant único.

**Motor de posicionamento com critério.** Evolução de `apps/cluster/replicacao.py::eventos_para_peer` — que já foi isolado nesta função sozinha exatamente para ser o único ponto a mudar quando isso chegasse. A decisão de replicar um dado para uma máquina passa a considerar: se o destino é do mesmo dono; se destino e origem participam de um `Projeto` em comum que autoriza aquela categoria de dado; se há espaço disponível no destino (`MaquinaStatus.disco_disponivel_gb`, já coletado pelo heartbeat); e uma marcação explícita, por dado ou por categoria, de que aquele conteúdo deve ter réplica.

**Roteador de LLM estendido a `Projeto`.** `apps/cluster/llm_router.py::escolher_execucao` e o gateway compatível com OpenAI (`apps/cluster/gateway.py`) passam a poder incluir máquinas de outros donos quando um `Projeto` em comum autoriza reciprocidade de computação — hoje o roteador só considera máquinas da mesma organização.

**Proveniência no grafo.** O Mapa Vivo (`apps/artifacts/graph.py`) passa a mostrar, por artefato, onde há cópia (uma ou mais máquinas) e, para conteúdo gerado por LLM, em que máquina/modelo/configuração/duração cada geração ocorreu — dado que já existe em `MaquinaModeloOllama` e no padrão de `EventoReplicacao`, faltando só a leitura pelo grafo.

O que já estava decidido continua valendo, sem mudança: nunca replicação nativa do Postgres/MinIO/Qdrant (motor próprio via log de eventos, por causa exatamente da necessidade de critério que replicação nativa não oferece); nunca failover automático de "quem é a máquina primária" sem que exista replicação real de dados primeiro (eleger uma nova primária sem os dados não resolve nada); descoberta de máquinas via Tailscale/MagicDNS, não broadcast customizado.

## Consequências

**Positivas:**
- Cobre os cenários reais de uso do próprio dono do projeto, não hipóteses — a validação veio de casos concretos, não de especulação sobre requisitos futuros.
- Reaproveita quase toda a infraestrutura da Fase 1: heartbeat, `Maquina`, `MaquinaStatus`, `EventoReplicacao`, o roteador de LLM e o gateway já existem e só precisam ser estendidos, não recriados.
- Dá um lugar natural para métricas ricas de proveniência que já estão sendo coletadas (`MaquinaModeloOllama`) mas ainda não aparecem em lugar nenhum da interface.
- Mantém descoberta simples (Tailscale/MagicDNS) em vez de adotar uma plataforma de orquestração genérica que não resolveria a parte específica do domínio.

**Negativas:**
- Modelo mais complexo de raciocinar que primária/secundária — capacidades independentes por máquina, mais um conceito de colaboração (`Projeto`), mais um motor de política de posicionamento.
- `Projeto` é uma superfície de confiança inteiramente nova, com sua própria necessidade de regras (quem convida, quem revoga, o que um membro vê de outro) que ainda não foram desenhadas em detalhe.
- Um motor de posicionamento é uma peça de política nova — precisa nascer com a mesma disciplina do `policy_engine.py` (determinístico, auditável), sem se tornar acoplado a ele.

**Mitigações:**
- Tudo é aditivo: a Fase 1 (dono único, primária fixa, descoberta automática) continua funcionando exatamente como está — nada precisa ser desfeito para essa evolução acontecer.
- Nenhuma mudança de código foi feita junto com esta decisão — o alinhamento de modelo vem antes da implementação, de propósito.

**Restrições geradas:**
- O motor de posicionamento nunca decide sozinho enviar dado `restrito`/`confidencial` para fora do dono sem uma autorização explícita e registrada — mesmo princípio de negação por padrão já normatizado em `docs/seguranca/compartilhamento.md`.
- Toda decisão de posicionamento (replicar ou não, para onde) deve ser auditável — quem, quando, por qual critério — no mesmo espírito de `AuditLog`/`PipelineEvent`.
- `Projeto` nunca implica confiança automática de organização inteira: participação é por usuário, explícita, e não estende a superfície de isolamento por padrão que `Organization` já garante.
- O roteador de LLM cross-`Projeto` só encaminha requisição para máquina de outro dono quando esse dono autorizou reciprocidade explicitamente — nunca por padrão.
