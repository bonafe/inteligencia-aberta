# Segurança: Compartilhamento e Múltiplas Organizações

## Modelo de Isolamento

Cada usuário ou organização é isolado logicamente. Organizações diferentes não compartilham dados, índices de busca semântica, grafos ou registros por padrão. O isolamento é garantido em nível de banco de dados (schemas ou bases separadas) e em nível de rede (contêineres com políticas de rede fechadas).

**Negação por padrão:** Sem permissão explícita, dado de uma organização é invisível para outra. Não existe "acesso por acidente".

## Tipos de Organização

| Tipo | Exemplo | Características |
|---|---|---|
| Individual | Cidadão usando o sistema pessoalmente | 1 usuário, dados pessoais |
| Equipe | Defensoria pública, escritório de advocacia | N usuários, dados compartilhados dentro da organização |
| Institucional | Receita Federal, hospital | N usuários com diferentes níveis de acesso, políticas próprias |

## Compartilhamento de Dados

Compartilhamento é sempre iniciado pelo dono do dado. Nunca automático.

### Criando um compartilhamento

```json
{
  "artefato_id": "uuid",
  "compartilhar_com": {
    "tipo": "usuario | grupo | organizacao",
    "id": "uuid-do-destinatario"
  },
  "permissoes": ["ler"],
  "validade": "2026-05-25T00:00:00Z",
  "motivo": "Consulta médica com Dra. Fernanda"
}
```

Permissões disponíveis: `ler`. Não existe permissão de `editar` ou `recompartilhar` para dados de outra organização — apenas o dono pode fazer isso.

### Ciclo de vida do compartilhamento

1. **Criado:** Dono define destinatário, permissões e validade.
2. **Ativo:** Destinatário acessa o dado dentro do escopo definido.
3. **Expirado:** Validade venceu — acesso bloqueado automaticamente.
4. **Revogado:** Dono cancelou antes da validade — acesso bloqueado imediatamente.

### Revogação

Revogação bloqueia acesso imediatamente. Sem janela de graça. O dado permanece no sistema do dono — apenas o acesso do destinatário é removido.

A revogação não garante que o destinatário não tem cópias locais (limitação técnica do sistema). O sistema registra a revogação, notifica o destinatário, e impede qualquer acesso futuro pela plataforma.

## Casos de Uso de Compartilhamento

### Paciente → Médico (UC-04)

- Paciente cria compartilhamento de documentos médicos.
- Define médico específico como destinatário.
- Define validade de 7 dias.
- Médico acessa durante a consulta.
- Paciente pode revogar a qualquer momento.
- Cada acesso do médico gera registro visível ao paciente.

### Cidadão → Advogado

- Cidadão compartilha documentos do processo.
- Advogado pode ler, mas não recompartilhar.
- Compartilhamento expira ao fim do mandato.

### Auditor → Equipe (contexto institucional)

- Dentro de uma organização institucional, o auditor compartilha relatório com sua equipe.
- Equipe pode ler, líder pode aprovar ou rejeitar.
- Dados não saem da organização — é compartilhamento interno.

## Projeto — colaboração contínua entre organizações diferentes

`Sharing` (acima) resolve um grant pontual: um dono compartilha UM artefato com UM destinatário, por um prazo, revogável. Não resolve um caso diferente: duas ou mais pessoas de **organizações diferentes** que querem colaborar de forma contínua — decidir juntas o que fica disponível entre elas, incluindo capacidade de computação (usar o LLM local da máquina uma da outra), não só leitura de documento.

Para isso existe `Projeto` ([ADR-006](../arquitetura/decisoes/006-cluster-adaptativo-multiproprietario.md)), ainda não implementado — só desenhado. Diferenças deliberadas em relação aos dois mecanismos que já existem:

| | `Sharing` | `Organization` | `Projeto` |
|---|---|---|---|
| Escopo | um artefato | todos os dados de uma organização | o que cada membro decidir trazer |
| Membros | um destinatário (usuário/grupo/organização) | usuários da mesma organização | usuários de organizações diferentes |
| Duração | pontual, com validade | permanente (é a identidade) | contínua, enquanto o membro participar |
| O que concede | ler | acesso pleno interno | o que for explicitamente marcado por item/categoria |

Um `Projeto` **nunca** implica confiança automática entre as organizações dos seus membros — participar de um projeto não torna visível nenhum dado que o membro não tenha explicitamente marcado como elegível para aquele projeto. A mesma regra de negação por padrão desta página vale aqui: sem marcação explícita, nada de uma organização atravessa para outra através de um projeto.

Dois usos previstos para `Projeto`:
- **Replicação seletiva**: o motor de posicionamento (`apps/cluster/replicacao.py::eventos_para_peer`, ADR-006) considera participação em projeto em comum como um dos critérios para elegibilidade de réplica — ao lado de propriedade do destino e espaço disponível.
- **Computação de LLM compartilhada**: o roteador de LLM (`apps/cluster/llm_router.py`) pode incluir máquina de outro membro do projeto, quando ambos os donos autorizarem reciprocidade — nunca por padrão.

## Controles de Rede por Organização

Para organizações com dados `confidencial`:

- Contêiner da organização tem rede isolada — sem comunicação direta com contêineres de outras organizações.
- LLM local pode ser dedicado por organização (mais custo, mais segurança).
- Saída para internet controlada por lista de domínios permitidos.

## Critérios de Aceitação

- [ ] Compartilhamento expirado bloqueia acesso em < 1 minuto após vencimento.
- [ ] Revogação bloqueia acesso em < 5 segundos.
- [ ] Dono do dado vê registro de todos os acessos ao seu dado compartilhado.
- [ ] Dado de uma organização nunca aparece em busca semântica de outra organização.
- [ ] Compartilhamento sem validade definida pede confirmação explícita ao usuário.
- [ ] Notificação enviada ao destinatário quando compartilhamento é criado e quando é revogado.
