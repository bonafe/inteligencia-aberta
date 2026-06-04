const LEVELS = ['publico', 'interno', 'restrito', 'confidencial'];
const LLM_BLOCKED_LEVELS = new Set(['restrito', 'confidencial']);
const DEFAULTS = {
  classification_level: 'restrito',
  allow_external_llm: false,
  user_id: '',
  tenant_id: '',
};

let currentDomain = '';

// ── Inicialização ────────────────────────────────────────────────────────────

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    setDomain('—', false);
    return;
  }

  try {
    const { hostname } = new URL(tab.url);
    currentDomain = hostname.replace(/^www\./, '');
  } catch {
    currentDomain = '';
  }

  setDomain(currentDomain || '—', false);

  const key = storageKey(currentDomain);
  const stored = await chrome.storage.local.get(key);
  const saved = stored[key] || null;

  applyConfig(saved || DEFAULTS);
  setBadge(!!saved);
  updateLlmWarning();
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function storageKey(domain) {
  return `config_${domain}`;
}

function setDomain(domain, configured) {
  document.getElementById('domain').textContent = domain;
  setBadge(configured);
}

function setBadge(configured) {
  const badge = document.getElementById('badge');
  if (configured) {
    badge.textContent = 'configurado';
    badge.className = 'badge badge-configured';
  } else {
    badge.textContent = 'padrão';
    badge.className = 'badge badge-default';
  }
}

function applyConfig(config) {
  // Classification level
  LEVELS.forEach(level => {
    document.getElementById(`level_${level}`)
      .classList.toggle('active', config.classification_level === level);
  });

  document.getElementById('llm_toggle').checked = !!config.allow_external_llm;
  document.getElementById('user_id').value = config.user_id || '';
  document.getElementById('tenant_id').value = config.tenant_id || '';
}

function readConfig() {
  const activeLevel = LEVELS.find(
    l => document.getElementById(`level_${l}`).classList.contains('active')
  ) || DEFAULTS.classification_level;

  return {
    classification_level: activeLevel,
    allow_external_llm: document.getElementById('llm_toggle').checked,
    user_id: document.getElementById('user_id').value.trim(),
    tenant_id: document.getElementById('tenant_id').value.trim(),
  };
}

function updateLlmWarning() {
  const activeLevel = LEVELS.find(
    l => document.getElementById(`level_${l}`).classList.contains('active')
  ) || 'restrito';
  const llmOn = document.getElementById('llm_toggle').checked;
  const warning = document.getElementById('llm_warning');
  warning.classList.toggle('visible', llmOn && LLM_BLOCKED_LEVELS.has(activeLevel));
}

function showStatus(msg, type) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = `status ${type}`;
}

function hideStatus() {
  const el = document.getElementById('status');
  el.className = 'status';
}

// ── Eventos ──────────────────────────────────────────────────────────────────

// Botões de classificação
LEVELS.forEach(level => {
  document.getElementById(`level_${level}`).addEventListener('click', () => {
    LEVELS.forEach(l => document.getElementById(`level_${l}`).classList.remove('active'));
    document.getElementById(`level_${level}`).classList.add('active');
    updateLlmWarning();
  });
});

// Toggle LLM
document.getElementById('llm_toggle').addEventListener('change', updateLlmWarning);

// Colapsável Identificação
document.getElementById('advanced-header').addEventListener('click', () => {
  document.getElementById('advanced-body').classList.toggle('open');
  document.getElementById('chevron').classList.toggle('open');
});

// Salvar configuração
document.getElementById('saveBtn').addEventListener('click', async () => {
  if (!currentDomain) return;

  const config = readConfig();
  config.saved_at = new Date().toISOString();

  await chrome.storage.local.set({ [storageKey(currentDomain)]: config });
  setBadge(true);
  showStatus(`✅ Configuração salva para ${currentDomain}`, 'success');
  setTimeout(hideStatus, 2500);
});

// Capturar e Enviar
document.getElementById('captureBtn').addEventListener('click', async () => {
  const btn = document.getElementById('captureBtn');
  btn.disabled = true;
  btn.textContent = 'Capturando…';
  hideStatus();

  const config = readConfig();

  try {
    const response = await chrome.runtime.sendMessage({
      action: 'capture_and_upload',
      config,
    });

    if (response.success) {
      showStatus('✅ Capturado com sucesso!', 'success');
    } else {
      showStatus('❌ ' + (response.error || 'Erro desconhecido'), 'error');
    }
  } catch (err) {
    showStatus('❌ Erro de comunicação com a extensão', 'error');
    console.error(err);
  }

  btn.disabled = false;
  btn.textContent = 'Capturar e Enviar';
});

// ── Iniciar ──────────────────────────────────────────────────────────────────
init();
