# ADR 010 — Federação entre instâncias por log de eventos assinados

**Status:** aceito — 2026-10-04 (decisões de arquitetura; **nenhum código foi escrito**)

## Contexto

Instâncias independentes do Inteligência Aberta — de donos diferentes, em versões diferentes, na mesma LAN, numa VPN ou na Internet — precisam trocar informação sem replicar bancos e sem tratar alegações como fatos (Cenário B). A análise completa está em `docs/arquitetura/federacao.md`; esta ADR registra as decisões tomadas na conversa de 2026-10-04 sobre as oito pendências da seção 14.

O que existe hoje e condiciona a decisão:

- `apps/cluster` (ADR 006, ADR 009) resolve o **Cenário A**: máquinas do mesmo dono ou de colaboradores próximos, com token compartilhado (`LLM_GATEWAY_TOKEN`) e replicação por estado (`EventoReplicacao`). Não é um protocolo entre partes que não confiam uma na outra.
- `PipelineEvent` → `PipelineRun` (ADR 005) já é o padrão "log append-only + projeção reconstruível", mas é diário operacional, não envelope de intercâmbio.
- O código **não tem Neo4j** (só aparece em documentação e roadmap), o storage é o **Garage** (ADR 008) e o **MHTML não tem hash** hoje.

## Decisão

**1. A fonte de verdade é um log de eventos assinados.** Cada evento é um envelope com autor (chave), espaço, tipo, `@context` versionado, payload JSON-LD, hash e assinatura, encadeado por `prev` **por chave**. Tudo o mais — Postgres de domínio, grafo, Qdrant, `PipelineRun`, visão consolidada — é **projeção reconstruível por replay**, e registra a versão do projetor que a calculou.

- **Armazenamento inicial:** tabela `FederationEvent` no Postgres (separada de `PipelineEvent` e de `EventoReplicacao`), escrita na **mesma transação** do estado de domínio (outbox, sem escrita dupla), com `UPDATE` e `DELETE` **bloqueados no banco** (papel sem esses privilégios ou trigger), não só por convenção.
- **Imutabilidade verificável:** assinatura do autor + cadeia de hash + imposição no banco. Contra a reescrita total do log pelo dono da instância, os pares guardam o último hash recebido (testemunha) e podem-se publicar checkpoints assinados da cabeça da cadeia.
- **Replay:** projetores determinísticos e idempotentes; eventos guardam **resultados** (extração, resposta de LLM), nunca instruções de reexecução, para que o replay não chame de novo serviços externos.
- **Broker (Kafka, NATS JetStream, Redis Streams) não é fonte de verdade.** Pode entrar depois como transporte/distribuição local às projeções, alimentado pelo log, sem mudar o envelope.

**2. Grafo é projeção.** Vale para qualquer tecnologia (Neo4j, tabelas de arestas no Postgres, outra). A escolha da tecnologia fica para a Fase 2 do roadmap, que não deve prometer Neo4j como decisão tomada.

**3. Espaço (`Space`).** Unidade com membros, política e eventos. Um objeto pode pertencer a **vários** espaços; a exportação é por espaço. `Space` nos nomes técnicos e no protocolo, "Espaço" na interface. O `Projeto` (ADR 006) vira um `Space`; a `Organization` é um espaço implícito; `Sharing` vira grant em um espaço.

**4. Escritores.** Logs por autor, sem DAG, na v1. Cada chave tem a sua cadeia; ninguém escreve na cadeia de outro. DAG só se surgir necessidade real de ordenar escritas concorrentes.

**5. Identidade: chaveiro por usuário, chave escolhida por espaço.**

- A identidade (usuário, id estável e local) tem várias chaves Ed25519 (`did:key`), cada uma com rótulo, estado (ativa, aposentada, comprometida) e tipo de guarda. A primeira é gerada automaticamente.
- Cada membro de um espaço tem um vínculo `Membro → Chave`; sem escolha, vale a chave padrão.
- **Privacidade por padrão:** a federação vê só a chave usada em cada espaço; a ligação entre chaves da mesma pessoa **não é publicada**, mas o usuário pode optar por revelá-la.
- O envelope separa `author` (id da chave) de `actor` (referência ao usuário). Eventos `key.added`, `key.rotated` e `key.revoked` vivem no mesmo log; na rotação, o primeiro evento da chave nova aponta para o último da antiga, com as duas assinaturas.
- **Revogação por posição na cadeia, não por data** ("a chave K é suspeita a partir do evento nº N"): quem tem a chave roubada pode assinar com data anterior; a cadeia fixa a ordem e o registro de recebimento dos pares serve de testemunha.
- **Custódia no servidor, por enquanto:** as chaves privadas, **inclusive a de recuperação**, ficam na instância, cifradas em repouso (mesmo padrão de `apps/infrastructure/crypto.py`).

**6. Apagamento: só tombstone/retratação.** O receptor marca, e a política local decide a remoção física. Cifra por objeto fica adiada.

**7. Separação de apps.** `apps/cluster` permanece como está (Cenário A). A federação (Cenário B) ganha um **app novo**, sem misturar as premissas de confiança.

**8. IDs, hash e vocabulário.**

- IDs de objetos: `urn:uuid:<uuid>`; a origem é dita pelo `author` e pelo espaço, não pelo ID.
- Hash de conteúdo: RFC 6920, `ni:///sha-256;<base64url>`, também como identificador do blob. O algoritmo viaja com o hash, permitindo trocá-lo.
- Namespace próprio: `ia:` = `https://w3id.org/inteligencia-aberta/v1#`. O `v1` é imutável; mudança incompatível cria `v2`. O `@context` vem **embutido** nas instâncias e pacotes; ninguém resolve a URI para entender um evento.

**Invariantes herdados da análise (seção 7):** assinatura sobre os bytes canônicos do envelope (JCS, RFC 8785), não sobre o JSON-LD canonicalizado; nunca apagar evento; autor e espaço em todo evento; o rótulo de classificação viaja com o objeto e o receptor não o pode rebaixar; afirmação separada de fato desde o primeiro dia (`Claim` e `Evidence` como conceitos de domínio).

## Alternativas consideradas

- **Serviço de mensageria (Kafka ou similar) como fonte de verdade.** Descartada: não oferece assinatura nem cadeia de hash; retenção e compactação conflitam com "nunca apagar evento"; não é protocolo entre partes que não confiam uma na outra (a instância ainda guardaria o log recebido); cria o problema da escrita dupla com o Postgres; é pesado para uma instância por host. O que atraía — replay e imutabilidade — o log assinado em tabela entrega, com garantia mais forte.
- **Neo4j como fonte de verdade das relações.** Descartada: assinatura, autor e retratação não cabem bem em nós e arestas; divergência entre fontes precisa ser preservada; desfazer uma fusão errada (`sameAs`) exigiria reescrever o grafo.
- **DAG de eventos (estilo Matrix).** Adiada: só se houver escritores concorrentes no mesmo espaço.
- **Só chave por instância.** Descartada em favor do chaveiro: separar autorias por chave, rotacionar, revogar e isolar contextos (trabalho × pessoal) custa pouco se o envelope já separar `author` de `actor`.
- **Chave pessoal portátil, com a privada fora do servidor, desde já.** Adiada (F4): exige resolver guarda e recuperação, que é decisão de produto.
- **Cifra por objeto com destruição de chave.** Adiada (ver Consequências).
- **Estender `apps/cluster` para a federação.** Descartada: misturaria confiança de infraestrutura com confiança entre terceiros.

## Consequências

- **A instância é a raiz de confiança nesta fase.** Com as chaves custodiadas no servidor, o dono ou um invasor da instância pode assinar como qualquer usuário local e revogar ou adicionar chaves. Como a chave de recuperação está no mesmo lugar, **não há recuperação independente** diante do comprometimento do servidor. O ganho atual é separar autorias e contextos, não autoria à prova da instância. A evolução (chave fora do servidor, assinada no navegador ou na extensão Chrome; recuperação offline) cabe na F4 **sem mudar envelope nem eventos**.
- **Apagamento é fraco.** Só tombstone: o dado permanece legível no log e nas cópias dos pares. Como o sistema não está em produção, é possível **recomeçar do zero** se a cifra por objeto virar requisito, mas essa liberdade vale **só enquanto nenhum evento tiver saído da instância**. **Reavaliar antes do primeiro intercâmbio com uma instância real** (F1b/F2); até lá, trocar só com instâncias de teste descartáveis. O payload cifrado entra como nova versão do envelope, sem alterar as anteriores.
- Todo estado derivado (grafo, `PipelineRun`, índices) passa a depender de projetores versionados e de um mecanismo de reconstrução; mudar a lógica de consolidação implica reprojetar. Snapshots de projeção só se o replay completo ficar lento.
- A ADR 006 mistura os Cenários A e B e precisa de revisão: o `Projeto` vira `Space`, e a colaboração entre donos passa a ser tratada pela federação, não por `apps/cluster`.
- Novas tabelas e conceitos (`Space`, `SpacePolicy`, `FederationEvent`, identidade e chaveiro, `Claim`, `Evidence`) em um app novo.
- O vocabulário próprio depende de um namespace em w3id.org **ainda não registrado**; até o registro, a URI é só identificador reservado.

## Próximos passos

Sem código até aqui. Ordem prevista (roadmap da seção 13 de `federacao.md`):

1. Revisar a ADR 006 e atualizar `docs/roadmap.md` (Neo4j na Fase 2).
2. **F0 — preparação, sem federar:** hash do MHTML no ato da captura (formato `ni:`) e backfill; ID global; `Claim`/`Evidence`; chave Ed25519 por instância.
3. **F1 — espaço local** e exportação JSON-LD de leitura; **F1b — pacote offline** assinado (a entrega que mais testa sem decidir nada de rede).
4. Registrar o namespace no w3id.org perto da F1.

## Referências

- `docs/arquitetura/federacao.md` (análise completa e exemplo MHTML → notícia → alegação)
- ADR 005 (log de eventos), ADR 006 (cluster e `Projeto`), ADR 008 (Garage), ADR 009 (Ollama e gateway entre peers)
- `docs/seguranca/classificacao.md`, `docs/seguranca/compartilhamento.md`
