# Especificação: Extensão de Captura (Inteligência Aberta)

## 1. Objetivo

Criar uma extensão para Google Chrome (e Chromium) que capture a "fotografia" exata de uma página web no momento da navegação. Preserva cadeia de custódia e visual de páginas (inclusive com login) para indexação no Inteligência Aberta.

A extensão também permite ao investigador **configurar como cada domínio deve ser processado**, escolhendo nível de classificação e se o LLM externo pode ser usado na análise.

---

## 2. Abordagem Tecnológica

Usa `chrome.pageCapture` para gerar MHTML. O popup oferece configuração por domínio, salva localmente com `chrome.storage`, e inclui as configurações no payload enviado ao Orchestrator.

---

## 3. Arquitetura (Manifest V3)

| Arquivo | Responsabilidade |
|---------|-----------------|
| `manifest.json` | Permissões: `activeTab`, `pageCapture`, `storage` |
| `popup.html` | UI: domínio atual, configurações de captura, botões |
| `popup.js` | Lê/salva config em `chrome.storage`; dispara captura via `chrome.runtime.sendMessage` |
| `background.js` | Service worker: captura MHTML, monta FormData com config, envia ao Orchestrator |

---

## 4. Interface do Popup

```
┌──────────────────────────────────────┐
│ ■ Inteligência Aberta                │
├──────────────────────────────────────┤
│ bb.com.br          [configurado]     │  ← domínio da aba ativa
├──────────────────────────────────────┤
│ CLASSIFICAÇÃO                        │
│  [Público]    [Interno]              │
│  [Restrito ✓] [Confidencial]         │
├──────────────────────────────────────┤
│ Análise Inteligente (LLM)            │
│ Usa IA para páginas não reconhecidas │
│ Não disponível para dados restritos  │
│                           [toggle]   │
├──────────────────────────────────────┤
│ Identificação ▼  (colapsável)        │
│   ID do usuário: [_____________]     │
│   ID da organização: [__________]    │
├──────────────────────────────────────┤
│ [Salvar para bb.com.br]              │
│ [     Capturar e Enviar      ]       │
│                                      │
│  ✅ Capturado com sucesso!           │
└──────────────────────────────────────┘
```

### Badge de configuração

| Estado | Badge | Significado |
|--------|-------|-------------|
| `configurado` | verde | `chrome.storage` tem config salva para este domínio |
| `padrão` | cinza | Nenhuma config salva; usa defaults |

---

## 5. Configuração por Domínio

As configurações são salvas em `chrome.storage.local` com a chave `config_{domain}` (ex: `config_bb.com.br`). São carregadas automaticamente ao abrir o popup na aba com aquele domínio.

### Campos configuráveis

| Campo | Tipo | Padrão | Descrição |
|-------|------|--------|-----------|
| `classification_level` | enum | `restrito` | Nível de sigilo do artefato gerado |
| `allow_external_llm` | bool | `false` | Permite uso de LLM externo para classificação e extração |
| `user_id` | string | `""` | UUID do usuário no Portal (opcional) |
| `tenant_id` | string | `""` | UUID da organização no Portal (opcional) |

### Constraint de política

Quando `classification_level` é `restrito` ou `confidencial`, o backend **ignora** `allow_external_llm=true` (regra do `policy_engine`). O popup exibe aviso visual quando essa combinação é selecionada.

### Estrutura no chrome.storage

```json
{
  "config_bb.com.br": {
    "classification_level": "restrito",
    "allow_external_llm": true,
    "user_id": "550e8400-...",
    "tenant_id": "b3f1e200-...",
    "saved_at": "2025-06-03T14:00:00Z"
  }
}
```

---

## 6. Fluxo de Dados

```
Usuário abre popup
  → popup.js lê aba ativa → extrai domínio
  → chrome.storage.local.get("config_{domain}") → carrega config ou defaults
  → renderiza UI com config atual

Usuário edita config e clica "Salvar"
  → chrome.storage.local.set("config_{domain}", config)
  → badge muda para "configurado"

Usuário clica "Capturar e Enviar"
  → popup.js → chrome.runtime.sendMessage({ action: "capture_and_upload", config })
  → background.js:
      1. chrome.tabs.query → aba ativa
      2. chrome.pageCapture.saveAsMHTML → Blob MHTML
      3. FormData com MHTML + metadados + config
      4. POST http://localhost:8001/api/v1/capture/mhtml
  → Orchestrator:
      5. Salva MHTML no MinIO
      6. POST portal:8000/artifacts/api/v1/artefatos/ com allow_external_llm
  → Portal:
      7. Cria Artifact com allow_external_llm do payload
      8. Signal → extract_text_from_mhtml.delay()
      9. Celery worker usa LLM se allow_external_llm=True e política permitir
  → popup.js exibe ✅ ou ❌
```

---

## 7. Payload Enviado ao Orchestrator

`multipart/form-data` com os campos:

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| `file` | Blob | Sim | Arquivo `.mhtml` |
| `url` | string | Sim | URL completa da aba |
| `title` | string | Não | `document.title` da aba |
| `timestamp` | string ISO | Sim | Momento da captura |
| `classification_level` | string | Não | Padrão: `restrito` |
| `allow_external_llm` | string `"true"/"false"` | Não | Padrão: `"false"` |
| `user_id` | string UUID | Não | Pode ser omitido |
| `tenant_id` | string UUID | Não | Pode ser omitido |

---

## 8. Fallback de Identidade (Fase 0)

Se `user_id` ou `tenant_id` forem omitidos, o Portal:
- Usa o **primeiro usuário cadastrado** por `date_joined`
- Se o usuário não tiver organização, cria automaticamente uma `INDIVIDUAL`

---

## 9. Critérios de Aceitação

- [ ] Popup abre e detecta o domínio da aba ativa corretamente.
- [ ] Badge "configurado" aparece quando há config salva; "padrão" caso contrário.
- [ ] Clicar nos botões de classificação atualiza a seleção visualmente.
- [ ] Toggle de LLM salva e restaura corretamente.
- [ ] Aviso aparece quando `restrito`/`confidencial` + LLM ativado.
- [ ] "Salvar" grava no `chrome.storage` e muda badge para "configurado".
- [ ] "Capturar" envia `allow_external_llm=true` quando toggle ligado.
- [ ] `allow_external_llm` chega ao `Artifact.allow_external_llm` no banco.
- [ ] Pipeline usa LLM na extração quando `artifact.allow_external_llm=True`.
- [ ] Páginas `restrito`/`confidencial` não acionam LLM mesmo com flag `true`.
