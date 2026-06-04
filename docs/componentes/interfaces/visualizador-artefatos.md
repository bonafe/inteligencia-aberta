# Especificação: Visualizador de Artefatos

## 1. Objetivo

Interface web no **Portal (Django)** para navegar e inspecionar artefatos do tipo `documento` (páginas MHTML capturadas pela extensão). O usuário visualiza a página preservada offline, o texto puro extraído pelo pipeline e os dados estruturados (JSON) gerados pelos extratores, tudo no mesmo espaço sem reload de página.

## 2. Layout e Experiência do Usuário (UI/UX)

A tela (*Galeria de Artefatos* / *Visualizador de Capturas*) tem a seguinte disposição:

### Header / Carrossel (Topo)

- Lista horizontal com mini-cards de artefatos capturados.
- Cada card: título, URL resumida, data/hora.
- Clique no card atualiza o quadro principal via JavaScript (sem reload).

### Painel de Metadados (Lateral esquerda)

- URL original, timestamp, nível de classificação, ID do artefato.
- Page type detectado e confiança (ex: `tabular_generico — 80%`).
- Contagem de palavras e caracteres (quando texto disponível).
- Botão "Abrir em Tela Cheia" para o MHTML em nova aba.

### Quadro Principal com Abas (Direita)

Três abas controladas por JS vanilla:

| Aba | Conteúdo | Disponível quando |
|-----|----------|-------------------|
| **MHTML** | `<iframe>` com a página renderizada | Sempre (é o artefato base) |
| **Texto** | Texto puro extraído em `<pre>` rolável | `DocumentText` existe para o artefato |
| **Dados Estruturados** | Árvore JSON interativa (colapsável) | `structured_data` não é nulo |

- Abas sem dados ficam desabilitadas visualmente (não clicáveis).
- Conteúdo de texto e JSON é carregado via AJAX sob demanda ao trocar de aba (evita serializar megabytes de texto no HTML inicial).

### Componente JSON Viewer

Renderizador vanilla JS embutido no template (sem dependência externa):

- Árvore colapsável: objetos e arrays exibem `▶ {3 keys}` quando colapsados.
- Colorização por tipo: strings em verde, números em laranja, booleanos em roxo, null em vermelho.
- Botão "Copiar JSON" que copia o JSON bruto para o clipboard.
- Indentação visual por nível de aninhamento.

## 3. Arquitetura Backend (Django)

### Views

**`ArtifactGalleryView`** — sem mudanças estruturais.  
- Consulta `Artifact` tipo `documento` com `mhtml_path`.
- Renderiza `gallery.html`.

**`ArtifactContentView`** (nova) — endpoint AJAX `GET /artifacts/<uuid>/content/`.  
- Busca `DocumentText` via `artifact.extracted_text` (OneToOne).
- Retorna `JsonResponse` com os campos abaixo; 404 se não existir.

```json
{
  "page_type": "tabular_generico",
  "detection_confidence": 0.80,
  "detection_source": "structural_analysis",
  "char_count": 4200,
  "word_count": 680,
  "extractor_version": "1.0",
  "text": "...",
  "structured_data": { ... }
}
```

**`ServeMHTMLView`** — sem mudanças.  
- Proxy MinIO → HTML auto-contido com recursos em base64.
- Requer `@xframe_options_sameorigin` (servido em iframe).

### URLs

```
GET /artifacts/gallery/                  → ArtifactGalleryView
GET /artifacts/<uuid>/mhtml/             → ServeMHTMLView
GET /artifacts/<uuid>/content/           → ArtifactContentView  ← novo
POST /artifacts/api/v1/artefatos/        → ArtefatoCreateAPIView
GET /artifacts/busca/                    → BuscaSemanticaView
```

## 4. Arquitetura Frontend

- **CSS:** Vanilla Dark Mode. Abas usam estado `active` / `disabled` via classes.
- **JS:** Vanilla. Três responsabilidades:
  1. `selectArtifact()` — troca o artefato ativo, reseta estado das abas.
  2. `switchTab()` — troca a aba visível; dispara fetch AJAX se texto/JSON ainda não foram carregados para o artefato atual.
  3. `renderJsonTree()` — renderizador recursivo de árvore JSON colapsável.
- Segurança do iframe: `sandbox=""` (sem JS, sem forms, sem popups).

## 5. Modelo de Dados Relevante

```
Artifact (tipo=documento)
  └── DocumentText (OneToOne via extracted_text)
        ├── text            TextField
        ├── page_type       CharField  (tabular_generico, artigo, processo_judicial, …)
        ├── detection_confidence  FloatField
        ├── structured_data JSONField (nullable)
        ├── char_count      IntegerField
        └── word_count      IntegerField
```

## 6. Tipos de Página e Estrutura do JSON

Gerados pelos extratores em `services/portal/apps/artifacts/extractors/strategies.py`:

| page_type | Chaves em structured_data |
|-----------|--------------------------|
| `tabular_financeiro` | `transacoes[]` (data, descricao, valor, tipo) |
| `tabular_generico` | `tabelas[]` (headers[], rows[][]) |
| `processo_judicial` | `numero_cnj`, `partes{}`, `movimentacoes[]` |
| `perfil_pessoa_juridica` | `cnpj`, `campos{}` |
| `artigo`, `documento_juridico`, `misto`, `desconhecido` | `null` (só texto) |

## 7. Checklist de Implementação

- [x] `ArtifactGalleryView` e `ServeMHTMLView` implementados
- [x] Template `gallery.html` com iframe MHTML
- [ ] `ArtifactContentView` (endpoint AJAX `/artifacts/<uuid>/content/`)
- [ ] URL mapeada para `ArtifactContentView`
- [ ] Abas no template (MHTML / Texto / Dados Estruturados)
- [ ] AJAX fetch de conteúdo ao trocar aba
- [ ] Renderizador JSON colapsável (vanilla JS inline)
- [ ] Metadados expandidos no painel lateral (page_type, word_count)
