# Federação entre instâncias — análise arquitetural

**Status:** proposta em discussão — **nada aqui está implementado nem decidido**. Escrita em 2026-10-03 para retomar a conversa depois; as decisões pendentes estão na seção 14. Quando fechadas, viram a ADR 010.

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
    "ia": "https://vocab.example.org/ia/v1#"
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

Cada item traz a recomendação da análise; nenhum está decidido.

- [ ] **1. Granularidade do espaço.** Um objeto pode estar em vários espaços ou só em um? *Recomendação: vários; a exportação é por espaço.*
- [ ] **2. Identidade do usuário.** Chave pessoal portátil desde a F0, ou só chave por instância no início? *Sem recomendação firme: chave por instância é mais simples; chave pessoal custa pouco se o envelope já tiver `author` separado de `actor` (como no exemplo), mas traz o problema de guarda e recuperação da chave.*
- [ ] **3. Neo4j.** O código não o tem. Tratar o grafo como **projeção**, nunca como fonte de verdade? *Recomendação: sim.*
- [ ] **4. Vários escritores no mesmo espaço desde o início?** Decide entre logs por autor (simples) e DAG (pesado). *Recomendação: logs por autor na v1.*
- [ ] **5. Apagamento legal.** Cifra por objeto com destruição de chave é requisito, ou basta tombstone? *Se for requisito, precisa estar no envelope desde a F2 — é difícil de acrescentar depois.*
- [ ] **6. Cenários A e B.** Manter `apps/cluster` como está (A) e criar um app novo para o B, sem misturá-los? *Recomendação: sim; e revisar a ADR 006, que mistura os dois.*
- [ ] **7. Nome do conceito** (Space, Context, Dataset, Collection…) e do namespace `ia:` (domínio do vocabulário próprio).
- [ ] **8. Esquema de IDs** (`urn:uuid:` simples vs. prefixo da instância/DID) e **formato de hash** (RFC 6920 `ni:` vs. multihash).

## 15. Fora de escopo deste documento

Replicação de infraestrutura (Cenário A), roteamento de LLM entre nós (já em `apps/cluster/` e ADR 009), e qualquer implementação. Nenhuma alteração de código foi feita junto com esta análise.

## Referências no repositório

- `docs/arquitetura/decisoes/006-cluster-adaptativo-multiproprietario.md` — cluster, `Projeto`, motor de posicionamento
- `docs/seguranca/compartilhamento.md` — `Sharing`, `Projeto`, isolamento por organização
- `docs/seguranca/classificacao.md` — níveis de classificação
- `docs/componentes/observabilidade.md` — `PipelineEvent`/`PipelineRun`
- `services/portal/apps/cluster/replicacao.py` e `signals_replicacao.py` — replicação atual
- `services/portal/apps/artifacts/models.py` — `Artifact`, `ArtifactLineage`, `Sharing`
