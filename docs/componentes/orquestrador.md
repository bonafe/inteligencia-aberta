# Componente: Orquestrador (LangGraph)

## Responsabilidade

Gerenciar o ciclo completo de uma investigação como uma máquina de estados explícita. O orquestrador não processa dados — ele controla qual agente executa quando, persiste o estado entre etapas e decide se avança, retrocede ou encerra.

## Estados do Ciclo de Inteligência

```
RECEBIDO → PLANEJADO → COLETANDO → EXTRAINDO → CORRELACIONANDO
                                                      ↓
                          ENTREGUE ← REDIGIDO ← VALIDADO ← ANALISANDO
```

Estados terminais: `ENTREGUE`, `FALHOU`, `CANCELADO`

### Definição de cada estado

| Estado | Descrição | Agente responsável |
|---|---|---|
| `RECEBIDO` | Entrada do usuário recebida e classificada | Motor de Políticas |
| `PLANEJADO` | Estratégia definida: fontes, agentes, ferramentas | Planejador |
| `COLETANDO` | Dados sendo obtidos de fontes externas | Coletor(es) |
| `EXTRAINDO` | Informações específicas sendo retiradas de documentos brutos | Extrator |
| `CORRELACIONANDO` | Conexões e padrões sendo identificados | Correlacionador |
| `ANALISANDO` | Insights sendo gerados | Analista |
| `VALIDANDO` | Qualidade e veracidade sendo auditadas | Validador |
| `REDIGINDO` | Relatório final sendo produzido | Redator |
| `ENTREGUE` | Resultado entregue ao usuário | — |
| `FALHOU` | Erro irrecuperável — usuário notificado | — |
| `CANCELADO` | Cancelado pelo usuário ou por política | — |

## Transições e Condições

- `PLANEJADO → COLETANDO`: Sempre. O planejador sempre gera ao menos uma fonte para coletar.
- `COLETANDO → EXTRAINDO`: Quando ao menos um dado foi coletado com sucesso.
- `COLETANDO → FALHOU`: Se todas as tentativas de coleta falharam após novas tentativas.
- `VALIDANDO → REDIGINDO`: Se confiança mínima atingida (configurável, padrão: 0.6).
- `VALIDANDO → ANALISANDO`: Se validador rejeitar dados — retorna para nova análise com marcação de baixa confiança.
- Qualquer estado → `CANCELADO`: Mediante solicitação do usuário ou tempo limite configurável.

## Persistência de Estado

O estado completo de cada investigação é persistido no PostgreSQL após cada transição. Investigações podem ser retomadas após falha ou interrupção.

```json
{
  "investigacao_id": "uuid",
  "tenant_id": "...",
  "estado_atual": "COLETANDO",
  "classificacao": "público",
  "historico_estados": [
    { "estado": "RECEBIDO", "timestamp": "...", "duracao_ms": 120 },
    { "estado": "PLANEJADO", "timestamp": "...", "duracao_ms": 3400 }
  ],
  "artefatos_coletados": ["uuid-1", "uuid-2"],
  "plano": { ... },
  "erros": []
}
```

## Contrato com os Agentes

O orquestrador passa para cada agente:

```json
{
  "investigacao_id": "uuid",
  "estado_atual": "COLETANDO",
  "classificacao": "público",
  "contexto": { "query_original": "...", "plano": { ... } },
  "artefatos_disponiveis": ["uuid-1"],
  "instrucoes": "Colete dados de CNPJ para as empresas listadas no plano."
}
```

O agente retorna:

```json
{
  "artefatos_produzidos": ["uuid-3", "uuid-4"],
  "proximo_estado_sugerido": "EXTRAINDO",
  "confianca": 0.88,
  "erros": []
}
```

O orquestrador decide a transição — o agente apenas sugere.

## Critérios de Aceitação

- [ ] Estado persiste após reinício do contêiner.
- [ ] Transições são atômicas — não existe estado intermediário visível externamente.
- [ ] Tempo limite por estado: configurável globalmente e por investigação. Padrão: 5 minutos por estado.
- [ ] Cancelamento do usuário processa em < 2 segundos, qualquer estado.
- [ ] Histórico de estados é somente inserção — imutável.
- [ ] Investigações simultâneas do mesmo usuário: no mínimo 5 paralelas.
