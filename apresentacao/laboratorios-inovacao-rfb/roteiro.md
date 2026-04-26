# Roteiro — Laboratórios de Inovação da Receita Federal

**Evento:** Encontro dos Laboratórios de Inovação da Receita Federal  
**Público:** Servidores e equipes de inovação da RFB  
**Tom:** Reflexivo e propositivo — não é demo técnica, é convite para pensar junto  
**Duração estimada:** ~20 minutos

---

## Slide 1 — Abertura

**Título:** Inteligência Aberta

**Subtítulo:** Agentes de IA a serviço de cada cidadão

> Ponto de partida: uma pergunta simples antes de mostrar qualquer tecnologia.

---

## Slide 2 — Uma pergunta

**O que você faria diferente se tivesse um analista especializado disponível 24 horas, que conhecesse seu contexto, lesse qualquer documento e te explicasse tudo em linguagem simples?**

> Deixar a pergunta respirar. O público de inovação vai se identificar — eles vivem esse problema no dia a dia dos cidadãos que atendem.

---

## Slide 3 — O problema

**Inteligência analítica sempre foi um privilégio.**

- Assessoria jurídica, financeira, médica, tributária
- Reservada a quem pode pagar — ou a quem trabalha no lugar certo
- O cidadão comum navega sozinho por sistemas opacos

> Conectar com o contexto da RFB: quantos contribuintes declaram IR sem entender o que estão assinando?

---

## Slide 4 — O que é inteligência?

**Agir com inteligência é diferente de ser inteligente.**

- Inteligência não é um traço fixo — é uma capacidade que depende de acesso
- Acesso a informação confiável, contexto relevante, tempo para analisar
- Dados → Informação → Inteligência (algo acionável, que muda uma decisão)

> Ancoragem conceitual antes de entrar no sistema.

---

## Slide 5 — O que significa "Aberta"

**Quatro dimensões, um nome:**

1. **Fontes abertas** — dados públicos, APIs, OSINT
2. **Software aberto** — MIT, auditável, redistribuível
3. **Acesso aberto** — voz, linguagem natural, sem barreira técnica
4. **Inteligência livre** — *free as in freedom*, não como produto exclusivo

---

## Slide 6 — O ciclo de inteligência

**O sistema replica o que analistas humanos fazem — em escala.**

| Fase | Agente |
|---|---|
| Planejamento | Planejador |
| Coleta | Coletor |
| Processamento | Extrator |
| Correlação | Correlacionador |
| Validação | Validador |
| Análise | Analista |
| Disseminação | Redator |

> Mostrar que não é novidade conceitual — é o ciclo clássico de inteligência, agora acessível a qualquer pessoa.

---

## Slide 7 — Arquitetura (visão macro)

**Diagrama simplificado:**

```
Cidadão (voz / texto / web)
        ↓
   Orquestrador (LangGraph)
        ↓
  Agentes Especializados
        ↓
    Ferramentas via MCP
        ↓
Fontes: APIs públicas · Documentos · Dados privados do usuário
```

> Não entrar em stack técnica — só mostrar o fluxo. Destacar que voz é interface de primeira classe.

---

## Slide 8 — Casos de uso (concretos para RFB)

**O que isso resolve na prática:**

| Persona | Caso |
|---|---|
| ![Dona Maria](../../assets/img/personas/maria.png) Dona Maria | Entende sua declaração de IR por voz |
| ![Auditor da Receita Federal](../../assets/img/personas/analista.png) Auditor da Receita Federal | Cruza dados internos com fontes abertas com isolamento total |
| ![João](../../assets/img/personas/joao.png) João | Acompanha processos e prazos em linguagem simples |
| ![Carlos](../../assets/img/personas/carlos.png) Carlos | Analisa obrigações do seu CNPJ sem contador |

> Escolher 1 ou 2 para aprofundar dependendo do interesse da sala.

---

## Slide 9 — Segurança e conformidade

**Dados sensíveis nunca saem sem autorização explícita.**

- Classificação obrigatória em todo dado: `público` → `interno` → `restrito` → `confidencial`
- Dados `restrito` e `confidencial`: apenas LLMs locais (on-premise), nunca APIs externas
- Conformidade com LGPD por design
- Multi-tenancy: isolamento lógico desde a origem
- Rastreabilidade ponta a ponta — toda conclusão tem fonte auditável

> Ponto crítico para audiência federal. Detalhar se houver perguntas.

---

## Slide 10 — Estado atual e próximos passos

**Onde estamos:**

- Especificação arquitetural completa
- Repositório público (MIT)
- MVP em desenvolvimento: `docker-compose`, execução local

**Para onde vamos:**

- Interface web operacional
- Tools iniciais: `consultar_cnpj`, `buscar_processos`, `buscar_noticias`
- Interface de voz em português brasileiro
- Suporte a modelos locais para dados sensíveis

---

## Slide 11 — Convite

**Este projeto é aberto — nas quatro dimensões.**

- Repositório: github.com/bonafe/inteligencia-aberta
- Licença MIT — use, adapte, contribua
- Laboratórios de inovação são exatamente o tipo de parceiro que faz isso crescer

> Fechar com a pergunta do slide 2 — agora com resposta.

---

## Notas de produção

- [ ] Definir identidade visual dos slides (paleta, tipografia)
- [ ] Decidir se usa Reveal.js ou HTML/CSS próprio
- [x] Imagens das personas — `assets/img/personas/` (maria, carlos, priya, joao, fernanda, analista, rafael)
- [ ] Diagrama do ciclo de inteligência como imagem ou SVG animado
- [ ] Diagrama da arquitetura como imagem ou mermaid renderizado
