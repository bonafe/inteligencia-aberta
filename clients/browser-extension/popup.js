document.getElementById('captureBtn').addEventListener('click', async () => {
    const btn = document.getElementById('captureBtn');
    const status = document.getElementById('status');
    
    btn.disabled = true;
    btn.innerText = 'Capturando...';
    status.style.display = 'none';

    try {
        // Envia mensagem para o background script para iniciar a captura
        const response = await chrome.runtime.sendMessage({ action: "capture_and_upload" });
        
        if (response.success) {
            status.innerText = '✅ Capturado com sucesso!';
            status.style.color = '#198754';
        } else {
            status.innerText = '❌ Erro: ' + response.error;
            status.style.color = '#dc3545';
        }
    } catch (e) {
        status.innerText = '❌ Erro de comunicação com a extensão!';
        status.style.color = '#dc3545';
        console.error(e);
    }
    
    status.style.display = 'block';
    btn.disabled = false;
    btn.innerText = 'Capturar e Enviar';
});
