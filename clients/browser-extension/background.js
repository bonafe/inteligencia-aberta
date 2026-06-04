const API_URL = 'http://localhost:8001/api/v1/capture/mhtml';

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'capture_and_upload') {
    captureAndUpload(request.config || {})
      .then(() => sendResponse({ success: true }))
      .catch(err => {
        console.error('Capture Error:', err);
        sendResponse({ success: false, error: err.message });
      });
    // true = resposta assíncrona
    return true;
  }
});

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
        const formData = new FormData();
        formData.append('file', mhtmlData, 'capture.mhtml');
        formData.append('url', tab.url);
        formData.append('title', tab.title || '');
        formData.append('timestamp', new Date().toISOString());
        formData.append('classification_level', config.classification_level || 'restrito');
        formData.append('allow_external_llm', config.allow_external_llm ? 'true' : 'false');
        if (config.user_id)   formData.append('user_id', config.user_id);
        if (config.tenant_id) formData.append('tenant_id', config.tenant_id);

        const response = await fetch(API_URL, { method: 'POST', body: formData });

        if (!response.ok) {
          const body = await response.text().catch(() => '');
          throw new Error(`API retornou ${response.status}: ${body}`);
        }

        resolve();
      } catch (err) {
        reject(err);
      }
    });
  });
}
