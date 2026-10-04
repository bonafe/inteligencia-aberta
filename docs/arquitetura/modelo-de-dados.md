# Modelo de Dados

## Entidades Principais

### Artefato de Inteligência

Unidade fundamental de dado no sistema. Representa entidades do mundo real coletadas ou produzidas durante uma investigação. Apenas estes seis tipos são artefatos de inteligência — texto extraído e fragmentos de RAG são modelos de pipeline separados (ver abaixo).

```json
{
  "id": "uuid-v4",
  "tipo": "empresa | pessoa | documento | processo | endereço | evento",
  "conteudo": { ... },
  "classificacao": {
    "nivel": "público | interno | restrito | confidencial",
    "tenant_id": "org_fulano | org_receita_federal | ...",
    "permitir_llm_externo": true,
    "compartilhamento": [],
    "expira_em": null
  },
  "proveniencia": {
    "fontes": [
      {
        "origem": "Receita Federal",
        "url": "...",
        "data_coleta": "2026-04-25T10:00:00Z",
        "agente": "coletor-v1",
        "confianca": 0.95
      }
    ],
    "tipo_informacao": "fato | opinião | inferência"
  },
  "criado_em": "2026-04-25T10:00:00Z",
  "atualizado_em": "2026-04-25T10:00:00Z"
}
```

**Regras:**
- `tipo_informacao` é obrigatório. Nunca omitido.
- `confianca` entre 0 e 1. Artefatos sem fonte verificável recebem `confianca < 0.5`.
- `nivel` de classificação é imutável após criação — reclassificar é criar novo artefato.

---

### Pessoa Física

```json
{
  "tipo": "pessoa",
  "conteudo": {
    "nome": "...",
    "cpf": "...",
    "data_nascimento": "...",
    "enderecos": ["uuid-endereco"],
    "vinculos": ["uuid-empresa", "uuid-processo"]
  }
}
```

### Pessoa Jurídica (Empresa)

```json
{
  "tipo": "empresa",
  "conteudo": {
    "razao_social": "...",
    "nome_fantasia": "...",
    "cnpj": "...",
    "situacao": "ativa | baixada | inapta | suspensa",
    "data_abertura": "...",
    "socios": ["uuid-pessoa"],
    "enderecos": ["uuid-endereco"],
    "contratos_publicos": ["uuid-contrato"]
  }
}
```

### Processo

```json
{
  "tipo": "processo",
  "conteudo": {
    "numero": "1234567-89.2026.8.26.0000",
    "tribunal": "TJSP",
    "classe": "Ação Civil Pública",
    "partes": {
      "polo_ativo": ["uuid-pessoa"],
      "polo_passivo": ["uuid-empresa"]
    },
    "situacao": "em andamento | encerrado | suspenso",
    "ultima_movimentacao": "2026-04-20T00:00:00Z",
    "proxima_audiencia": null
  }
}
```

---

## Modelos de Pipeline

Texto extraído e fragmentos de RAG **não são artefatos de inteligência**. São artefatos de infraestrutura de pipeline com modelos próprios, derivados de um `documento` mas não compartilhando o mesmo contrato semântico (sem `info_type`, sem `sources` independentes).

### DocumentText

Texto limpo extraído de um artefato `documento`. Relação 1:1 com o documento de origem.

```python
class DocumentText(Model):
    id                  = UUIDField(primary_key=True)
    document            = OneToOneField(Artifact, related_name="extracted_text")
    text                = TextField
    title               = CharField
    source_url          = CharField
    page_type           = CharField          # "artigo", "tabular_financeiro", etc.
    detection_confidence = FloatField
    detection_source    = CharField          # "cache" | "structural_analysis"
    url_pattern_cache   = FK(URLPatternCache, null=True)
    structured_data     = JSONField(null=True)      # vencedor entre as estratégias: decisão adiada, hoje sempre nulo
    full_text           = TextField(null=True)      # texto bruto da página (inclui menus e rodapé)
    dados_estruturados_deterministico = JSONField(null=True)   # extrator por page_type
    dados_estruturados_dom2parser     = JSONField(null=True)   # estrutura inferida por repetição no DOM
    dados_estruturados_extruct        = JSONField(null=True)   # JSON-LD, microdata e OpenGraph declarados pelo site
    extractor_version   = CharField
    char_count          = IntegerField
    word_count          = IntegerField
    created_at          = DateTimeField
    updated_at          = DateTimeField
```

### DocumentFragment

Chunk de texto para busca semântica (RAG). Pertence a um `DocumentText`.

```python
class DocumentFragment(Model):
    id               = UUIDField(primary_key=True)
    document_text    = ForeignKey(DocumentText, related_name="fragments")
    text             = TextField
    fragment_index   = IntegerField
    total_fragments  = IntegerField
    qdrant_point_id  = CharField(blank=True)   # preenchido após embed_fragment
    qdrant_collection = CharField(blank=True)
    created_at       = DateTimeField
    updated_at       = DateTimeField
```

Classificação e tenant para isolamento no Qdrant são derivados de `fragment.document_text.document`.

---

## Modelos do conhecimento, da federação e do cluster

Introduzidos a partir da ADR 010. Os IDs são UUIDs; o **identificador global** de qualquer objeto é o próprio PK escrito como `urn:uuid:<uuid>` (`apps.federacao.ids`). O hash de conteúdo é RFC 6920, `ni:///sha-256;<base64url>`.

### Identidade de conteúdo e de instância

```python
class Artifact(Model):                       # campo novo
    blob_hash = CharField(null=True, db_index=True)   # ni: do MHTML; NÃO é único (duas capturas iguais repetem o hash)

class ChaveInstancia(Model):                 # apps/federacao — a chave Ed25519 desta instância
    did             = CharField(unique=True)           # did:key — contém a chave pública
    privada_cifrada = TextField                        # Fernet; nunca sai do modelo, nem é exibida
    estado          = CharField                        # ativa | aposentada | comprometida (no máximo uma ativa)
```

### Alegação e evidência

Separam o que uma fonte **diz** do que o sistema **sabe**. Nada em `Claim` afirma que é verdade.

```python
class Claim(Model):                          # apps/artifacts — imutável (só estado e retratada_em mudam)
    tenant, artefato                                   # FK Organization, FK Artifact (PROTECT)
    sujeito_ref, predicado                             # referência tipada; "schema:taxID", "ia:…", "prov:…"
    objeto_ref | objeto_literal                        # exatamente um dos dois (CHECK no banco)
    autor_ref                                          # quem afirma, ex.: dominio:jornal.example
    produtor, produtor_versao, modelo                  # extrator (e modelo, se for LLM)
    extractor_confidence = FloatField(null=True)       # em [0, 1] (CHECK)
    classification_level                               # herdado do artefato
    estado = "ativa" | "retratada"; revisa = FK(self)  # correção = outra alegação; a antiga permanece
    chave                                              # hash de sujeito+predicado+objeto+autor+produtor+versão (idempotência)

class Evidence(Model):                       # imutável; toda Claim nasce com ao menos uma
    claim, blob_hash                                   # ni: do blob de onde veio
    localizador_tipo, localizador                      # ex.: extruct.json-ld + {"item": "json-ld[0]", "propriedade": "taxID"}
    trecho = CharField(max_length=500)                 # sob a classificação da alegação
```

Referências tipadas: `urn:uuid:`, `cnpj:` (14 dígitos com dígito verificador válido), `url:`, `dominio:` e `mencao:ni:…#localizador`. Entidades ainda não existem como objetos; a resolução e o `sameAs` ficam para o correlacionador.

### Espaço e política de replicação

```python
class Space(Model):                          # apps/federacao — agrupa artefatos; um artefato pode estar em vários
    nome, descricao, organizacao                       # a organização LOCAL do espaço (único por organização)
    arquivado = BooleanField                           # não recebe novos artefatos; nada é apagado
class EspacoArtefato(Model):  espaco, artefato         # só o espaço explícito usa esta tabela

# O espaço padrão de cada organização é IMPLÍCITO: não tem linha — "todos os artefatos da organização" —
# e tem ID derivado por uuid5, para poder ser citado em regras como qualquer espaço.

class RegraReplicacao(Model):                # apps/federacao — o motor decide em apps/federacao/regras.py (função pura)
    organizacao, efeito (permitir | negar), sentido (enviar | receber)
    par_ref, par_tipo, nivel, tipo_objeto, espaco_urn, objeto_urn, valida_ate   # None = qualquer
    ativa, padrao, chave_padrao                        # as padrão se desativam, não se apagam
    # regra com objeto_urn (concessão) EXIGE valida_ate (CHECK) e, sendo permitir, um par_ref

class RegraClassificacaoDominio(Model):      # apps/artifacts — "tudo de bancodobrasil.com.br nasce, no mínimo, confidencial"
    tenant, dominio, nivel, ativa                      # só sobe o nível; casa por sufixo de rótulo; não reclassifica o existente
```

### Máquinas, pares e operações de modelo

```python
class Maquina(Model):                        # apps/cluster — esta instância (eh_local) ou um PAR remoto
    organizacao, dono, apelido
    eh_local, did, tipo (proprio | terceiro), estado (pendente | confirmado | revogado)
    impressao_digital_conferida_em, conferida_por      # só a conferência da impressão digital leva a confirmado
    endpoint_controle, ollama_endpoint, gateway_endpoint
    ultimo_pull_em, ultimo_pull_erro, pull_falhas      # estado puxado do par (último conhecido)

class MaquinaStatus(Model):                  # projeção: CPU/RAM/disco, ollama_disponivel/versao, disco_ollama_livre_gb
class MaquinaModeloOllama(Model):            # modelo instalado: tamanho, digest, família, quantização, carregado, tokens/s
class ConviteEnrolamento(Model):             # token de uso único (só o hash é guardado), 24 h, tipo que será dado ao par

class OperacaoModeloOllama(Model):           # instalar (pull) ou remover (delete) um modelo de uma máquina
    maquina, tipo, modelo, status (pendente | executando | concluida | falhou | cancelada)
    bytes_total, bytes_concluidos, fase, erro, solicitado_por, origem_par, ator_afirmado, operacao_remota_id
    # no máximo UMA operação ativa por (maquina, modelo) — índice parcial no banco
```

---

## Grafo de Conhecimento

O grafo é a representação central para análise de vínculos. Ele é uma **projeção** do log de eventos e dos modelos acima, reconstruível a partir deles — nunca a fonte de verdade ([ADR 010](decisoes/010-federacao-por-log-assinado.md)). A tecnologia (Neo4j ou tabelas de arestas no Postgres) será decidida na Fase 2; **hoje não existe código de grafo**, só esta especificação.

### Nós (Entidades)

| Nó | Propriedades-chave |
|---|---|
| `Pessoa` | cpf, nome |
| `Empresa` | cnpj, razao_social, situacao |
| `Endereço` | cep, logradouro, municipio |
| `Documento` | tipo, hash, data |
| `Processo` | numero, tribunal, classe |
| `Fonte` | url, origem, data_coleta |

Cada nó carrega o `artefato_id` correspondente — o grafo não replica dados, referencia artefatos.

### Arestas (Vínculos)

| Aresta | De → Para | Propriedades |
|---|---|---|
| `SÓCIO_DE` | Pessoa → Empresa | data_entrada, data_saída, participação |
| `ADMINISTRADOR_DE` | Pessoa → Empresa | cargo, data_início |
| `RESIDE_EM` | Pessoa → Endereço | data_início, data_fim |
| `SEDE_EM` | Empresa → Endereço | data_início |
| `PARTE_EM` | Pessoa/Empresa → Processo | polo (ativo/passivo) |
| `CITADO_EM` | Pessoa/Empresa → Documento | contexto |
| `RELACIONADO_A` | Empresa → Empresa | tipo_relação, fonte |
| `VERIFICADO_POR` | Nó → Fonte | data_verificação, confianca |

### Exemplo de Consulta (Cypher)

```cypher
-- Empresas ativas com sócio em comum com a empresa investigada
MATCH (alvo:Empresa {cnpj: '00.000.000/0001-00'})
      <-[:SÓCIO_DE]-(socio:Pessoa)
      -[:SÓCIO_DE]->(outra:Empresa)
WHERE outra.situacao = 'ativa'
RETURN socio.nome, outra.razao_social, outra.cnpj
```

---

## Classificação de Dados

Metadados de classificação presentes em todo artefato. Ver especificação completa em [`../seguranca/classificacao.md`](../seguranca/classificacao.md).

| Nível | Descrição | LLM Externo | Compartilhamento |
|---|---|---|---|
| `público` | Dados de fontes abertas | Permitido | Livre |
| `interno` | Dados dentro de uma organização | Permitido com auditoria | Restrito à organização |
| `restrito` | Dados pessoais do usuário | Bloqueado | Explícito e limitado |
| `confidencial` | Dados altamente sensíveis | Bloqueado | Explícito, temporário e revogável |

---

## Armazenamento por Tipo de Dado

| Dado | Onde |
|---|---|
| Artefatos estruturados | PostgreSQL |
| Embeddings (para RAG) | Qdrant / pgvector |
| Grafo de vínculos | **Projeção** do log de eventos (ADR 010); a tecnologia — Neo4j ou tabelas de arestas no Postgres — será decidida na Fase 2 |
| Arquivos brutos (PDFs, imagens, áudios) | Garage (S3) |
| Estado de sessão e orquestração | PostgreSQL + Redis |
| Registros de auditoria | PostgreSQL (somente inserção, sem exclusão) |

---

## Invariantes do Modelo

Estas regras nunca podem ser violadas:

1. Nenhum artefato existe sem pelo menos uma fonte associada.
2. `tipo_informacao` é sempre explícito (`fato`, `opinião` ou `inferência`).
3. Registros de auditoria são somente inserção — nenhum processo tem permissão de `DELETE` nessa tabela.
4. Dados classificados como `restrito` ou `confidencial` não têm embeddings em índice compartilhado — usam índice isolado por organização.
5. Revogação de compartilhamento bloqueia acesso imediatamente — sem janela de graça.
6. Uma alegação (`Claim`) nunca se edita nem se apaga — só se retrata ou se revisa com outra — e **nasce sempre com ao menos uma evidência**, pelo serviço único `registrar_alegacao`.
7. O nível de classificação viaja com o objeto e nenhuma regra o rebaixa. Ao **enviar** a um terceiro: `interno` nunca sai; `restrito` e `confidencial` só saem por concessão explícita (para aquele objeto e aquele par, com validade); rótulo desconhecido vale `confidencial`.
8. Na replicação, **negar vence** e, sem regra que permita, nega (negação por padrão).
9. Um par só entra no roteamento de LLM e no canal de controle depois de **confirmado** pela conferência da impressão digital por um administrador; revogar corta o canal sem recolher o que já foi copiado.
10. No máximo uma operação de modelo ativa por (máquina, modelo); uma máquina **offline** nunca recebe comando (falha na hora).
