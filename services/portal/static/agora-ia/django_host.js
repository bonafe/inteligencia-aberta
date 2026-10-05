//Host adapter (docs/especificacao/09, 9.2 do projeto Ultima Agora): liga o Agora ao portal Django.
//
//  identidade   → o usuário da sessão do portal
//  catálogo     → /agora/api/v1/workspaces/ (papéis, classificação, arquivamento)
//  tokens       → POST .../token/ (curto; o agora-sync só confia nele)
//  domínio      → /agora/api/v1/dominio/ (leitura, sempre com o usuário da sessão)
//
//O documento colaborativo (composição e notas) é local-first: IndexedDB aqui e, quando o portal tem um
//agora-sync configurado, sincronizado por ele. Dados de domínio nunca entram no documento (só referências).
import { IndexedDbBackend, MemoryBackend } from '../agora/collaboration/providers/indexeddb_persistence.js';
import { YjsStateStore } from '../agora/collaboration/providers/yjs_store.js';
import { colorFor } from '../agora/collaboration/presence/presence.js';

const cookie = name => document.cookie.split('; ').find(row => row.startsWith(`${name}=`))?.split('=')[1];

export class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
}

//fetch with the session cookie and Django's CSRF header; JSON in, JSON out
export async function api(base, path, { method = 'GET', body = null, params = null } = {}) {
    const query = params ? `?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''))}` : '';
    const response = await fetch(`${base}${path}${query}`, {
        method, credentials: 'same-origin',
        headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}), ...(method !== 'GET' ? { 'X-CSRFToken': decodeURIComponent(cookie('csrftoken') ?? '') } : {}) },
        body: body ? JSON.stringify(body) : undefined,
    });
    if (response.status === 401 || response.redirected && /entrar/.test(response.url)) throw new ApiError(401, 'Sessão expirada: entre de novo no portal.');
    if (!response.ok) throw new ApiError(response.status, (await response.json().catch(() => ({}))).error ?? `HTTP ${response.status}`);
    return response.json();
}

//What a component sees as `this.domain`. forWorkspace() scopes it so that the API can flag objects that are
//more restricted than the workspace (R-CLS-3).
export function domainClient(base, workspaceId = null) {
    return {
        get: (path, params = {}) => api(`${base}/dominio`, path, { params: { ...params, workspace: workspaceId } }),
        forWorkspace: id => domainClient(base, id),
    };
}

const normalizar = meta => ({
    id: meta.id, title: meta.title, ownerId: meta.owner, role: meta.role, classification: meta.classification,
    updatedAt: meta.updated_at, archivedAt: meta.archived_at,
});

export class DjangoHost {
    #base;
    #config;
    store;
    identity;
    domain;
    persistent = true;
    syncEnabled;

    //One local database per user: documents cached on this device must not be reachable by someone else who logs in here
    constructor({ config, backend = new IndexedDbBackend(`ia-agora-${config.usuario.id}`) }) {
        this.#config = config;
        this.#base = config.api;
        this.syncEnabled = Boolean(config.sync_url);
        this.persistent = !(backend instanceof MemoryBackend);
        this.identity = { id: config.usuario.id, name: config.usuario.name, kind: 'human', color: colorFor(config.usuario.id) };
        this.domain = domainClient(this.#base);
        this.store = new YjsStateStore({
            backend,
            sync: config.sync_url ? { url: config.sync_url, session: id => api(this.#base, `/workspaces/${id}/token/`, { method: 'POST' }) } : null,
        });
        this.backend = backend;
    }

    //Falls back to memory when the browser has no IndexedDB, so the app never opens "dead"
    static async create({ config }) {
        try {
            const backend = new IndexedDbBackend(`ia-agora-${config.usuario.id}`);
            await Promise.race([backend.listWorkspaces(), new Promise((_, reject) => setTimeout(() => reject(new Error('IndexedDB não respondeu')), 3000))]);
            return new DjangoHost({ config, backend });
        } catch (error) {
            console.warn('[DjangoHost] IndexedDB indisponível, trabalhando em memória:', error);
            return new DjangoHost({ config, backend: new MemoryBackend() });
        }
    }

    // ---- WorkspaceCatalog ----

    async list() { return (await api(this.#base, '/workspaces/')).workspaces.map(normalizar); }

    //null when it does not exist or the user has no access (the API answers 404 for both, on purpose)
    async get(id) {
        try { return normalizar(await api(this.#base, `/workspaces/${id}/`)); }
        catch (error) { if (error.status === 404) return null; throw error; }
    }

    async create(title = 'Novo workspace') { return normalizar(await api(this.#base, '/workspaces/', { method: 'POST', body: { title } })); }
    async rename(id, title) { return normalizar(await api(this.#base, `/workspaces/${id}/`, { method: 'PATCH', body: { title } })); }
    async archive(id) { return normalizar(await api(this.#base, `/workspaces/${id}/archive/`, { method: 'POST' })); }
    async touch(id) { return this.get(id); }
    async adopt() { throw new Error('No portal, o acesso a um workspace é dado pelo dono; não há como adotar por link.'); }

    openDocument(meta) { return this.store.open(meta.id, { role: meta.role }); }
    discardLocal(id) { return this.store.discard(id); }

    //Copies composition and authored content into a new workspace (never domain data: the document only has references)
    async duplicate(id) {
        const source = await this.get(id);
        const doc = await this.openDocument(source);
        const copy = await this.create(`${source.title} (cópia)`);
        await this.store.importSnapshot(copy.id, { ...doc.snapshot(), meta: { ...(doc.get('meta') ?? {}), title: copy.title } });
        return copy;
    }
}
