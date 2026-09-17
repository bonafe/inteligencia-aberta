const API_URL = 'http://localhost:8001/api/v1/capture/mhtml';

const AUTH_KEY = 'auth';
// Mesmo teto do orchestrator (services/orchestrator/main.py:FAVICON_MAX_BYTES)
// — os dois lados concordam no limite pra não um aceitar o que o outro rejeita.
const FAVICON_MAX_BYTES = 300_000;

class NeedsLoginError extends Error {}

// chrome://favicon2 lê do cache de favicon do próprio Chrome — o ícone que já
// apareceu na aba, sem baixar nada da internet de novo. Evita dois problemas
// do favicon_url antigo (a URL que o site declara, buscada pelo orchestrator
// depois): sites que bloqueiam requisição sem navegador de verdade (a
// Wikipedia devolve 403 pra isso) e o favicon simplesmente não ter sido
// embutido no MHTML pelo Chrome. Ver
// https://developer.chrome.com/docs/extensions/how-to/ui/favicons —
// permissão "favicon" em manifest.json é o que habilita isto.
function urlDoFavicon(pageUrl) {
  const url = new URL(chrome.runtime.getURL('/_favicon/'));
  url.searchParams.set('pageUrl', pageUrl);
  url.searchParams.set('size', '32');
  return url.toString();
}

// Nunca levanta — favicon é cosmético, se algo der errado a captura segue
// sem ele (o portal já sabe emprestar o favicon de outra captura do mesmo
// domínio nesse caso, ver apps/artifacts/graph.py).
async function favIconParaDataUri(pageUrl) {
  try {
    const resp = await fetch(urlDoFavicon(pageUrl));
    if (!resp.ok) return '';
    const blob = await resp.blob();
    if (!blob.type.startsWith('image/') || blob.size === 0 || blob.size > FAVICON_MAX_BYTES) return '';
    const buffer = await blob.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binario = '';
    for (let i = 0; i < bytes.length; i++) binario += String.fromCharCode(bytes[i]);
    return `data:${blob.type};base64,${btoa(binario)}`;
  } catch (err) {
    return '';
  }
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'capture_and_upload') {
    captureAndUpload(request.config || {})
      .then(result => sendResponse({ success: true, correlationId: result?.correlationId }))
      .catch(err => {
        console.error('Capture Error:', err);
        if (err instanceof NeedsLoginError) {
          sendResponse({ success: false, needsLogin: true, error: err.message });
        } else {
          sendResponse({ success: false, error: err.message });
        }
      });
    // true = resposta assíncrona
    return true;
  }
});

async function getAccessToken() {
  const stored = await chrome.storage.local.get(AUTH_KEY);
  const auth = stored[AUTH_KEY];
  if (!auth || !auth.access) {
    throw new NeedsLoginError('Faça login na extensão antes de capturar.');
  }
  return auth.access;
}

async function captureAndUpload(config = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (!tab) {
    throw new Error('Nenhuma aba ativa encontrada.');
  }
  if (/^(chrome|edge|about):\/\//.test(tab.url)) {
    throw new Error('Não é possível capturar páginas internas do navegador.');
  }

  return new Promise((resolve, reject) => {
    chrome.pageCapture.saveAsMHTML({ tabId: tab.id }, async (mhtmlData) => {
      if (chrome.runtime.lastError) {
        return reject(new Error(chrome.runtime.lastError.message));
      }
      if (!mhtmlData) {
        return reject(new Error('Falha ao gerar arquivo MHTML.'));
      }

      try {
        const token = await getAccessToken();

        // Identificador desta captura, gerado no clique. Ele acompanha a página
        // por todo o sistema (orchestrator → MinIO → portal → workers) e é o que
        // permite ver a trilha completa no painel de eventos, começando aqui.
        const correlationId = crypto.randomUUID();

        const formData = new FormData();
        formData.append('file', mhtmlData, 'capture.mhtml');
        formData.append('url', tab.url);
        formData.append('title', tab.title || '');
        // O ícone da aba, já em data URI (ver favIconParaDataUri acima) — o
        // orchestrator só guarda, não baixa nada. Vazio se não achou/não tinha.
        formData.append('favicon_url', await favIconParaDataUri(tab.url));
        formData.append('timestamp', new Date().toISOString());
        formData.append('classification_level', config.classification_level || 'restrito');
        formData.append('allow_external_llm', config.allow_external_llm ? 'true' : 'false');
        formData.append('correlation_id', correlationId);
        // Identidade (user_id/tenant_id) vem das claims do JWT — não é mais enviada aqui.

        const response = await fetch(API_URL, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: formData,
        });

        if (response.status === 401) {
          return reject(new NeedsLoginError('Sessão expirada. Entre novamente na extensão.'));
        }
        if (!response.ok) {
          const body = await response.text().catch(() => '');
          return reject(new Error(`API retornou ${response.status}: ${body}`));
        }

        resolve({ correlationId });
      } catch (err) {
        reject(err);
      }
    });
  });
}
