# Componente: Interface Web (Django)

## Responsabilidade

Interface operacional completa para usuários com maior letramento digital: gestão de configurações, visualização de relatórios estruturados, administração de organizações, painéis e exportações.

Não é o canal principal de uso — chat e voz são. A interface web serve para operações que não cabem bem em conversa: gerenciar configurações, revisar relatórios longos, administrar acessos, visualizar grafos complexos.

## Seções Principais

### Painel Principal

- Investigações em andamento e recentes.
- Alertas de processos com nova movimentação.
- Resumo de dados armazenados pelo usuário.

### Investigações

- Iniciar nova investigação (formulário simples ou via chat integrado).
- Visualizar relatório completo com fontes, grafo de vínculos, grau de confiança.
- Exportar em PDF ou JSON.
- Ver histórico de investigações anteriores.

### Documentos

- Envio e gestão de documentos pessoais/sigilosos.
- Classificação e metadados.
- Gerenciar compartilhamentos ativos.

### Configurações

- Perfil do usuário e preferências.
- Chaves de API para fontes externas (armazenadas com criptografia).
- Configuração de LLM (externo vs. local).
- Endereços de servidores MCP.
- Preferências de notificação.

### Administração (acesso restrito)

- Gestão de organizações.
- Monitoramento de uso.
- Registros de auditoria.
- Gerenciamento de contêineres.

## Pilha Tecnológica

- **Django** com templates renderizados no servidor para páginas estruturadas.
- **HTMX** para interatividade sem aplicação de página única — atualizações parciais de página.
- **Alpine.js** para comportamentos de interface simples.
- **Tailwind CSS** para estilização.

## Critérios de Aceitação

- [ ] Relatório de investigação carrega em < 5 segundos.
- [ ] Grafo de vínculos com até 100 nós é navegável sem degradação de desempenho.
- [ ] Exportação PDF gera arquivo fiel ao relatório em tela em < 10 segundos.
- [ ] Interface funciona em dispositivos móveis (responsiva).
- [ ] Todas as ações destrutivas (excluir documento, revogar acesso) pedem confirmação explícita.
- [ ] Painel de administração é inacessível para usuários sem função `admin`.
