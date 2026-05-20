# Especificação: Extensão de Captura (Inteligência Aberta)

## 1. Objetivo
Criar uma extensão para o Google Chrome (e navegadores baseados em Chromium) capaz de capturar a "fotografia" exata de uma página web no momento da navegação. O foco é preservar a cadeia de custódia e o visual de páginas web (inclusive as que requerem login) para uso offline e indexação no sistema do Inteligência Aberta.

## 2. Abordagem Tecnológica
A extensão utilizará a API nativa `chrome.pageCapture` do Chrome para gerar um arquivo **MHTML** (.mhtml).
O MHTML é um formato de arquivo único que arquiva o código HTML da página juntamente com todos os seus recursos vinculados (imagens, applets, animações em Flash, arquivos de áudio, etc.) em um único arquivo, garantindo fidelidade visual e funcionamento offline.

## 3. Arquitetura da Extensão (Manifest V3)

A extensão será composta pelos seguintes arquivos principais:

*   **`manifest.json`**: Arquivo de configuração.
    *   *Permissões necessárias*: `activeTab` (para acessar a aba atual), `pageCapture` (para gerar o MHTML), `storage` (para salvar configurações como a URL da API e token de autenticação).
*   **`popup.html` / `popup.js`**: Interface do usuário. Um menu simples que aparece ao clicar no ícone da extensão, contendo o botão "Capturar para Inteligência Aberta" e o status do envio.
*   **`background.js` (Service Worker)**: O coração da extensão. Responsável por orquestrar a captura via `chrome.pageCapture.saveAsMHTML` e realizar as requisições HTTP (POST) para a API do Orchestrator/Portal.

## 4. Fluxo de Dados (Caminho Feliz)

1.  **Ação do Usuário**: O investigador abre uma página alvo (ex: perfil em rede social, notícia, fórum) e clica em "Capturar" na extensão.
2.  **Geração do Snapshot**: O `background.js` aciona a API `pageCapture` na aba ativa, gerando um objeto `Blob` contendo o MHTML da página.
3.  **Coleta de Metadados**: A extensão coleta informações contextuais:
    *   URL Original
    *   Título da aba (`document.title`)
    *   Timestamp (Data e hora exata da captura)
4.  **Envio (Upload)**: O `background.js` monta um `FormData` contendo o Blob do MHTML e os metadados, enviando via requisição HTTP `POST` para o endpoint da plataforma (ex: `http://localhost:8001/api/v1/capture/mhtml`).
5.  **Processamento no Backend**:
    *   A API recebe o arquivo e salva no armazenamento de objetos (**MinIO**), garantindo a imutabilidade do arquivo offline.
    *   A API lê o MHTML, extrai o texto limpo e envia para o banco vetorial (**Qdrant**) para habilitar busca semântica futura.
6.  **Feedback**: A extensão recebe a resposta de sucesso da API e mostra um "✅ Capturado com sucesso" no popup do usuário.

## 5. Integração com a Plataforma e Contexto de Envio

Para a extensão funcionar de forma consistente com o modelo de dados rígido do Inteligência Aberta (que exige que todo artefato pertença a um `tenant` e a um `user`), o fluxo de integração prevê duas abordagens (Fase 0 e Fases Futuras):

### O que a extensão deve enviar (Payload Multipart/form-data):
*   `file`: arquivo.mhtml (application/x-mimearchive)
*   `url`: "https://site-alvo.com/perfil"
*   `title`: "Nome do Perfil - Site Alvo"
*   `timestamp`: "2026-05-19T22:15:00Z"
*   `user_id` (Opcional): ID do usuário na plataforma.
*   `tenant_id` (Opcional): ID da organização.
*   `classification_level` (Opcional): Nível de sigilo da informação (padrão: `restrito`).

### Lógica de Configuração e Fallback (Fase 0 - MVP)
Como em uma primeira fase a extensão pode não ter um sistema de login complexo implementado, o sistema lida com o contexto da seguinte forma:

1. **Configuração na Extensão (Em breve)**: A extensão terá uma tela de "Opções" onde o investigador poderá configurar quem é o usuário operando, a qual organização ele está atrelando a captura, e o nível de sigilo padrão.
2. **Fallback no Backend**: Se a extensão enviar o arquivo sem especificar o `user_id` ou `tenant_id`, a API do Orquestrador aplicará a seguinte regra de resiliência:
   - Atribuirá a captura ao **primeiro usuário cadastrado** (assumindo ser o dono da plataforma/tenant).
   - Se esse usuário não possuir nenhuma `Organization` atrelada, o backend **criará automaticamente uma organização do tipo `individual`** em nome dele, garantindo que a restrição de banco de dados (`tenant_id` obrigatório) seja respeitada sem quebrar o fluxo.

## 6. Próximos Passos
- [ ] Inicializar o diretório `clients/browser-extension`.
- [ ] Criar o esqueleto do `manifest.json` com as permissões.
- [ ] Desenvolver a lógica de captura no `background.js`.
- [ ] Implementar o endpoint de recebimento no Orchestrator/Portal.
- [ ] Conectar o armazenamento do MinIO para receber os arquivos MHTML.
