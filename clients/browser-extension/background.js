chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "capture_and_upload") {
        captureAndUpload()
            .then(() => sendResponse({ success: true }))
            .catch(error => {
                console.error("Capture Error:", error);
                sendResponse({ success: false, error: error.message });
            });
        
        // Retornar true indica que usaremos sendResponse de forma assíncrona
        return true; 
    }
});

async function captureAndUpload() {
    // 1. Pega a aba ativa atual
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    
    if (!tab) {
        throw new Error("Nenhuma aba ativa encontrada.");
    }

    if (tab.url.startsWith('chrome://') || tab.url.startsWith('edge://') || tab.url.startsWith('about:')) {
        throw new Error("Não é possível capturar páginas internas do navegador.");
    }

    // 2. Aciona o pageCapture para salvar em MHTML
    return new Promise((resolve, reject) => {
        chrome.pageCapture.saveAsMHTML({ tabId: tab.id }, async (mhtmlData) => {
            if (chrome.runtime.lastError) {
                return reject(new Error(chrome.runtime.lastError.message));
            }

            if (!mhtmlData) {
                return reject(new Error("Falha ao gerar arquivo MHTML."));
            }

            try {
                // 3. Montar os metadados e o arquivo no form-data
                const formData = new FormData();
                formData.append('file', mhtmlData, 'capture.mhtml');
                formData.append('url', tab.url);
                formData.append('title', tab.title || '');
                formData.append('timestamp', new Date().toISOString());

                // 4. Disparar para a API local (assumindo que o Orchestrator está na porta 8001)
                const apiUrl = 'http://localhost:8001/api/v1/capture/mhtml'; 
                
                const response = await fetch(apiUrl, {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`API retornou ${response.status}: ${errorText}`);
                }

                resolve();
            } catch (err) {
                reject(err);
            }
        });
    });
}
