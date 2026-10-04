# Federação entre instâncias — análise arquitetural

**Status:** análise de 2026-10-03; as oito decisões da seção 14 foram fechadas em 2026-10-04 e consolidadas na [ADR 010](decisoes/010-federacao-por-log-assinado.md); a política de replicação (seção 16) está na [ADR 011](decisoes/011-politica-de-replicacao.md). **Nada aqui está implementado.** Onde o texto original divergir das decisões registradas (seção 14 e subseções ao final), valem as decisões e a ADR.

**Aviso de método:** a avaliação dos padrões externos (seção 4) foi feita com conhecimento prévio, sem consulta à web. O estado de RDF 1.2/RDF-star, de ferramentas JSON-LD e de métodos DID deve ser reconferido antes de qualquer decisão que dependa deles.

## 1. O problema

Dois cenários diferentes de replicação:

- **Cenário A — infraestrutura.** Máquinas do mesmo ambiente/VPN replicam tecnologias de persistência (Postgres↔Postgres, object store↔object store). Alta confiança entre nós. Já tratado, em parte, por `apps/cluster/` (ADR 006). **Não é o objeto deste documento.**
- **Cenário B — federação.** Instalações independentes do Inteligência Aberta — na mesma LAN, numa VPN ou na Internet, de organizações diferentes, em versões diferentes, no futuro com tecnologias internas diferentes — descobrem-se, comunicam-se e sincronizam informação. **A federação não é replicação de bancos**: fica acima da persistência. Postgres, Qdrant, Garage e um eventual grafo são detalhes internos de cada instância.

Requisitos que guiam o desenho: federação por **contexto e regras** (não "replicar com o servidor X"), **protocolo ≠ implementação**, **proveniência** em tudo, alegações **nunca promovidas a fatos** automaticamente, identidade federada separada da conta local, conflito como informação a preservar, local-first/offline-first, sem servidor central obrigatório, padrões abertos em vez de protocolo próprio.

## 2. O que existe hoje

### 2.1 Divergências em relação às premissas iniciais

| Premissa | Realidade no repositório |
|---|---|
| Neo4j faz parte da stack | Só existe em documentação (`roadmap.md`: `[ ]`). Não está nos `docker-compose*.yml` nem nos `requirements.txt`. O Mapa Vivo (`apps/artifacts/graph.py`) lê do Postgres. |
| O object store é o MinIO | É o Garage (ADR 008). O orchestrator ainda usa o cliente `minio` (protocolo S3). |
| O MHTML tem hash | Não tem. O objeto é gravado como `{artifact_id}.mhtml` (`services/orchestrator/main.py`) e o `Artifact.content` guarda `mhtml_bucket`/`mhtml_path`. O único hash no código é um `md5` de chave de cache (`extractors/detector.py`). |

O terceiro ponto é o mais relevante: o modelo de domínio guarda a **localização física** do blob, não a sua **identidade**.

### 2.2 Reaproveitável

- **`PipelineEvent`** (`apps/events/models.py`): append-only, `sequence`, `correlation_id`, `causation_id`, ator (`user`, `hostname`), `schema_version`. Com a projeção reconstruível `PipelineRun`, valida o padrão "log imutável + estado derivado".
- **Proveniência em formação:** `ArtifactLineage`, `DocumentText.extractor_version`/`detection_source`, `EstruturacaoLLM`, `ChamadaLLM` (provider, modelo, máquina, duração) — já têm forma de `prov:Activity`/`prov:SoftwareAgent`.
- **Classificação** em quatro níveis, imutável por artefato, e `permite_llm_externo`: o rótulo de sensibilidade já acompanha o dado.
- **`Sharing`, `Organization`, `Membership`, `Team`:** semente da autorização.
- **UUID como PK:** unicidade global barata.
- **`eventos_para_peer`** (`apps/cluster/replicacao.py`): ponto único de decisão do que sai; hoje não filtra nada — candidato natural a *policy enforcement point*.
- **Pull com cursor e token** (`ReplicacaoEventosAPIView`): transporte reaproveitável (equivale ao `added_after` do TAXII).
- **Tailscale** para descoberta e transporte privado dentro de uma VPN.

### 2.3 O que não serve para a federação

- **`EventoReplicacao` é sincronização de estado disfarçada de evento.** Tipo `artifact.upsert`, payload `serializers.serialize("json", [instance])` (campos do ORM). Acoplado ao schema Django e à versão do software; semântica de sobrescrita; sem autor, assinatura ou espaço; recorte só por organização; `sequence` global por instância (vaza volume de atividade); o **lado receptor não existe**.
- **`Maquina` é identidade de infraestrutura:** linha de banco com `token_hash`, não um par de chaves.
- **JWT HS256 com `JWT_SIGNING_KEY` simétrica:** quem valida também assina. Inadequado entre partes que não confiam totalmente.
- **`Artifact` mistura coisas distintas.** `content` é JSON livre com `vinculos`/`socios`/`partes` (arrays de UUID) — afirmações sobre o mundo gravadas como fato. `sources` é JSON livre; `info_type` (fato/opinião/inferência) é um enum de uma dimensão. **Não existe `Claim` nem `Evidence`.**
- **`Projeto`** existe só em ADR 006 e `docs/seguranca/compartilhamento.md`; nenhum model o implementa.
- **ADR 006 mistura A e B:** o modo `replica` replica por eventos, mas sob "mesmo dono, confiança total".

## 3. Acoplamentos que dificultariam a federação

1. Blob referenciado por bucket/path dentro do `content`, sem hash.
2. Replicação serializa o ORM, não o domínio.
3. Identidade de ator = `User.id` local ou `Maquina.id`.
4. Alegação e entidade são o mesmo registro.
5. Proveniência espalhada (`sources`, `ArtifactLineage`, `extractor_version`, `PipelineEvent`, `ChamadaLLM`) sem modelo único.
6. `X-Machine-Token` é segredo compartilhado, não prova nada a terceiros.
7. A organização é a única fronteira de autorização; não há espaço/contexto.
8. Se um grafo (Neo4j) chegar, não pode virar fonte de verdade.

## 4. Padrões: o que faz sentido

| Padrão | Veredito | Motivo |
|---|---|---|
| **JSON-LD** | Adotar (envelope de intercâmbio) | Semântica RDF sobre JSON; quem ignora RDF ainda lê JSON. |
| **Schema.org** | Adotar (vocabulário principal) | `NewsArticle`, `Person`, `Organization`, `MediaObject` (`sha256`, `encodingFormat`), `Claim` (`appearance`, `author`). |
| **PROV-O** | Adotar | Único padrão maduro para a linhagem; `Entity`/`Activity`/`Agent` mapeiam direto no fluxo. |
| **DCTERMS** | Pontual | Só onde Schema.org não cobre. |
| **SKOS** | Adiar | Quando houver taxonomias de fato. |
| **DCAT** | Talvez | Para catálogo de espaços; sem prioridade. |
| **W3C ORG** | Quase dispensável | `schema:Organization` basta; `org:Membership`+`Role` só se cargo virar dado de primeira classe. |
| **FOAF** | Não | `schema:knows`/`relatedTo` cobrem. |
| **GeoSPARQL / OWL-Time** | Adiar | `schema:geo` e datas ISO bastam; sem triple store espacial. |
| **RDFS/OWL** | Mínimo | Só `rdfs:subClassOf` em perfis, para tipos desconhecidos. Sem raciocínio automático. |
| **SHACL** | Fase posterior | Validar perfis; antes, JSON Schema do envelope. |
| **RDF-star / named graphs** | Não agora | Ainda amadurece; ferramentagem JSON-LD fraca. Modelar a alegação como **recurso próprio** (`rdf:Statement` + `schema:Claim` + `prov:Entity`). |
| **DIDs** | `did:key`/`did:web` apenas | Resolução complexa não compensa agora. |
| **Verifiable Credentials** | Adiar | Úteis para convite/papel, depois do núcleo. |
| **ActivityPub** | Só a ideia | Ator com inbox/outbox, entrega assinada; não a semântica social. |
| **Matrix** | Só a ideia | DAG de eventos e resolução de estado: pesado para a v1. |
| **AT Protocol** | Mais próximo do espírito | Repositório por identidade, assinado, blobs por conteúdo, portabilidade. |
| **STIX/TAXII** | Referência | `Sighting`, `Note`, `Opinion`, `Report`, `confidence`, marcações TLP; coleções com `added_after`. |
| **Solid** | Não | Pods e ACP divergem do modelo local-first. |
| **IPFS** | Só a ideia | Endereçamento por conteúdo, com hash simples; sem dependência. |

**Sobre os "dialetos":** se o intercâmbio for JSON-LD, o modelo canônico já é o formato de troca. ActivityPub e STIX seriam **projeções com perda** na exportação, não camadas centrais. Não construir adaptadores cedo.

## 5. Arquitetura em camadas

| Camada | Responsabilidade | Hoje | Federado |
|---|---|---|---|
| 0. Armazenamento | Postgres, Garage, Qdrant, futuro grafo | Existe | Detalhe interno, invisível |
| 1. Domínio | Models Django | `Artifact`, `DocumentText`… | Interno, evolui sozinho |
| 2. Semântico | Mapeamento domínio ↔ JSON-LD (camada anticorrupção) | Não existe | **Novo.** Schema.org + PROV-O + poucos termos `ia:` |
| 3. Proveniência | Quem, com quê, a partir de quê | Espalhada | Unificada como PROV |
| 4. Identidade | Quem é o ator | `User.id`, `Maquina.id` | Par de chaves Ed25519, `did:key` |
| 5. Autorização | O que o ator pode ver/escrever | Org, `Sharing` | Papéis por **espaço** |
| 6. Política de compartilhamento | O que sai, para quem, sob quais condições | `eventos_para_peer` (vazio) | Política do espaço (teto de classificação, tipos permitidos) |
| 7. Envelope | Unidade assinada e imutável | `EventoReplicacao` (estado) | Evento assinado, com `prev` |
| 8. Transporte | Como os bytes andam | Pull HTTP por token | Pull assinado; depois inbox/push |
| 9. Sincronização | Cursor, descoberta de blobs | `sequence` global | Cursor por espaço e por autor; tem/quer |
| 10. Interpretação local | Confiança, conflito, consolidação | Não existe | **Cada instância calcula a sua** |

Um requisito transversal: **confiança não é um número único.** Distinguir confiança do extrator, confiabilidade da fonte, confiança da classificação, revisão humana, corroboração por fontes independentes, contradições e confiança analítica derivada. A instância receptora recebe `alegação + evidências + proveniência + avaliações` e calcula a **sua** interpretação; não herda a avaliação do par.

## 6. Espaço (nome em aberto)

Proposta de conceito (nome provisório **Space**; alternativas: Context, Dataset, Collection): unidade com identificador, membros (identidades com papel) e **política**. Todo evento carrega o espaço.

- Um objeto pode pertencer a **vários** espaços; a exportação é feita por espaço. *(decisão pendente — seção 14)*
- A política é determinística, no espírito do `policy_engine`: teto de classificação, tipos permitidos, negação por padrão.
- Relação com o existente: `Projeto` (ADR 006) vira um `Space`; a `Organization` vira um espaço implícito; `Sharing` vira um grant em um espaço de um destinatário.
- Exemplos: um projeto de pesquisa sincroniza quase tudo; um espaço familiar compartilha fotos classificadas como familiares, pessoas e parentescos, sem expor o conteúdo profissional da mesma instância.

## 7. Menor núcleo implementável agora

**Entra:**

1. **Hash de conteúdo no ato da captura** e retroativo (backfill) — o passo mais barato e de maior valor.
2. **Identificador global estável** (`urn:uuid:` ou prefixo da instância).
3. **Chave Ed25519 por instância**, com `did:key`.
4. **`FederationEvent`**: tabela nova, separada de `PipelineEvent` e de `EventoReplicacao`. Envelope com autor, espaço, tipo, `@context` versionado, payload JSON-LD, hash e assinatura.
5. **`Space` e `SpacePolicy` mínimos**, podendo começar valendo só para controle de acesso local.
6. **`Claim` e `Evidence` como conceitos de domínio**, mesmo que o extrator produza poucos no início.
7. **Pull assinado** com cursor por espaço e endpoint de blobs por hash.
8. **Receptor que guarda o evento original** e só projeta o que entende.

**Fica fora:** resolução de DID além de `did:key`, VCs, CRDTs, DAG, triple store, SHACL, adaptadores ActivityPub/STIX, identidades contextuais não correlacionáveis.

**Decisões difíceis de reverter, a tomar antes de qualquer federação:**

1. O esquema de IDs.
2. Hash com prefixo do algoritmo (multihash ou RFC 6920 `ni:`), para permitir troca futura.
3. Assinatura sobre os bytes canônicos do envelope (JCS, RFC 8785), **não** sobre o JSON-LD canonicalizado, que é pesado.
4. `@context` versionado e imutável.
5. Nunca apagar evento: retratação ou tombstone.
6. Autor e espaço em todo evento.
7. O rótulo de classificação viaja com o objeto e o receptor não o pode rebaixar.
8. Separar afirmação de fato desde o primeiro dia.

## 8. Exemplo completo: MHTML → notícia → entidades e alegações

Identificadores ilustrativos. `ia:` é um namespace placeholder; `ia:directorOf` marca uma lacuna de vocabulário (candidato: `org:Membership` + `Role`).

```json
{
  "@context": {
    "schema": "https://schema.org/",
    "prov": "http://www.w3.org/ns/prov#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "ia": "https://w3id.org/inteligencia-aberta/v1#"
  },
  "@graph": [
    { "@id": "did:key:z6MkCapturista", "@type": ["prov:Agent", "schema:Person"] },
    { "@id": "did:web:ia-a.example#extensao", "@type": ["prov:SoftwareAgent", "schema:SoftwareApplication"],
      "schema:name": "Extensão de captura", "schema:softwareVersion": "0.9.2" },
    { "@id": "did:web:ia-a.example#extrator", "@type": ["prov:SoftwareAgent", "schema:SoftwareApplication"],
      "schema:name": "extrator-ia", "schema:softwareVersion": "2026.10.1" },
    { "@id": "did:key:z6MkRevisora", "@type": ["prov:Agent", "schema:Person"] },

    { "@id": "https://jornal.example/noticia/123", "@type": "prov:Entity" },

    { "@id": "urn:uuid:captura-1", "@type": "prov:Activity",
      "prov:startedAtTime": "2026-10-03T14:02:11Z",
      "prov:used": { "@id": "https://jornal.example/noticia/123" },
      "prov:wasAssociatedWith": [ { "@id": "did:key:z6MkCapturista" }, { "@id": "did:web:ia-a.example#extensao" } ] },

    { "@id": "ni:///sha-256;3q2-7wAAAAAAAA", "@type": ["prov:Entity", "schema:MediaObject"],
      "schema:encodingFormat": "multipart/related",
      "schema:contentSize": "1843200",
      "schema:sha256": "dead…beef",
      "prov:wasGeneratedBy": { "@id": "urn:uuid:captura-1" },
      "prov:wasDerivedFrom": { "@id": "https://jornal.example/noticia/123" } },

    { "@id": "urn:uuid:extracao-1", "@type": "prov:Activity",
      "prov:startedAtTime": "2026-10-03T14:02:40Z",
      "prov:used": { "@id": "ni:///sha-256;3q2-7wAAAAAAAA" },
      "prov:wasAssociatedWith": { "@id": "did:web:ia-a.example#extrator" } },

    { "@id": "urn:uuid:noticia-1", "@type": ["schema:NewsArticle", "prov:Entity"],
      "schema:headline": "Prefeitura investiga contrato",
      "schema:url": "https://jornal.example/noticia/123",
      "schema:datePublished": "2026-10-02",
      "schema:publisher": { "@id": "urn:uuid:org-jornal" },
      "prov:wasDerivedFrom": { "@id": "ni:///sha-256;3q2-7wAAAAAAAA" },
      "prov:wasGeneratedBy": { "@id": "urn:uuid:extracao-1" } },

    { "@id": "urn:uuid:org-jornal", "@type": "schema:Organization", "schema:name": "Jornal Exemplo" },
    { "@id": "urn:uuid:p-joao", "@type": ["schema:Person", "prov:Entity"], "schema:name": "João Silva",
      "prov:wasGeneratedBy": { "@id": "urn:uuid:extracao-1" } },
    { "@id": "urn:uuid:org-x", "@type": ["schema:Organization", "prov:Entity"], "schema:name": "Empresa X",
      "prov:wasGeneratedBy": { "@id": "urn:uuid:extracao-1" } },

    { "@id": "urn:uuid:claim-1", "@type": ["rdf:Statement", "schema:Claim", "prov:Entity"],
      "rdf:subject": { "@id": "urn:uuid:p-joao" },
      "rdf:predicate": { "@id": "ia:directorOf" },
      "rdf:object": { "@id": "urn:uuid:org-x" },
      "schema:author": { "@id": "urn:uuid:org-jornal" },
      "schema:appearance": { "@id": "urn:uuid:noticia-1" },
      "prov:wasDerivedFrom": { "@id": "urn:uuid:noticia-1" },
      "prov:wasGeneratedBy": { "@id": "urn:uuid:extracao-1" },
      "ia:extractorConfidence": 0.72 },

    { "@id": "urn:uuid:revisao-1", "@type": "prov:Activity",
      "prov:used": { "@id": "urn:uuid:claim-1" },
      "prov:wasAssociatedWith": { "@id": "did:key:z6MkRevisora" } }
  ]
}
```

Nada ali afirma "João é diretor da Empresa X". O grafo diz que o Jornal Exemplo afirmou isso, em uma notícia capturada naquele dia pela pessoa A; que o extrator, em certa versão, produziu a alegação com confiança 0,72; e que a pessoa B a revisou.

**Envelope assinado** (o JSON-LD acima vai em `payload`):

```json
{
  "v": 1,
  "id": "sha256:…hash do envelope sem sig…",
  "space": "urn:uuid:espaco-pesquisa-1",
  "actor": "did:web:ia-a.example",
  "author": "did:key:z6MkCapturista",
  "type": "capture.recorded",
  "prev": "sha256:…evento anterior deste autor…",
  "at": "2026-10-03T14:02:41Z",
  "must_understand": [],
  "payload": { "@context": {}, "@graph": [] },
  "sig": "ed25519:…"
}
```

- A localização interna (bucket e path) **não** vai no payload, nem `schema:contentUrl`.
- O `author` pode ser pseudônimo por espaço. Uma política de redação pode trocar nomes e agentes por pseudônimos como um **novo** evento do remetente, sem quebrar a assinatura do original.

### Mapeamento PROV-O

| Conceito | PROV |
|---|---|
| Captura | `prov:Activity` (`used` URL, `wasAssociatedWith` pessoa e extensão) |
| MHTML | `prov:Entity` + `schema:MediaObject` (`wasGeneratedBy` captura) |
| Extração | `prov:Activity` (`used` MHTML, `wasAssociatedWith` extrator + versão) |
| Agente humano | `prov:Agent` + `schema:Person` |
| Agente de software (inclui IA, com modelo e versão) | `prov:SoftwareAgent` |
| Notícia | `prov:Entity` + `schema:NewsArticle` (`wasDerivedFrom` MHTML) |
| Alegação | `prov:Entity` + `schema:Claim` + `rdf:Statement` (`wasDerivedFrom` notícia) |
| Revisão | `prov:Activity` (`used` alegação, `wasAssociatedWith` revisor) |

Correção de fonte e retratação: `prov:wasRevisionOf` e `prov:wasInvalidatedBy`; as versões anteriores permanecem. `ChamadaLLM`/`EstruturacaoLLM` já são `Activity` + `SoftwareAgent` de IA; `ArtifactLineage` já é `wasDerivedFrom` entre artefatos — a lacuna é a unificação num modelo só.

## 9. Descoberta de blobs por hash

1. O evento de A anuncia a mídia por hash e tamanho, sem o conteúdo.
2. B procura o hash no índice local (índice único em `sha256`).
3. **Já tem:** registra só a referência e a proveniência ("A também possui este blob"); nada é transferido.
4. **Não tem:** pede `GET /federation/v1/blobs/sha256/{h}`, recalcula o hash ao receber e rejeita se divergir. Cada instância grava onde quiser (Garage, S3, disco).

Cuidados:

- **Duas capturas da mesma página raramente têm o mesmo hash** (boundaries e timestamps do MHTML diferem). O ganho real está em repassar o mesmo blob entre pares e em anexos e imagens. Para "mesmo conteúdo", usar chave secundária (hash do HTML ou do texto normalizado).
- **Perguntar "você tem X?" revela interesse.** Permitir só dentro de um espaço, entre pares autenticados e só para hashes que o próprio par anunciou.
- A extração é reaproveitável por `(hash do blob, versão do extrator)`.

## 10. Instância antiga recebendo conceitos novos (leitor tolerante)

1. **Guardar o envelope exatamente como veio.** A assinatura continua válida, pois é sobre os bytes recebidos.
2. **Tipo desconhecido:** guardar como recurso genérico; se o perfil declara `rdfs:subClassOf` (ex.: subtipo de `schema:CreativeWork`), tratar como o supertipo.
3. **`must_understand`:** evento que afeta política ou autorização (um novo tipo de regra, por exemplo) e que a instância não entende **não é aplicado nem repassado** — falha fechada, como a extensão crítica (`crit`) do JWS.
4. **Rótulo de classificação desconhecido:** tratar como o mais restritivo.
5. **Reprojetar ao atualizar.** Como o log original é guardado, a projeção é reconstruível — como o `PipelineRun` já é.
6. **Negociação:** um endpoint `/.well-known/` publica versões de protocolo, perfis e vocabulários suportados.

## 11. Segurança

| Ameaça | Mitigação | Risco residual |
|---|---|---|
| Falsificação (spoofing) | Eventos assinados; chave do par registrada fora de banda (impressão digital conferida); confiança no primeiro uso só onde aceitável | O enrolamento inicial é o ponto fraco |
| Replay | Id único do evento, `prev` por autor, aplicação idempotente; requisições com nonce e timestamp | Replay de requisição de leitura |
| Adulteração | Hash + assinatura; blobs verificados por hash | Nenhum para dado já assinado |
| Roubo de chave | Rotação com evento assinado pela chave antiga, chave de recuperação offline, lista de revogação | O ladrão pode forjar datas anteriores; o registro de recebimento de cada par serve de testemunha |
| Revogação | Evento de revogação com data; eventos posteriores perdem confiança | Não recolhe cópias já feitas (`compartilhamento.md` já reconhece) |
| Vazamento de metadados | Mínimo necessário, TLS/WireGuard, tem/quer limitado, pseudônimos por espaço | Pertencimento ao espaço, tamanhos e horários continuam inferíveis |
| Abuso da federação | Lista de pares permitidos (sem federação aberta por padrão), cotas, limite de tamanho e taxa; MHTML recebido nunca é renderizado nem executado fora de sandbox | Pares maliciosos de baixo volume |
| Confiança entre pares | Níveis por par: ignorar, quarentena, aceitar só como alegação, aceitar e repassar | Calibrar os níveis é decisão de produto |

O JWT HS256 atual **não** deve ser reaproveitado entre instâncias.

## 12. Conflitos

Princípio: a maioria das coisas vira **asserção de um autor**, não estado compartilhado; assim quase não há conflito de escrita. E **divergência não é erro**: "Fonte A afirma X" e "Fonte B afirma não-X" é conhecimento válido e fica preservado.

| Situação | Tratamento |
|---|---|
| Edição concorrente de metadados mutáveis | Evitar estado mutável compartilhado. Cada edição é uma asserção do autor; a instância local escolhe a visão (mais recente por par confiável, ou mostra todas). Sem CRDT na v1. |
| Duas entidades descobertas como a mesma pessoa | Nunca fundir destrutivamente. A fusão é uma asserção `sameAs` com evidência e autor; a visão consolidada é derivada, local e reversível; desfazer é retratar a asserção. Risco: transitividade de `sameAs` errado. |
| Alegações contraditórias | Ambas preservadas. Um vínculo `contradicts` é uma asserção analítica de alguém; o estado (contestada, corroborada) é calculado localmente. |
| Exclusão | Evento de retratação/tombstone pelo autor ou administrador do espaço. O receptor marca; a política local decide a remoção física. Para apagamento legal, considerar cifrar o payload com chave por objeto e destruir a chave (o log mantém o hash). |
| Retratação ou correção pela fonte | Nova captura gera nova alegação; a antiga fica como histórico ("a fonte disse X em d1 e corrigiu em d2"). |
| Mudança de classificação | Hoje, reclassificar é criar novo artefato. Entre pares, "elevar restrição" precisa propagar (evento `classification.raised`); não recolhe cópias. |

### Eventos × estado

| Abordagem | Veredito |
|---|---|
| Sincronização de estado | É o que `EventoReplicacao` faz hoje; frágil entre versões e sem proveniência |
| Federação por eventos | **Adotar** como formato de intercâmbio |
| Event sourcing completo | Não adotar como persistência interna |
| CRDTs | Não na v1 |
| Logs append-only por autor, com cadeia de hash | **Adotar** |
| DAG de eventos (Matrix) | Só se houver vários escritores concorrentes no mesmo espaço |
| Árvores/DAGs de Merkle | Adiar; a cadeia de hash por autor basta |

Conclusão: **logs append-only assinados por autor como intercâmbio; estado local derivado por projeção** (padrão que o repositório já usa em `PipelineEvent` → `PipelineRun`).

## 13. Roadmap incremental

| Fase | Entrega | Critério de saída |
|---|---|---|
| **F0 — preparação (sem federar)** | Hash no ato da captura e backfill; ID global; modelo `Claim`/`Evidence`; chave Ed25519 por instância | Todo MHTML, novo e antigo, tem hash; a alegação é separada da entidade |
| **F1 — espaço local** | `Space` e política, aplicados só ao acesso local; exportação JSON-LD (leitura) de um espaço | Modelo semântico exercitado sem rede |
| **F1b — pacote offline** | Exportar e importar um **pacote** (manifesto, eventos assinados e blobs): federação por arquivo | Duas instâncias trocam um espaço por pen drive e conferem assinaturas e hashes |
| **F2 — pull assinado** | `FederationEvent`, cursor por espaço, blobs por hash, entre duas instâncias | Sincronização incremental e idempotente de um espaço |
| **F3 — robustez** | Eventos desconhecidos, `must_understand`, níveis de confiança do par, retratação e revogação | Instância antiga convive com a nova sem perder dados |
| **F4 — identidade** | Identidades contextuais, VCs para convite e papel, outros métodos DID | Convites verificáveis |
| **F5 — abertura** | Projeções STIX/ActivityPub, SHACL, relays offline, DAG se houver vários escritores | Só por demanda real |

A **F1b** é a mais valiosa: testa quase tudo (semântica, assinatura, blobs, política) sem decidir nada de rede.

## 14. Decisões pendentes (para a próxima conversa)

Cada item traz a recomendação da análise. Itens marcados `[x]` foram decididos em 2026-10-04 (conversa com o usuário) e entram na ADR 010.

- [x] **1. Granularidade do espaço.** Um objeto pode estar em vários espaços ou só em um? **Decidido: vários espaços; a exportação é por espaço.**
- [x] **2. Identidade do usuário.** Chave pessoal portátil ou só chave por instância? **Decidido: chaveiro por usuário, com chave escolhida por espaço, guarda custodiada pelo servidor por enquanto.** Ver "Chaveiro do usuário" abaixo.
- [x] **3. Neo4j.** O código não o tem. Tratar o grafo como **projeção**, nunca como fonte de verdade? **Decidido: sim.** O princípio vale para qualquer tecnologia de grafo (Neo4j, tabelas de arestas no Postgres com `WITH RECURSIVE`, outra); a escolha da tecnologia fica para a Fase 2 do roadmap, que não deve prometer Neo4j como decisão tomada. Ver "Fonte de verdade" abaixo.
- [x] **4. Vários escritores no mesmo espaço desde o início?** Decide entre logs por autor (simples) e DAG (pesado). **Decidido (recomendação adotada): logs por autor na v1**, com cadeia `prev` por chave (ver "Chaveiro do usuário"); DAG só se surgir necessidade real de ordenar escritas concorrentes.
- [x] **5. Apagamento legal.** Cifra por objeto com destruição de chave é requisito, ou basta tombstone? **Decidido: só tombstone por enquanto.** Ver "Apagamento" abaixo.
- [x] **6. Cenários A e B.** **Decidido (recomendação adotada): manter `apps/cluster` como está (A) e criar um app novo para a federação (B), sem misturá-los;** revisar a ADR 006, que mistura os dois.
- [x] **7. Nome do conceito** (Space, Context, Dataset, Collection…) e do namespace `ia:`. **Decidido: `Space` e w3id.org.** Ver "Nome e namespace" abaixo.
- [x] **8. Esquema de IDs** (`urn:uuid:` simples vs. prefixo da instância/DID) e **formato de hash** (RFC 6920 `ni:` vs. multihash). **Decidido: `urn:uuid:` para objetos e RFC 6920 (`ni:`, SHA-256) para hash.** Ver "IDs e hash" abaixo.

### Fonte de verdade e replay (decidido em 2026-10-04)

Pergunta que levou à decisão: a fonte de verdade deveria ser um serviço de mensageria (tipo Kafka) em vez de um log? Conclusão: o log **é** o modelo que se quer (eventos como verdade, estado derivado); o que se decide é onde ele mora. Motivos para o usuário: **replay** e **verdade imutável**.

- **A fonte de verdade é o log de eventos assinados**, com cadeia de hash (`prev`) por autor. O envelope vale independentemente de onde estiver guardado.
- **Armazenamento inicial:** tabela `FederationEvent` no Postgres, escrita na **mesma transação** do estado de domínio (outbox, sem escrita dupla), com `UPDATE` e `DELETE` bloqueados **no banco** (papel sem esses privilégios ou trigger), não só por convenção do código.
- **Imutabilidade verificável:** assinatura do autor + cadeia de hash + imposição no banco. Contra a reescrita total do log pelo dono da instância: os pares guardam o último hash recebido (testemunha, seção 11) e podem-se publicar checkpoints assinados da cabeça da cadeia.
- **Toda projeção é reconstruível por replay** (grafo, `PipelineRun`, Qdrant, visão consolidada) e registra a **versão do projetor** que a calculou. Projetores são determinísticos e idempotentes.
- **Eventos guardam resultados, nunca instruções de reexecução:** o replay não chama de novo LLM nem serviços externos.
- **Snapshots de projeção** só se o replay completo ficar lento; fora da v1.
- **Broker (Kafka, NATS JetStream, Redis Streams) não é fonte de verdade.** Pode entrar depois como transporte/distribuição local às projeções, alimentado pelo log, sem mudar o envelope. Kafka não oferece assinatura nem cadeia de hash, sua retenção/compactação conflita com "nunca apagar evento" e não é protocolo entre partes que não confiam uma na outra.

### Chaveiro do usuário (decidido em 2026-10-04)

A pergunta "chave pessoal ou por instância" virou "um chaveiro por usuário, com a chave escolhida por espaço".

- **Identidade** (usuário, id estável e local) tem **várias chaves** (par Ed25519, `did:key`), cada uma com rótulo (ex.: "trabalho", "pessoal"), data, estado (ativa, aposentada, comprometida) e tipo de guarda.
- **Primeira chave automática:** o usuário novo recebe uma chave gerada pelo sistema.
- **Chave por espaço:** cada membro de um espaço tem o vínculo `Membro → Chave`; sem escolha, vale a chave padrão do chaveiro. Isso substitui a ideia de "chave ativa" global.
- **Privacidade por padrão:** o que a federação vê é só a chave usada naquele espaço. O chaveiro completo é privado; que duas chaves são da mesma pessoa **não é publicado**. O usuário pode **optar por revelar** a ligação (ex.: assinando as duas chaves com a chave de recuperação).
- **Envelope:** `author` = id da chave (`did:key`), separado de `actor` (referência ao usuário). Todo evento carrega o id da chave. A cadeia `prev` vale por chave, ou seja, por (usuário, espaço). Na rotação, o primeiro evento da chave nova aponta para o último da antiga, com as duas assinaturas.
- **Eventos do chaveiro** (`key.added`, `key.rotated`, `key.revoked`) vivem no mesmo log, assinados por uma chave já autorizada; a primeira chave se autoassina no cadastro.
- **Revogação por posição, não por data:** "a chave K é suspeita a partir do evento nº N". A data declarada vale pouco, pois quem tem a chave roubada pode assinar com data anterior; a cadeia `prev` fixa a ordem e o registro de recebimento dos pares (seção 11) serve de testemunha. A consulta "tudo que a chave K assinou depois de N" é direta.
- **Chave de recuperação:** autoriza adicionar e revogar as demais chaves (a chave comprometida não pode revogar a si mesma).
- **Convites e papéis apontam para a chave** usada no espaço; trocar de chave num espaço exige evento de rotação visível aos membros.

**Limitação assumida nesta fase:** as chaves privadas, **inclusive a de recuperação**, ficam **custodiadas no servidor da instância**, cifradas em repouso (mesmo padrão de `apps/infrastructure/crypto.py`). Consequências, que devem constar na ADR 010:

- A instância continua sendo a raiz de confiança: o dono ou um invasor do servidor pode assinar como qualquer usuário local e revogar ou adicionar chaves.
- Como a recuperação está no mesmo lugar das demais chaves, **não há recuperação independente** diante de comprometimento do servidor; a revogação depende da própria instância.
- O ganho atual é separar autorias por chave, rotacionar, revogar e isolar contextos (trabalho × pessoal), não a autoria à prova da instância.

**Caminho de evolução (F4, sem mudar envelope nem eventos):** registrar chave pública cuja privada nunca chega ao servidor (assinatura no navegador ou na extensão Chrome) e mover a chave de recuperação para fora do servidor (guarda offline do usuário).

### Apagamento (decidido em 2026-10-04)

- **Só tombstone/retratação** (seção 12): o autor ou administrador do espaço emite o evento; o receptor marca, e a política local decide a remoção física. Sem cifra por objeto na v1.
- **Por que dá para adiar:** o sistema ainda não está em produção, então é possível **recomeçar do zero** (zerar bancos e log) se a cifra por objeto virar requisito, adotando o novo formato de envelope desde o primeiro evento.
- **Prazo dessa liberdade:** ela vale só enquanto **nenhum evento tiver saído da instância**. Depois que outra instância (ou um pacote offline da F1b) guardar cópias, zerar localmente não recolhe o que os pares já têm, e eventos antigos sem cifra ficam para sempre em claro nelas. **Reavaliar antes do primeiro intercâmbio com uma instância real** (F1b/F2); até lá, trocas só entre instâncias de teste descartáveis.
- **Evolução:** o `@context` e a versão do envelope são versionados e imutáveis (seção 7); o payload cifrado entra como nova versão do envelope, sem alterar as anteriores.

### IDs e hash (decidido em 2026-10-04)

- **IDs de objetos:** `urn:uuid:<uuid>`, coerente com os UUIDs como PKs do projeto. O ID **não carrega a origem**: quem criou é dito pelo `author` (chave) do evento e pelo espaço, não pelo prefixo do ID.
- **Hash de conteúdo:** RFC 6920, `ni:///sha-256;<base64url>`, usado também como identificador do blob (seção 9). O nome do algoritmo viaja com o hash, o que permite trocá-lo no futuro sem reinterpretar valores antigos. Nesta fase, SHA-256.
- **Consequência para a F0:** o hash do MHTML é calculado no ato da captura e gravado já nesse formato; o backfill dos MHTMLs existentes usa o mesmo formato.

### Nome e namespace (decidido em 2026-10-04)

- **Conceito:** `Space` nos nomes técnicos (modelos `Space`, `SpacePolicy`, campo `space` em todo evento, API e protocolo) e **"Espaço"** na interface. Escolhido por não colidir com nada existente (`Context` colidiria com o `@context` do JSON-LD). A palavra "projeto" (ADR 006) deixa de ser conceito próprio: o `Projeto` vira um `Space` (seção 6); se a interface mantiver "projeto", será só um rótulo de tipo de espaço.
- **Namespace do vocabulário próprio:** `ia:` = `https://w3id.org/inteligencia-aberta/v1#`, via **w3id.org** (redirecionamento permanente da comunidade W3C), independente de domínio ou hospedagem. O segmento `inteligencia-aberta` é a proposta inicial; é o único ponto ainda ajustável antes do registro.
- **Versionamento:** o `v1` é imutável (seção 7); mudanças incompatíveis criam `v2`, nunca alteram o `v1`.
- **Sem busca em tempo de execução:** o `@context` vem **embutido** em cada instância e nos pacotes; nenhuma instância depende de resolver a URI para entender um evento (requisito do pacote offline da F1b). A URL pode resolver para a documentação dos termos, mas é só conveniência.
- **Registro:** o pedido ao repositório do w3id.org (pull request público) **não foi feito** e fica para perto da F1; até lá a URI é só identificador reservado na ADR 010.

### Replicação entre as máquinas do mesmo dono (decidido em 2026-10-04)

**As suas outras máquinas replicam pelo mesmo mecanismo da federação.** Uma segunda máquina do mesmo dono é uma instância como qualquer outra: tem a sua chave Ed25519 e é um **par** com nível de confiança "próprio" dentro de um espaço seu. Há um único caminho de replicação para escrever, testar e auditar.

- **Supera o `EventoReplicacao`** (`apps/cluster/replicacao.py`, `signals_replicacao.py`, `GET /cluster/api/v1/replicacao/eventos/`): replicava estado, sem assinatura nem proveniência, e o lado que recebe nunca foi escrito. O código **não foi removido** — com a regra "sem compatibilidade por ora" (`CLAUDE.md`) pode sair quando a federação o substituir de fato.
- **`apps/cluster` fica com:** o heartbeat e o roteamento de LLM com o gateway (ADR 009). A topologia `compute` (várias máquinas sobre um banco compartilhado) **foi removida** (2026-10-04, não era usada), e a de "réplica com stack própria" passa à federação: toda máquina é uma instância completa, e dado só se move por federação, então não há duplicação cega.
- **Efeito sobre a ADR 006:** o "motor de posicionamento entre máquinas do mesmo dono" (disco, marcação de réplica, sensibilidade) deixa de ser peça do cluster e vira parte da política de replicação abaixo.

### Motor de regras de replicação (decidido em 2026-10-04, P3)

A pergunta "qual o teto de classificação por tipo de par" virou "um motor onde se possam acrescentar regras" (ex.: um notebook recebe `confidencial` e outro não). A tabela de tetos que se cogitou é só o conjunto de **regras padrão** do motor.

**Três camadas**

1. **Pisos (não editáveis)** — o que os níveis de `docs/seguranca/classificacao.md` já impõem; nenhuma regra os contorna:
   - `interno` **nunca** sai para terceiro (o nível é "restrito à organização");
   - `restrito` e `confidencial` só saem para terceiro por **concessão explícita**, por objeto, com validade e revogável (como o `Sharing`);
   - rótulo de classificação desconhecido vale como `confidencial` (seção 10);
   - o receptor nunca rebaixa o nível (seção 7).
2. **Padrões (editáveis)** — todos de efeito *permitir*: **máquina própria** (mesmo dono) recebe todos os níveis; **terceiro** (qualquer outra pessoa ou organização) recebe `público`.
3. **Regras do usuário** — acrescentadas livremente. Exemplos: negar `confidencial` ao par `notebook-trabalho`; permitir `confidencial` ao `notebook-viagem`; negar tudo do espaço `familia` a qualquer par; **concessão** = regra de permitir com escopo de objeto e validade (ex.: `restrito` à Maria, só este objeto, até uma data).

**Avaliação.** Para cada (objeto, par destino, espaço): (1) aplicam-se os pisos; (2) juntam-se as regras que casam; (3) **se alguma `negar` casa → nega** (negar vence, **sem prioridade nem ordem**); (4) senão, se alguma `permitir` casa → permite; (5) senão, **nega** (negação por padrão). Como todos os padrões são *permitir*, as regras de *negar* do usuário sempre prevalecem.

- **Nos dois lados:** o emissor aplica as suas regras ao enviar e o receptor as dele ao aceitar (a interseção da P2). Um notebook pode ter "não aceito `confidencial`" e se protege mesmo que o emissor envie.
- **Condições da v1:** par (por nome/chave), nível de classificação, tipo de objeto, espaço e validade. O **domínio de origem da captura não é condição de replicação na v1**: o caso de uso (ex.: tudo de `bancodobrasil.com.br` é `confidencial`) se resolve **classificando na captura**, e então os pisos e as regras por par já agem sobre o nível. Essa **classificação por domínio** é uma funcionalidade à parte, **implementada em 2026-10-04** (`RegraClassificacaoDominio`, ver `docs/seguranca/classificacao.md`): regra de domínio → nível aplicada na captura, que **só sobe** o nível (nunca rebaixa o escolhido na extensão), casa por sufixo (`bancodobrasil.com.br` cobre `www.` e `login.`) e não reclassifica o que já existe (a reclassificação é um ato explícito, `classificacao.md`). Hoje o nível vem de um seletor global no popup da extensão (padrão `restrito`).
- **Explicável e auditável:** cada decisão devolve a regra que decidiu e o motivo; isso alimenta o registro da P8 ("por que isto não foi para o notebook B?").
- **Determinístico:** função pura, sem LLM nem heurística, **separada do `policy_engine`** (que decide uso de LLM e não é tocado). Avaliador próprio, pequeno, em vez de uma biblioteca de política (Casbin, OPA, Cedar não foram avaliados a fundo; reavaliar se o domínio crescer).
- **Armazenamento:** regras em tabela no banco, editáveis pelo admin no início; toda mudança de regra é auditada.
- **Dependência:** regras referenciam pares por nome e chave, então o motor e o **cadastro de pares (P6)** precisam nascer juntos.

### Cadastro de pares e enrolamento (decidido em 2026-10-04, P6)

- **Cadastro de pares:** tabela `Par`, por instância, com nome (ex.: `notebook-viagem`), `did:key` da instância remota, endpoint, tipo (`próprio` ou `terceiro`) e estado (pendente, confirmado, revogado). É a referência das regras do motor (P3).
- **Enrolamento por convite:** a instância A gera um convite (token de uso único, o seu DID e o endpoint) e o administrador o cola na instância B. As duas trocam os DIDs e cada uma mostra uma **impressão digital curta** do DID da outra; **o administrador confere que batem e confirma nos dois lados** (a chave registrada fora de banda da seção 11). Não se confia no primeiro contato, nem dentro da VPN.
- **O tipo é decidido localmente por cada lado.** "Próprio" é um rótulo que o administrador atribui no enrolamento; não há prova criptográfica de "mesmo dono" (não existe identidade de usuário entre instâncias). A prova é humana: foi a mesma pessoa que executou os dois lados. Cada instância escolhe sozinha o tipo que dá à outra, e os dois podem divergir.
- **Revogar:** marca o estado e para de enviar; **não recolhe** o que já foi copiado. **Rotação de chave do par:** evento assinado pela chave antiga; se a chave se perdeu, refaz-se o enrolamento.
- **`Par` e `Maquina` são uma coisa só:** o par tem, opcionalmente, `gateway_endpoint` e `ollama_endpoint`; o roteador de LLM passa a ler dos pares e o `registrar_maquina` deixa de existir (substitui o cadastro manual que ficou depois da remoção do `compute`). Sem compatibilidade a preservar, a tabela `Maquina` pode ser reformulada.
- **Fora desta decisão:** como duas instâncias se alcançam (NAT, VPN, quem puxa de quem) — é a F2 (transporte); o cadastro vale para o pacote offline da F1b também.

### Política de replicação: decisões P1, P2, P4, P5, P7 e P8 (decididas em 2026-10-04)

**P1 — tipo e confiança do par.** O **tipo** (`próprio` ou `terceiro`) diz *quem* o par é e define o que pode sair para ele (P3). A **confiança** diz *o que se faz com o que ele envia*. São dois campos separados no `Par`:

| Confiança | O que o receptor faz | Padrão para |
|---|---|---|
| ignorar | descarta, não guarda | par pausado ou revogado |
| quarentena | guarda o envelope original **sem projetar**; o administrador revisa antes de aceitar | (escolha do administrador) |
| alegação | projeta como **alegação do autor**, nunca como fato | terceiro |
| aceitar e repassar | aceita e pode reexportar a outros pares | próprio |

O que se repassa continua sujeito aos pisos e às regras, e o nível do objeto viaja junto. O administrador pode mudar o padrão (ex.: um terceiro em quarentena).

**P2 — quem decide o que sai.** A **interseção, com negação por padrão dos dois lados**: o emissor aplica as suas regras ao enviar e o receptor as dele ao aceitar. O receptor recusa em silêncio, sem devolver o motivo, para não vazar metadados sobre as suas regras.

**P4 — granularidade.** **Tudo é regra**; a granularidade vem das condições (par, espaço, tipo, nível, validade). Para um objeto individual: **concessão** = regra *permitir* com escopo de objeto e validade; **"não federar este objeto"** = regra *negar* com escopo de objeto. Não há mecanismo separado de marcação por objeto.

**P5 — o que se replica.**
- **Eventos:** sempre, a todos que as regras permitem.
- **Texto extraído e dados estruturados:** replicam como **resultados** dentro dos eventos; nada é reprocessado.
- **Blobs (o MHTML):** **espelho em segundo plano entre as máquinas próprias**; **sob demanda por hash com terceiros**, e só de hashes que o próprio par anunciou (seção 9). O espelho para quando o espaço livre em disco cai abaixo de um **limite configurável**.
- **Embeddings (vetores):** **não replicam**. Dependem do modelo e da versão; cada instância calcula os seus a partir do texto replicado. Custa CPU em cada máquina, mas evita carregar vetores incompatíveis.

**P7 — organização do objeto importado.** **O espaço é o limite** e cada espaço aponta para uma organização local, escolhida ao entrar nele:
- **espaço de terceiro:** por padrão cria-se uma organização dedicada ("Federado: <espaço>"), para os dados nunca se misturarem com os do dono e o isolamento por organização valer;
- **espaço próprio:** o administrador escolhe em qual organização sua cai, com padrão na principal;
- **objeto que já existe localmente:** permanece no `tenant` em que estava e só ganha a associação ao novo espaço (um objeto pode estar em vários, decisão 1);
- o objeto importado **mantém o UUID de origem** (`urn:uuid:`).

**P8 — auditoria.** Cada decisão de enviar ou receber registra quem (par/chave), quando, o objeto (`urn`), a **regra que decidiu** e o motivo.
- **`AuditLog`:** só decisões sobre `restrito` e `confidencial` e as concessões, **permitidas e bloqueadas** — é a trilha de compliance (campos `operation`, `outcome`, `reason`, `metadata`).
- **`PipelineEvent`:** o fluxo todo, de todos os níveis, como diário operacional. Os dois **não se fundem**.
- O registro **nunca guarda conteúdo**, só identificadores.
- O motor fica num **ponto único**, sucessor de `eventos_para_peer`.

### `Claim` e `Evidence` (decidido em 2026-10-04, último item da F0)

**Ponto de partida (levantado no código):** hoje **não existe nenhuma alegação como objeto**. Quatro extratores rodam em toda captura e gravam blocos de dados no `DocumentText` (`dados_estruturados_deterministico`, `_dom2parser`, `_extruct`, e a `EstruturacaoLLM` manual); `structured_data` (o campo vencedor) fica sempre `None`; nada cria `Artifact` de tipo pessoa ou empresa; nenhum extrator registra de onde na página veio o valor (a exceção é o `ParserSpec` do `dom2parser`, com localizador por campo); não há autoria nem confiança por item.

**Modelo**

- **`Claim`** (ID global `urn:uuid:` do próprio PK): sujeito e objeto (referência a uma entidade ou **identificador tipado**, como `cnpj:...`), predicado (propriedade do Schema.org ou do `ia:`), **autor da alegação** (quem afirma, ex.: o site ou o publicador), artefato de origem, **produtor** (extrator, versão e, se for LLM, o modelo), `extractor_confidence` (pode ser nula), nível de classificação herdado do artefato, estado (ativa ou retratada) e ligação opcional à alegação que ela revisa.
- **`Evidence`**: o ponteiro até a fonte — hash do blob (`ni:`), tipo e valor do localizador (seletor, tabela/linha/coluna, deslocamento no texto ou posição em JSON-LD) e um **trecho citado curto**.
- **Invariantes:** nenhuma alegação nasce sem evidência (criadas juntas, numa transação, por um serviço único); alegação **nunca se edita** — a correção cria outra e a antiga permanece (`prov:wasRevisionOf`); a retratação é um estado, não um apagamento.
- **`Artifact.info_type` não muda:** "fato" ali significa só "a captura aconteceu e a página mostrava isso". A alegação é separada da entidade e do fato.
- **Onde:** `apps/artifacts`. O envio como evento assinado e o mapeamento JSON-LD ficam para a F1/F2; nesta fase as tabelas são o registro primário e viram projeção do log depois.

**As quatro decisões**

1. **Granularidade:** uma alegação por **atributo ou relação de uma entidade** (o CNPJ de uma empresa, as partes de um processo), **não por linha de tabela**. Tabelas seguem como dado, com uma evidência no nível da tabela (coerente com o roteamento por `page_type`).
2. **Evidência com trecho literal:** o trecho citado (limite sugerido de **500 caracteres**) é conteúdo da página e fica no banco **sob a classificação do objeto**. É o que torna a evidência verificável.
3. **Sujeito e objeto sem criar entidades ainda:** referências por identificador tipado; a resolução de entidades e o `sameAs` ficam para o correlacionador.
4. **Primeiro produtor: o `extruct`** (dados declarados pelo publicador, autoria clara, localizador simples como `json-ld[n]`), para o modelo ser exercitado de verdade. O perfil de empresa e as partes do processo exigem alterar os extratores para registrarem o localizador, e ficam para depois.

**Implementado em 2026-10-04** (`apps/artifacts/alegacoes.py`, `referencias.py`, `extractors/claims_extruct.py`; modelos `Claim` e `Evidence`, migration `artifacts.0017`):

- **Serviço único** `registrar_alegacao`: valida tudo antes de gravar e cria a alegação com as evidências numa transação. **Idempotente** por `(artefato, chave)`, onde a chave é o hash de sujeito, predicado, objeto, autor, produtor e versão: reprocessar com o mesmo produtor não duplica; uma versão nova do produtor gera alegações novas e as antigas permanecem.
- **Imutabilidade no modelo:** só `estado` e `retratada_em` mudam; `delete()` é recusado; `Artifact` com alegações não pode ser apagado (`PROTECT`). O banco garante por `CHECK` que o objeto é uma referência **ou** um literal e que a confiança fica em [0, 1]. **Não há bloqueio de `UPDATE` direto no banco** (um `QuerySet.update()` ou SQL ainda altera): é proteção do modelo, não do banco.
- **Referências tipadas:** `urn:uuid:`, `cnpj:` (14 dígitos com DV válido), `url:`, `dominio:` e `mencao:ni:…#localizador`.
- **Produtor `extruct`:** só JSON-LD. Tipos e propriedades de uma lista curta e explícita (organização, pessoa e artigo; pessoa **sem** e-mail nem data de nascimento). O sujeito é o CNPJ válido, senão a URL, senão uma menção ancorada no blob; o autor é o domínio da captura (sem `www.`). Roda como a etapa `extracao.alegacoes` da extração, **sem nunca derrubá-la**; se o artefato ainda não tinha `blob_hash`, a etapa o calcula e grava.

**Confiança:** só `extractor_confidence` por enquanto; as demais dimensões da seção 5 (confiabilidade da fonte, corroboração, revisão humana) são calculadas localmente depois.

### `Space` — etapa 1 da F1 (implementada em 2026-10-04)

Só **estrutura**: o espaço agrupa objetos e **não concede acesso a ninguém** (o acesso segue por organização, `orgs_do_usuario`, em mais de uma dúzia de pontos das views) e **ainda não tem membros** (dependem da identidade e dos pares).

- **`Space`** (`apps/federacao`, tabela `federacao_space`): ID `urn:uuid:`, nome (normalizado, único por organização), descrição, **organização local** (decisão P7), `arquivado` e `criado_por`. **`EspacoArtefato`** liga um artefato a um espaço explícito; **um artefato pode estar em vários**. Apagar o artefato desfaz o vínculo e não o espaço.
- **Espaço padrão implícito, sem linha no banco:** "todos os artefatos da organização". Tem **ID estável derivado** da organização (`uuid5` com um espaço de nomes fixo — mudá-lo mudaria o ID de todos), para poder ser citado em regras e eventos como qualquer espaço. Um artefato novo já está nele, sem nenhum signal.
- **Serviço** (`apps/federacao/espacos.py`): `criar_espaco`, `arquivar`/`desarquivar` (arquivado não recebe novos artefatos e nada é apagado), `incluir_artefato` e `remover_artefato` (idempotentes), `artefatos_do_espaco` e `espacos_do_artefato` (tratam o explícito e o padrão do mesmo jeito) e `resolver_urn`.
- **Regra de inclusão:** só artefato da **mesma organização** do espaço entra; a exceção é o objeto `importado`, que mantém o `tenant` de origem e só ganha a associação (decisão P7) — um parâmetro explícito, **ainda sem uso** (não há importação). O admin aplica a regra estrita.
- **Fora desta etapa:** membros e papéis, acesso por espaço, o motor de regras (que usará o espaço como condição) e eventos assinados.

### Motor de regras de replicação — etapa 2 da F1 (implementado em 2026-10-04)

O motor da ADR 011, **ainda sem nenhum fluxo de envio ligado a ele** (não há envio entre instâncias): é uma **função pura** testada, mais a tabela de regras, o serviço de banco e um comando para simular decisões.

- **`apps/federacao/regras.py` (puro):** `avaliar(regras, contexto, agora)` devolve uma `Decisao` (permitido, motivo, origem `piso`/`regra`/`padrao-negar`, nível efetivo, a regra que decidiu, se era padrão, e as que casaram). Sem banco, sem rede, sem relógio próprio (`agora` entra de fora). Separado do `policy_engine`.
- **Pisos (só ao *enviar*, a terceiro):** `interno` nunca sai, nem com concessão; `restrito` e `confidencial` só saem por **concessão** — regra de *permitir* **para aquele objeto e aquele par, com validade ainda vigente e ativa**. Uma regra permissiva comum não basta. Rótulo desconhecido vale `confidencial`. Ao *receber* não há pisos.
- **Precedência:** negar vence (em qualquer ordem), senão permitir, senão **negar por padrão**. Toda condição preenchida precisa bater; as condições são **sentido (enviar/receber), par (nome/chave), tipo de par, nível, tipo de objeto, espaço, objeto e validade**.
- **`RegraReplicacao`** (por organização; tabela `federacao_regra_replicacao`, migration `federacao.0003`): validada **também fora do admin** (nível, URNs normalizadas, `par_ref` aparado). Regra com `objeto_urn` **exige** `valida_ate` (também por `CHECK` no banco) e, se for *permitir*, um `par_ref`.
- **Regras padrão**, semeadas por organização (`enviar-proprio-tudo`, `enviar-terceiro-publico`, `receber-proprio-tudo`, `receber-terceiro-tudo`), todas *permitir*. **Desative, não apague** (o admin não deixa apagar): sem `completo`, a semeadura só age se a organização não tem **nenhuma** padrão, então uma desativada não ressurge. A primeira `decidir` de uma organização as semeia.
- **`apps/federacao/politica.py`:** `decidir`/`decidir_artefato` usam as regras **da organização dona do dado** (as de outra organização não valem); `conceder`/`revogar` (concessão exige validade no futuro e par; revogar desativa e não recolhe cópias); `registrar_decisao`, o rastro da P8: **`PipelineEvent`** `federacao.decisao` para toda decisão e **`AuditLog`** só para `restrito`/`confidencial` (permitidas e bloqueadas), **sem conteúdo**.
- **`manage.py regras_replicacao`:** `--semear`, `--listar` e `--decidir` (simula e explica "por que isto não vai para o par X?"; **não registra nada**).
- **Provisório:** o par é um `par_ref` em texto e um `par_tipo`; o cadastro de pares (`Par`) ainda não existe.

### `Par` e enrolamento — Marco A do controle entre instâncias (implementado em 2026-10-04)

Primeira peça do controle das instâncias do mesmo dono (plano: controlar modelos do Ollama pelos painéis). Decisão 4 da ADR 011, agora em código:

- **`Maquina` é o `Par`** (fusão, sem renomear): ganhou `eh_local`, `did`, `tipo` (próprio/terceiro), `estado` (pendente/confirmado/revogado), `impressao_digital_conferida_em/por`, `endpoint_controle`, `capacidades_json`, `ultimo_pull_em/erro`. Uma única `Maquina` local por organização (`CHECK`), e `did` único por organização. O **roteador de LLM só considera pares `confirmado`**.
- **Convite** (`ConviteEnrolamento`, hash do token, 24 h, uso único) e **enrolamento sem TOFU**: A cria o convite; B cola o código, vê a **impressão digital** de A (80 bits do SHA-256 do DID, `a1b2-c3d4-e5f6-0718-9abc`) e aceita com um `POST /federacao/convite/aceitar/` **assinado pela chave de B**; A confere a assinatura, consome o token e responde **assinando a resposta**; cada lado fica com o outro `pendente`. **Só "Confirmar" (dono/admin, depois de conferir a impressão digital por fora do canal) leva a `confirmado`.** Revogar corta o canal sem recolher cópias.
- **Primitivas do canal** (`apps/federacao/canal.py`): mensagem canônica `ia-ctrl-v1` que cobre método, rota+query, timestamp, nonce, DID de origem, DID de **destino** e o hash do corpo; janela de ±60 s; nonce no cache Redis (`cache.add`, atômico) **queimado só por requisição autêntica**; resposta assinada e vinculada ao nonce. Erros devolvem sempre a mesma recusa; o motivo (`codigo`) só vai ao log local (`PipelineEvent` `federacao.canal`).
- **Endereço do par validado** (`apps/federacao/endpoints.py`, anti-SSRF): link-local/multicast/reservado sempre recusados, HTTP só em rede privada/VPN (inclui CGNAT do Tailscale e `*.ts.net`), sem usuário/senha/caminho. **Limite:** o nome é checado no cadastro, não na conexão (DNS rebinding).
- **Papéis, pela primeira vez** (`apps/accounts/permissoes.py`): `eh_admin`, `exige_admin`, `orgs_onde_e_admin`. Só `OWNER`/`ADMIN` vigentes convidam, aceitam, confirmam, mudam o tipo e revogam; qualquer membro **vê**. `is_staff` não administra organização alheia. `orgs_do_usuario` não mudou.
- **Telas** `/cluster/pares/`: lista com impressão digital, convite (código mostrado **uma vez**, não guardado), pré-visualização com a impressão digital do convite, aceitar, confirmar (exige marcar "conferi"), mudar tipo, revogar. O texto de "**Próprio dá poder**" aparece nos pontos de decisão e marcar próprio exige confirmação explícita.
- **Auditoria:** `AuditLog` `par.convite_criado|convite_aceito|convite_recebido|confirmado|revogado|tipo_alterado`, sem token.
- **Removidos junto:** `EventoReplicacao`, o endpoint `/cluster/api/v1/replicacao/eventos/`, `signals_replicacao`, `replicacao.py`, `provisionamento.py`, o comando `registrar_maquina` e o `token_hash` (o endpoint de replicação era o único que o usava). Também as rotas mortas da allowlist do middleware.
- **Fora desta etapa:** o controle em si (rotas de comando e `estado`), o pull periódico e os modelos do Ollama — marcos B a F.

## 15. Fora de escopo deste documento

Replicação de infraestrutura (Cenário A), roteamento de LLM entre nós (já em `apps/cluster/` e ADR 009), e qualquer implementação. Nenhuma alteração de código foi feita junto com esta análise.

## 16. Política de replicação — decisões pendentes

Quem recebe o quê. Parte do que já foi decidido: a política do **espaço** é determinística, com negação por padrão, e o **nível de confiança por par** é {ignorar, quarentena, aceitar só como alegação, aceitar e repassar} (seção 11), agora acrescido de **próprio** (máquina do mesmo dono). Cada item traz a recomendação; nenhum está decidido.

- [x] **P1. Níveis de confiança por par.** **Decidido: dois campos no `Par` — tipo (quem é) e confiança (o que faço com o que ele manda).** Ver "Política de replicação: decisões P1, P2, P4, P5, P7 e P8" abaixo.
- [x] **P2. Quem decide o que sai.** **Decidido: interseção, com negação por padrão dos dois lados.** Ver abaixo.
- [x] **P3. Teto de classificação por tipo de par → motor de regras de replicação.** **Decidido: motor determinístico em três camadas (pisos, padrões, regras do usuário), negar vence.** Ver "Motor de regras de replicação" abaixo.
- [x] **P4. Granularidade da autorização.** **Decidido: tudo é regra; a granularidade vem das condições.** Ver abaixo.
- [x] **P5. O que se replica.** **Decidido: eventos sempre; blobs em espelho entre as próprias máquinas e sob demanda com terceiros; vetores não replicam.** Ver abaixo.
- [x] **P6. Como uma máquina passa a ser "minha".** **Decidido: cadastro de pares por convite com conferência da impressão digital, tipo atribuído localmente por cada lado, `Par` fundido com `Maquina`.** Ver "Cadastro de pares e enrolamento" abaixo.
- [x] **P7. Organização do objeto importado.** **Decidido: o espaço é o limite e aponta para uma organização local.** Ver abaixo.
- [x] **P8. Auditoria.** **Decidido: `AuditLog` para `restrito`/`confidencial` e concessões; `PipelineEvent` para o fluxo todo.** Ver abaixo.

## Referências no repositório

- `docs/arquitetura/decisoes/006-cluster-adaptativo-multiproprietario.md` — cluster, `Projeto`, motor de posicionamento
- `docs/seguranca/compartilhamento.md` — `Sharing`, `Projeto`, isolamento por organização
- `docs/seguranca/classificacao.md` — níveis de classificação
- `docs/componentes/observabilidade.md` — `PipelineEvent`/`PipelineRun`
- `services/portal/apps/cluster/replicacao.py` e `signals_replicacao.py` — replicação atual
- `services/portal/apps/artifacts/models.py` — `Artifact`, `ArtifactLineage`, `Sharing`
