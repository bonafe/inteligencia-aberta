# Componente: Pipeline RAG

## Responsabilidade

Permitir que os agentes consultem grandes volumes de documentos do usuário com busca semântica — encontrando trechos relevantes por significado, não por palavra-chave.

## Fluxo de Indexação

```
Documento recebido (PDF, imagem, texto)
    ↓
[Extrator] OCR se necessário → texto bruto
    ↓
[Fragmentação] Divisão em trechos com sobreposição (fragmento: 512 tokens, sobreposição: 64)
    ↓
[Embedding] Geração de vetor semântico por fragmento
    ↓
[Índice isolado por organização] Armazenamento no Qdrant / pgvector
```

## Isolamento por Organização e Classificação

Cada organização tem seu próprio namespace no banco vetorial. Buscas nunca cruzam namespaces.

Dados `confidencial` e `restrito` usam modelo de embedding local para geração de vetores. Dados `público` podem usar APIs externas de embedding.

## Fluxo de Recuperação

```
Consulta do agente
    ↓
[Embedding da consulta] Mesmo modelo usado na indexação
    ↓
[Busca por similaridade] Top-K fragmentos mais relevantes (padrão K=5)
    ↓
[Re-ranking] Ordena por relevância ao contexto da investigação
    ↓
[Injeção no prompt] Fragmentos injetados como contexto para o LLM
    ↓
[Resposta com citação] LLM responde referenciando os fragmentos usados
```

## Metadados por Fragmento

Cada fragmento é armazenado como `DocumentFragment` no PostgreSQL e como ponto vetorial no Qdrant. O payload do Qdrant referencia a cadeia de origem:

```json
{
  "fragment_id": "uuid-do-fragment",
  "document_text_id": "uuid-do-document-text",
  "document_artifact_id": "uuid-do-artefato-documento",
  "tenant_id": "uuid-da-organizacao",
  "fragment_index": 3,
  "classification_level": "confidencial",
  "source_url": "https://...",
  "title": "...",
  "text_preview": "primeiros 200 caracteres...",
  "created_at": "2026-05-20T..."
}
```

Nota: `classification_level` e `tenant_id` são derivados do artefato `documento` de origem — não são armazenados diretamente no `DocumentFragment`.

## Critérios de Aceitação

- [ ] Busca semântica retorna resultados em < 2 segundos para índices de até 100k fragmentos.
- [ ] Resultado de busca de uma organização nunca inclui fragmentos de outra organização.
- [ ] Modelo de embedding local usado para dados `restrito` e `confidencial`.
- [ ] Cada fragmento na resposta inclui referência ao documento e página de origem.
- [ ] Documento excluído pelo usuário remove todos os fragmentos do índice em < 60 segundos.
- [ ] Indexação de documento de 100 páginas concluída em < 5 minutos.
