# Personas

Estas personas guiam todas as decisões de produto e especificação técnica. Cada componente novo deve ser testado contra ao menos uma delas.

---

## P1 — Dona Maria, 68 anos, aposentada

**Contexto:** Vive sozinha no interior, recebe INSS, tem celular Android básico. Nunca usou computador. Lê com dificuldade.

**Necessidade:** Entender se tem direito a algum benefício adicional, conferir seus extratos, saber como funciona o imposto de renda de aposentada.

**Como interage:** Voz. Não digita. Se tiver que ler uma tela com muitos menus, desiste.

**Dados que traz:** Número do CPF, número do benefício INSS, documentos escaneados pelo filho quando precisa.

**Sucesso:** Pergunta por voz "tenho direito a algum benefício que não estou recebendo?" e recebe resposta falada, clara, com próximos passos simples.

**Critério de falha:** Qualquer resposta que exija que ela navegue num menu ou clique em algo.

---

## P2 — Carlos, 34 anos, MEI (microempreendedor individual)

**Contexto:** Tem uma loja de bairro, faz DAS todo mês mas não entende bem o que está pagando. Usa smartphone com fluência, mas não tem contador.

**Necessidade:** Saber se está pagando os impostos corretos, entender suas obrigações fiscais, analisar se o negócio está dando lucro de verdade.

**Como interage:** Chat em linguagem natural. Manda áudios às vezes.

**Dados que traz:** Notas fiscais, extratos bancários, DAS emitidos. Quer que o sistema guarde esses dados com segurança — são sigilosos.

**Sucesso:** Pergunta "meu negócio tá valendo a pena?" e recebe análise com base nos dados que ele próprio inseriu, sem precisar de contador.

**Critério de falha:** Perder dados privados ou expô-los sem autorização.

---

## P3 — Advogada Priya, 41 anos, defensoria pública

**Contexto:** Atende dezenas de casos simultâneos, muitos envolvendo comunidades vulneráveis. Precisa de OSINT rápido e confiável para construir argumentações.

**Necessidade:** Consultar rapidamente dados públicos sobre empresas, processos, vínculos societários, imóveis. Cruzar informações de múltiplas fontes com rastreabilidade.

**Como interage:** Interface web, relatórios estruturados, exportação em PDF.

**Dados que traz:** Documentos do caso (sigilosos), anotações de investigação.

**Sucesso:** Produz um relatório de vínculos entre empresas em minutos, com cada fonte citada, pronto para juntar ao processo.

**Critério de falha:** Conclusão sem fonte rastreável. Inferência da IA apresentada como fato.

---

## P4 — João, 52 anos, morador de comunidade urbana

**Contexto:** Sua rua tem um processo de reintegração de posse há anos. Ele quer entender o que está acontecendo mas nunca conseguiu falar com um advogado.

**Necessidade:** Acompanhar o andamento do processo, entender o que cada documento significa, saber quais são seus direitos.

**Como interage:** Voz ou chat simples. Linguagem coloquial.

**Dados que traz:** Número do processo, fotos de documentos tiradas com o celular.

**Sucesso:** Entende em linguagem simples o que está acontecendo no processo e o que pode fazer. Recebe alerta quando há movimentação.

**Critério de falha:** Resposta com jargão jurídico sem explicação. Falta de rastreabilidade da informação.

---

## P5 — Dra. Fernanda, 45 anos, médica de família

**Contexto:** Atende pacientes em UBS. Quer acesso ao histórico de saúde do paciente de forma rápida, com dados que o próprio paciente autoriza compartilhar.

**Necessidade:** Consultar histórico de atendimentos anteriores, medicamentos em uso, alergias declaradas — todos fornecidos pelo paciente, não por sistemas hospitalares que ela não tem acesso.

**Como interage:** Interface web profissional, ágil.

**Dados que traz:** Não. É a receptora dos dados que o paciente compartilha.

**Sucesso:** O paciente compartilha seu histórico antes da consulta. Ela acessa na hora, com trilha de quem autorizou e quando. Pode perder o acesso quando o paciente revogar.

**Critério de falha:** Acesso a dados sem autorização do paciente. Dado sem data de origem.

---

## P6 — Analista, 29 anos, servidor da Receita Federal

**Contexto:** Faz análise de risco fiscal. Trabalha com dados internos da RFB e dados públicos. Precisa cruzar informações de múltiplas fontes.

**Necessidade:** Usar o sistema em contexto institucional, com segregação total dos dados da RFB dos dados pessoais de outros tenants.

**Como interage:** Interface web, API, relatórios.

**Dados que traz:** Dados internos altamente sigilosos — nunca podem sair para LLMs externos.

**Sucesso:** Fluxo completo de investigação com dados internos processados exclusivamente em LLM local, fontes públicas em LLM externo se necessário, sem mistura.

**Critério de falha:** Qualquer dado interno trafegando para API externa.

---

## P7 — Rafael, 23 anos, estudante universitário, ativista

**Contexto:** Pesquisa financiamento de campanhas políticas e vínculos empresariais como hobby/ativismo. Tem letramento técnico médio.

**Necessidade:** Fazer OSINT sobre dados públicos — CNPJ, doações eleitorais, processos, imóveis — e montar relatórios de vínculos.

**Como interage:** Chat, interface web, API se necessário.

**Dados que traz:** Apenas dados públicos coletados por ele.

**Sucesso:** Monta um grafo de vínculos entre doadores e beneficiários de contratos públicos, com todas as fontes citadas, exporta o relatório.

**Critério de falha:** Sistema aceita premissas como fatos. Falta de diferenciação entre fato e inferência.
