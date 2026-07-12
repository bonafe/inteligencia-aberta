const LEVELS = ['publico', 'interno', 'restrito', 'confidencial'];
const LLM_BLOCKED_LEVELS = new Set(['restrito', 'confidencial']);
const DEFAULTS = {
  classification_level: 'restrito',
  allow_external_llm: false,
};

const TOKEN_URL = 'http://localhost:8000/api/v1/token/';
const AUTH_KEY = 'auth';  // chave global (a identidade é da conta, não do domínio)

let currentDomain = '';

// ── Inicialização ────────────────────────────────────────────────────────────

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    setDomain('—', false);
  } else {
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

  await refreshAuthUI();
}

// ── Autenticação ─────────────────────────────────────────────────────────────

async function getAuth() {
  const stored = await chrome.storage.local.get(AUTH_KEY);
  return stored[AUTH_KEY] || null;
}

async function refreshAuthUI() {
  const auth = await getAuth();
  const loggedIn = !!(auth && auth.access);

  document.getElementById('login-form').style.display = loggedIn ? 'none' : 'block';
  document.getElementById('account-info').style.display = loggedIn ? 'block' : 'none';
  if (loggedIn) {
    document.getElementById('account-username').textContent = auth.username || '—';
  }

  // Sem login, não dá para capturar (o orchestrator exige Bearer).
  const captureBtn = document.getElementById('captureBtn');
  captureBtn.disabled = !loggedIn;
  captureBtn.title = loggedIn ? '' : 'Faça login para capturar';
}

async function doLogin() {
  const username = document.getElementById('login_username').value.trim();
  const password = document.getElementById('login_password').value;
  if (!username || !password) {
    showStatus('❌ Informe usuário e senha', 'error');
    return;
  }

  const btn = document.getElementById('loginBtn');
  btn.disabled = true;
  btn.textContent = 'Entrando…';

  try {
    const resp = await fetch(TOKEN_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });

    if (!resp.ok) {
      showStatus(resp.status === 401 ? '❌ Usuário ou senha inválidos' : `❌ Erro ${resp.status}`, 'error');
      return;
    }

    const { access, refresh } = await resp.json();
    await chrome.storage.local.set({
      [AUTH_KEY]: { access, refresh, username, saved_at: new Date().toISOString() },
    });
    document.getElementById('login_password').value = '';
    showStatus('✅ Login efetuado', 'success');
    setTimeout(hideStatus, 2000);
    await refreshAuthUI();
  } catch (err) {
    showStatus('❌ Não foi possível conectar ao portal', 'error');
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Entrar';
  }
}

async function doLogout() {
  await chrome.storage.local.remove(AUTH_KEY);
  await refreshAuthUI();
  showStatus('Sessão encerrada', 'info');
  setTimeout(hideStatus, 2000);
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
  LEVELS.forEach(level => {
    document.getElementById(`level_${level}`)
      .classList.toggle('active', config.classification_level === level);
  });
  document.getElementById('llm_toggle').checked = !!config.allow_external_llm;
}

function readConfig() {
  const activeLevel = LEVELS.find(
    l => document.getElementById(`level_${l}`).classList.contains('active')
  ) || DEFAULTS.classification_level;

  return {
    classification_level: activeLevel,
    allow_external_llm: document.getElementById('llm_toggle').checked,
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

// Login / logout
document.getElementById('loginBtn').addEventListener('click', doLogin);
document.getElementById('logoutBtn').addEventListener('click', doLogout);

// Salvar configuração do domínio
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
    } else if (response.needsLogin) {
      showStatus('❌ Sessão expirada — entre novamente', 'error');
      await refreshAuthUI();
    } else {
      showStatus('❌ ' + (response.error || 'Erro desconhecido'), 'error');
    }
  } catch (err) {
    showStatus('❌ Erro de comunicação com a extensão', 'error');
    console.error(err);
  }

  btn.textContent = 'Capturar e Enviar';
  await refreshAuthUI();  // reabilita o botão conforme o estado de login
});

// ── Iniciar ──────────────────────────────────────────────────────────────────
init();
