# Agente: Extrator

## Responsabilidade

Extrair informações estruturadas de dados brutos: PDFs, HTML de páginas web, imagens com texto (via OCR), áudios transcritos. Transforma dado bruto em entidade estruturada.

## Capacidades

- **OCR:** Extração de texto de imagens e PDFs escaneados.
- **Leitura estruturada de documentos:** Estruturação de dados de notas fiscais, contratos, laudos médicos, extratos bancários.
- **Extração de entidades:** CPF, CNPJ, datas, valores, nomes, endereços de texto não estruturado.
- **Fragmentação:** Divide documentos longos em fragmentos para processamento e indexação RAG.

## Regra de Classificação

O extrator herda a classificação do artefato de origem. Se o documento original é `confidencial`, o artefato extraído também é `confidencial`.

## Critérios de Aceitação

- [ ] PDF com texto digital extraído com fidelidade > 98%.
- [ ] PDF escaneado (imagem) extraído com WER < 10% para fontes legíveis.
- [ ] Extração de CPF/CNPJ de texto não estruturado com precisão > 95%.
- [ ] Documento de 100 páginas processado em < 60 segundos.
- [ ] Cada fragmento produzido mantém referência à página/seção de origem.
