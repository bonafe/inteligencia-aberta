# Especificação: Visualizador de Artefatos (MHTML)

## 1. Objetivo
Criar uma interface web no **Portal (Django)** focada em exibir os artefatos de inteligência do tipo `documento` (especificamente as páginas web MHTML capturadas pela extensão). O objetivo é prover uma experiência rica onde o usuário consiga navegar rapidamente pelo histórico de capturas (thumbnails) e visualizar a "foto" da página preservada offline no próprio navegador, juntamente com seus metadados.

## 2. Layout e Experiência do Usuário (UI/UX)
A tela (chamada de *Galeria de Artefatos* ou *Visualizador de Capturas*) terá a seguinte disposição:

*   **Header / Seletor (Topo):**
    *   Um carrossel ou lista horizontal com mini-cards representando os artefatos capturados.
    *   Cada card exibirá o título da página, a URL resumida e a data/hora da captura.
    *   Ao clicar em um card, a página não recarrega inteira, mas atualiza o quadro principal via JavaScript.

*   **Painel de Metadados (Abaixo do seletor):**
    *   Exibe as informações do artefato selecionado: URL Original completa, Timestamp Exato, Nível de Classificação (Público, Restrito, etc) e ID do Artefato.
    *   **Ação Primária:** Um botão "Ver em Tela Cheia", que abre a página salva em uma nova aba do navegador para visualização expandida.

*   **Quadro de Visualização (Main Frame):**
    *   Um `<iframe>` estilizado que renderiza o arquivo HTML estático.
    *   **Segurança (Sandbox):** O iframe utilizará o atributo `sandbox=""` (restrito). Isso é vital porque a página capturada pode conter scripts maliciosos, popups indesejados ou tentar abrir aplicativos externos (ex: `xdg-open` no Linux ou `whatsapp://`). O sandbox garante que a página congele em formato puramente visual, desativando o JavaScript e blindando o nosso Portal contra *Cross-Site Scripting* (XSS) e comportamentos invasivos.

## 3. Arquitetura Backend (Django)

Para suportar essa interface, precisamos adicionar os seguintes componentes no app de Artefatos do Portal (`services/portal/apps/artifacts`):

1.  **View de Galeria (`ArtifactGalleryView`)**:
    *   Consulta a tabela `artifacts_artifact` filtrando apenas artefatos do tipo `documento` e cujo campo `content` contenha o caminho do MHTML (`mhtml_path`).
    *   Renderiza o template HTML `gallery.html`.

2.  **Endpoint Proxy do MinIO (`ServeMHTMLView`)**:
    *   **Problema:** O arquivo MHTML está no MinIO, cujo acesso direto pode requerer autenticação ou URLs pré-assinadas.
    *   **Solução:** Criar uma view no Django (`/artifacts/<uuid>/mhtml`) que se conecta ao MinIO (usando a lib `minio`), baixa o arquivo em memória, e o retorna em um `HttpResponse` ou `StreamingHttpResponse` com o cabeçalho `Content-Type: multipart/related`.
    *   Isso garante que o `<iframe>` no frontend aponte para o próprio Django, mantendo a autenticação e a segurança de acesso (Policy Engine) do portal.

## 4. Arquitetura Frontend (CSS Vanilla & JS)

*   **Estilização:** Será utilizado **Vanilla CSS** focado em estética "Dark Mode" premium, com efeitos de glassmorphism, cores vibrantes (escala de azuis/verdes para ambiente investigativo) e micro-interações (hover nos cards).
*   **Comportamento (Vanilla JS):** O clique em um thumbnail atualizará o atributo `src` do `iframe` e o texto dos elementos de metadados dinamicamente na DOM, criando a sensação de uma Single Page Application leve, sem necessidade de re-renderizar a tela inteira.

## 5. Próximos Passos de Implementação
- [ ] Adicionar o pacote `minio` no `requirements.txt` do Portal (Django).
- [ ] Implementar a lógica de Proxy MHTML (`views.py`) no app `artifacts`.
- [ ] Criar a View e Template da Galeria (`gallery.html`).
- [ ] Mapear as URLs no `urls.py`.
- [ ] Estilizar com CSS avançado e dinâmico.
