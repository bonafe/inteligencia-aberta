# Casos de Uso

Jornadas completas por persona. Cada caso de uso é a unidade mínima de valor entregável — um feature completo que uma pessoa real consegue usar.

Formato: contexto → ação do usuário → o que o sistema faz → resultado esperado → critérios de aceitação.

---

## UC-01 — Consulta de direitos previdenciários por voz

**Persona:** P1 — Dona Maria ![](../../assets/img/personas/maria.png)  
**Canal:** Voz  
**Classificação dos dados:** Restrito (CPF, número do benefício)

**Jornada:**
1. Maria fala: *"Tenho direito a algum benefício que não tô recebendo?"*
2. Sistema transcreve, interpreta a intenção.
3. Agente planejador define estratégia: consultar INSS, Benefício de Prestação Continuada, legislação vigente.
4. Agente coletor acessa dados públicos do INSS (com CPF fornecido por Maria, classificado como restrito).
5. Agente analista cruza dados com regras de elegibilidade.
6. Agente redator gera resposta em linguagem simples.
7. Sistema responde por voz: *"Dona Maria, pelo que vi, a senhora pode ter direito ao auxílio-alimentação. Posso te explicar como solicitar?"*

**Critérios de aceitação:**
- Resposta em voz, sem exigir leitura.
- Linguagem coloquial, sem jargão.
- Toda afirmação tem fonte citada internamente (mesmo que não lida em voz, disponível para auditoria).
- CPF não trafega para LLM externo.
- Latência de resposta < 15 segundos.

---

## UC-02 — Análise financeira do MEI

**Persona:** P2 — Carlos ![](../../assets/img/personas/carlos.png)  
**Canal:** Chat (texto/áudio)  
**Classificação dos dados:** Confidencial (dados financeiros privados)

**Jornada:**
1. Carlos carrega extratos bancários dos últimos 6 meses (PDF ou foto).
2. Faz a pergunta: *"Meu negócio tá valendo a pena?"*
3. Agente extrator processa os documentos via OCR + parsing financeiro.
4. Dados ficam armazenados como confidenciais — não saem para LLM externo.
5. Agente analista (usando LLM local) calcula margem, tendência, sazonalidade.
6. Agente redator entrega análise: *"Nos últimos 6 meses você faturou R$ X, teve custo de R$ Y, e a margem real foi de Z%. Março foi seu mês mais forte — veja o gráfico."*

**Critérios de aceitação:**
- Dados financeiros processados exclusivamente em LLM local ou on-premise.
- Carlos consegue perguntar em linguagem natural sem saber termos financeiros.
- Resposta inclui gráfico ou visualização simples.
- Carlos consegue pedir mais detalhes em linguagem natural.
- Dados deletáveis sob demanda.

---

## UC-03 — Acompanhamento de processo judicial

**Persona:** P4 — João ![](../../assets/img/personas/joao.png)  
**Canal:** Chat / Voz  
**Classificação dos dados:** Público (dados do processo) + Restrito (dados do usuário)

**Jornada:**
1. João informa o número do processo.
2. Agente coletor acessa tribunais públicos (TJ, CNJ).
3. Agente extrator lê o conteúdo das peças públicas.
4. Agente analista interpreta o estado atual e próximos passos prováveis.
5. Agente redator traduz em linguagem simples: *"Seu processo tá na fase de contestação. Isso significa que a outra parte está respondendo ao pedido. O prazo deles termina em 15 de maio. Quer que eu te avise quando tiver novidade?"*
6. Sistema configura alerta e notifica João quando houver nova movimentação.

**Critérios de aceitação:**
- Explicação sem jargão jurídico (ou com explicação imediata do jargão).
- Diferenciação clara entre fato (o que está no processo) e inferência (o que provavelmente vai acontecer).
- Alerta funcional por push/mensagem quando houver movimentação.
- João consegue perguntar *"o que é contestação?"* e receber resposta.

---

## UC-04 — Compartilhamento de histórico médico com médico

**Persona:** P1/P4 (paciente) ![](../../assets/img/personas/maria.png) ![](../../assets/img/personas/joao.png) → P5 Dra. Fernanda ![](../../assets/img/personas/fernanda.png)  
**Canal:** Chat (paciente) / Web (médica)  
**Classificação dos dados:** Confidencial

**Jornada:**
1. Paciente carrega documentos médicos (laudos, exames, receitas).
2. Paciente autoriza compartilhamento com a Dra. Fernanda por um período determinado (ex: 7 dias).
3. Dra. Fernanda recebe notificação e acessa o histórico autorizado.
4. Ela faz perguntas ao sistema: *"Esse paciente tem histórico de alergia a penicilina?"*
5. Sistema responde com base nos documentos compartilhados.
6. Após 7 dias, ou mediante revogação do paciente, acesso é bloqueado.

**Critérios de aceitação:**
- Compartilhamento é opt-in e explícito (paciente inicia).
- Médica não acessa nada além do que foi autorizado.
- Revogação bloqueia acesso imediatamente.
- Log completo de quem acessou o quê e quando.
- Dados nunca saem para LLM externo.

---

## UC-05 — Investigação de vínculos societários (OSINT)

**Persona:** P3 — Priya ![](../../assets/img/personas/priya.png) ou P7 — Rafael ![](../../assets/img/personas/rafael.png)  
**Canal:** Web / Chat  
**Classificação dos dados:** Público

**Jornada:**
1. Usuário informa: *"Investiga os vínculos da empresa XPTO LTDA — sócios, contratos públicos, processos."*
2. Agente planejador define estratégia: Receita Federal, SICAF, portais de transparência, tribunais, imprensa.
3. Agentes coletores executam consultas em paralelo.
4. Agente correlacionador monta grafo de vínculos.
5. Agente validador verifica cruzamento de fontes.
6. Agente redator gera relatório com grafo visual, lista de vínculos, fontes e grau de confiança.
7. Usuário exporta em PDF ou continua investigando com perguntas em linguagem natural.

**Critérios de aceitação:**
- Cada dado no relatório tem fonte citada.
- Grafo de vínculos navegável.
- Relatório diferencia fato, inferência e opinião.
- Usuário pode pedir aprofundamento de qualquer nó do grafo.
- Exportação em PDF com todas as fontes.

---

## UC-06 — Declaração de IR assistida

**Persona:** P2 — Carlos ![](../../assets/img/personas/carlos.png) ou P4 — João ![](../../assets/img/personas/joao.png)  
**Canal:** Chat / Voz  
**Classificação dos dados:** Confidencial

**Jornada:**
1. Usuário diz: *"Precisa me ajudar com o imposto de renda esse ano."*
2. Sistema pergunta o que ele já tem: notas fiscais, informes de rendimento, recibos de despesa médica.
3. Usuário vai carregando os documentos conforme solicitado (foto, PDF, digitação).
4. Agente extrator processa cada documento.
5. Agente analista monta o rascunho da declaração com os dados disponíveis.
6. Sistema explica cada campo em linguagem simples e alerta sobre o que falta.
7. Usuário revisa e exporta o arquivo para importar na plataforma da Receita Federal.

**Critérios de aceitação:**
- Nunca afirma que a declaração está correta sem avisar que é uma assistência, não uma declaração homologada.
- Dados financeiros processados localmente.
- Usuário consegue concluir sem precisar entender o sistema tributário.
- Arquivo de saída compatível com importação no IRPF da Receita Federal.

---

## UC-07 — Investigação institucional com dados sigilosos (multi-tenant)

**Persona:** P6 — Auditor da Receita Federal ![](../../assets/img/personas/analista.png)  
**Canal:** Web / API  
**Classificação dos dados:** Confidencial (dados internos) + Público (fontes abertas)

**Jornada:**
1. Auditor carrega dados internos da RFB (marcados automaticamente como confidenciais, tenant RFB).
2. Define escopo da investigação: cruzar dados internos com CNPJ público, processos, imprensa.
3. Sistema executa: dados internos → LLM local. Dados públicos → pode usar LLM externo.
4. Agente correlacionador cruza os dois mundos sem expor dados internos para fora.
5. Relatório gerado com rastreabilidade: cada conclusão indica se veio de dado interno ou público.

**Critérios de aceitação:**
- Dados internos da RFB nunca trafegam para API externa.
- Isolamento completo do tenant RFB dos demais usuários da plataforma.
- Log de auditoria imutável de toda a execução.
- Conclusões baseadas em dados internos são marcadas de forma diferente das baseadas em dados públicos.
