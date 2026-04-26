# Modelo de Dados

## Entidades Principais

### Artefato de Inteligência

Unidade fundamental de dado no sistema. Todo dado coletado ou produzido é um artefato.

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

## Grafo de Conhecimento

O grafo é a representação central para análise de vínculos. Neo4j como mecanismo de armazenamento.

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
| Grafo de vínculos | Neo4j |
| Arquivos brutos (PDFs, imagens, áudios) | MinIO |
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
