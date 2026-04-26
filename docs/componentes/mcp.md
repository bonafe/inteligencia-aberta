# Componente: MCP (Model Context Protocol)

## Responsabilidade

Ser a única ponte entre os agentes de IA e a infraestrutura. O MCP descobre ferramentas disponíveis, aplica políticas de confidencialidade antes de cada execução, e instancia contêineres sob demanda.

Nenhum agente acessa serviço externo diretamente. Toda chamada passa pelo MCP.

## Subcomponentes

### 1. Registro de Ferramentas

Catálogo de todas as ferramentas disponíveis no sistema. Atualizado automaticamente quando contêineres sobem ou descem.

```json
{
  "tool_id": "consultar_cnpj",
  "descricao": "Busca dados cadastrais de empresa na Receita Federal pelo CNPJ.",
  "parametros": {
    "cnpj": { "tipo": "string", "formato": "XX.XXX.XXX/XXXX-XX" }
  },
  "classificacao_minima_permitida": "público",
  "container": "scraper-receita-federal",
  "timeout_ms": 10000
}
```

### 2. Motor de Políticas

Componente determinístico (sem LLM) que valida se uma ferramenta pode ser executada dado o contexto da requisição.

**Regras aplicadas antes de cada execução:**

| Condição | Ação |
|---|---|
| Dado `confidencial` + LLM externo | BLOQUEAR |
| Dado `restrito` + LLM externo | BLOQUEAR |
| Ferramenta requer classificação `restrito` + dado `público` | BLOQUEAR |
| Organização A tentando acessar dado da Organização B | BLOQUEAR |
| Ferramenta não registrada | BLOQUEAR |
| Todas as condições OK | PERMITIR + REGISTRAR |

Toda decisão de bloqueio é registrada com: `tool_id`, `motivo`, `classificacao_do_dado`, `tenant_id`, `timestamp`.

### 3. Executor

Responsável por:
- Chamar o contêiner da ferramenta com os parâmetros validados.
- Instanciar o contêiner se não estiver rodando (via Docker API).
- Gerenciar novas tentativas (padrão: 3 tentativas com backoff exponencial).
- Retornar resultado ao agente solicitante.

### 4. Gerenciador de Contêineres

Instancia e encerra contêineres sob demanda. Mantém um conjunto de contêineres ativos para ferramentas de uso frequente.

## Contrato de Ferramenta

Toda ferramenta deve implementar este contrato:

**Entrada:**
```json
{
  "tool_id": "consultar_cnpj",
  "parametros": { "cnpj": "00.000.000/0001-00" },
  "contexto": {
    "investigacao_id": "uuid",
    "tenant_id": "...",
    "classificacao": "público"
  }
}
```

**Saída (sucesso):**
```json
{
  "status": "ok",
  "resultado": { ... },
  "fonte": {
    "origem": "Receita Federal",
    "url": "...",
    "data_coleta": "2026-04-25T10:00:00Z"
  },
  "confianca": 0.95
}
```

**Saída (erro):**
```json
{
  "status": "erro",
  "codigo": "FONTE_INDISPONIVEL | TIMEOUT | DADO_NAO_ENCONTRADO | BLOQUEADO_POR_POLITICA",
  "mensagem": "...",
  "recuperavel": true
}
```

## Ferramentas Iniciais

| Ferramenta | Fonte | Classificação mínima |
|---|---|---|
| `consultar_cnpj` | Receita Federal | público |
| `buscar_processos` | CNJ / Tribunais | público |
| `buscar_noticias` | APIs de notícias / scraping | público |
| `consultar_ans` | ANS | público |
| `buscar_documentos_internos` | MinIO (dados do usuário) | restrito |
| `buscar_semantico` | Qdrant / pgvector | depende do índice |
| `gerar_embedding` | LLM configurado | depende da classificação |

## Critérios de Aceitação

- [ ] Motor de Políticas rejeita 100% das tentativas de envio de dado confidencial para LLM externo.
- [ ] Registro atualiza automaticamente quando contêiner sobe ou desce (< 30 segundos de latência).
- [ ] Toda execução de ferramenta tem registro com: `tool_id`, `tenant_id`, `classificacao`, `resultado`, `duracao_ms`.
- [ ] Gerenciador de Contêineres instancia contêiner em < 10 segundos para ferramentas com imagem já baixada.
- [ ] Falha de um contêiner de ferramenta não afeta outros contêineres.
- [ ] Nenhuma ferramenta tem acesso direto ao banco de dados de outra organização.
