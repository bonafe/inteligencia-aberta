# Segurança: Classificação e Controle de Dados

## Modelo de Classificação

Todo dado no sistema carrega um nível de classificação. A classificação determina o que pode ser feito com o dado — não é apenas metadado, é controle de execução.

| Nível | Definição | Exemplos |
|---|---|---|
| `público` | Dados de fontes abertas, sem restrição de uso | CNPJ da Receita Federal, processos do CNJ, notícias |
| `interno` | Dados dentro de uma organização, não públicos | Relatórios internos, análises da equipe |
| `restrito` | Dados pessoais do cidadão | CPF, documentos de identidade, histórico de benefícios |
| `confidencial` | Dados altamente sensíveis — médicos, financeiros, jurídicos estratégicos | Histórico médico, extratos bancários, contratos sigilosos |

## Regras de Aplicação por Nível

### `público`
- LLM externo: permitido.
- Compartilhamento: livre dentro da organização.
- Indexação RAG: pode usar serviço externo de embedding.
- Retenção: indefinida até exclusão explícita.

### `interno`
- LLM externo: permitido com registro de auditoria.
- Compartilhamento: restrito à organização.
- Indexação RAG: pode usar serviço externo com registro.
- Retenção: conforme política da organização.

### `restrito`
- LLM externo: **bloqueado**.
- LLM local: permitido.
- Compartilhamento: explícito e limitado (ver [`compartilhamento.md`](compartilhamento.md)).
- Indexação RAG: embedding local obrigatório.
- Retenção: até revogação pelo usuário.

### `confidencial`
- LLM externo: **bloqueado**.
- LLM local: permitido somente dentro da organização.
- Compartilhamento: explícito, temporário e revogável (ver [`compartilhamento.md`](compartilhamento.md)).
- Indexação RAG: embedding local obrigatório, índice isolado.
- Retenção: até revogação pelo usuário.
- Auditoria: registro de cada acesso, imutável.

## Classificação por Padrão

Dado sem classificação explícita recebe `restrito`. Nunca `público` por padrão.

Essa regra existe porque o custo de tratar dado sensível como público é irreversível. O custo de tratar dado público como restrito é apenas operacional.

## Metadados Obrigatórios

Todo artefato e documento no sistema carrega:

```json
{
  "classificacao": {
    "nivel": "público | interno | restrito | confidencial",
    "tenant_id": "uuid-da-organizacao",
    "classificado_por": "sistema | usuario",
    "classificado_em": "2026-04-25T10:00:00Z",
    "permitir_llm_externo": false,
    "compartilhamento": [],
    "expira_em": null,
    "revisao_em": null
  }
}
```

## Classificação por domínio de origem

Uma regra por organização (`RegraClassificacaoDominio`, cadastrada no admin) diz "tudo capturado de `bancodobrasil.com.br` nasce, no mínimo, `confidencial`". É aplicada **na criação do artefato**, antes de decidir se o LLM externo pode ser usado:

- **Só sobe o nível:** vale o maior entre o que o usuário escolheu na extensão e o da regra; nunca rebaixa. Se a elevação leva a `restrito` ou `confidencial`, o LLM externo é fechado mesmo que a extensão o tenha pedido.
- **Casa por sufixo de rótulo:** `bancodobrasil.com.br` cobre `www.` e `login.ap.`, mas não `meubancodobrasil.com.br` nem `bancodobrasil.com.br.outro.com`. O domínio é normalizado (minúsculas, sem esquema, caminho ou porta, em punycode); IPs não são suportados.
- **Várias regras:** vale a de maior nível.
- **Não reclassifica o que já existe:** a regra vale para capturas novas; o que já foi capturado segue como estava (ver "Reclassificação").
- **Rastro:** quando a regra eleva o nível, emite-se o evento `captura.classificada` (nível pedido, efetivo, domínio, regra, e o efeito sobre `allow_external_llm`). A resposta da captura traz `classification_level` e `classificacao_elevada`.

Os níveis seguem sendo os quatro do `policy_engine`, que não é alterado.

## Reclassificação

A classificação de um artefato não é mutável em operação. Para reclassificar:
1. O usuário solicita reclassificação explicitamente.
2. O sistema cria um novo artefato com a nova classificação.
3. O artefato original é mantido com sua classificação original no registro de auditoria.
4. Reclassificação de `confidencial` para nível menor exige confirmação dupla do usuário.

## Motor de Políticas

O Motor de Políticas é o componente determinístico responsável por aplicar as regras de classificação antes de cada operação. Ele recebe:

```json
{
  "operacao": "chamar_llm_externo | indexar_embedding | compartilhar",
  "classificacao_do_dado": "confidencial",
  "org_solicitante": "uuid",
  "org_dona": "uuid"
}
```

E retorna `PERMITIDO` ou `BLOQUEADO` com motivo. Toda decisão é registrada.

O Motor de Políticas nunca usa LLM para tomar decisões — é código determinístico.

## Auditoria

- Registro de auditoria é somente inserção: nenhum processo tem `DELETE` nessa tabela.
- Cada acesso a dado `confidencial` gera entrada com: *quem*, *o quê*, *quando*, *por quê* (operação solicitada).
- Registros de auditoria são exportáveis pelo administrador da organização.
- Retenção mínima: 2 anos.
