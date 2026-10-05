//Host adapter (docs/especificacao/09, 9.2 do projeto Ultima Agora): liga o Agora ao portal Django.
//
//  identidade   → o usuário da sessão do portal
//  catálogo     → /agora/api/v1/workspaces/ (papéis, classificação, arquivamento), COM cópia local
//  tokens       → POST .../token/ (curto; o agora-sync só confia nele)
//  domínio      → /agora/api/v1/dominio/ (leitura, sempre com o usuário da sessão), COM cópia local por nível
//
//OFFLINE-FIRST: sem rede, tudo que já passou por aqui continua disponível: a lista e os metadados dos workspaces (cópia
//local), os documentos (IndexedDB, edição incluída), e o que o usuário já consultou (dentro da política de classificação
//da instância). Criar, renomear e arquivar offline ficam pendentes e são enviados quando a rede volta.
import { IndexedDbBackend, MemoryBackend } from '../agora/collaboration/providers/indexeddb_persistence.js';
import { YjsStateStore } from '../agora/collaboration/providers/yjs_store.js';
import { colorFor } from '../agora/collaboration/presence/presence.js';
import { uuid } from '../agora/core/ids.js';
import { DomainCache } from './offline_cache.js';

const cookie = name => document.cookie.split('; ').find(row => row.startsWith(`${name}=`))?.split('=')[1];

export class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
}

//The server cannot be reached (no network, or a proxy with nothing behind it). Status 0 on purpose: not an HTTP answer.
export class OfflineError extends ApiError {
    constructor(message = 'Sem conexão com o servidor.') { super(0, message); }
}
const UNREACHABLE = new Set([502, 503, 504]);

//fetch with the session cookie and Django's CSRF header; JSON in, JSON out
export async function api(base, path, { method = 'GET', body = null, params = null } = {}) {
    const query = params ? `?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''))}` : '';
    let response;
    try {
        response = await fetch(`${base}${path}${query}`, {
            method, credentials: 'same-origin',
            headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}), ...(method !== 'GET' ? { 'X-CSRFToken': decodeURIComponent(cookie('csrftoken') ?? '') } : {}) },
            body: body ? JSON.stringify(body) : undefined,
        });
    } catch (error) {
        throw new OfflineError();                              // fetch only rejects when the request could not be made at all
    }
    if (UNREACHABLE.has(response.status)) throw new OfflineError();
    if (response.status === 401 || response.redirected && /entrar/.test(response.url)) throw new ApiError(401, 'Sessão expirada: entre de novo no portal.');
    if (!response.ok) throw new ApiError(response.status, (await response.json().catch(() => ({}))).error ?? `HTTP ${response.status}`);
    return response.json();
}

//What a component sees as `this.domain`. forWorkspace() scopes it so that the API can flag objects that are
//more restricted than the workspace (R-CLS-3). With a cache: what was read is kept (within policy) and served
//when the server cannot be reached, marked { offline: true, cached_at } so the component can say so.
export function domainClient(base, workspaceId = null, cache = null) {
    return {
        async get(path, params = {}) {
            const sent = { ...params, workspace: workspaceId };
            const key = DomainCache.keyFor(path, sent);
            try {
                const data = await api(`${base}/dominio`, path, { params: sent });
                cache?.put(key, data).catch(() => {});         // a full disk must not break a read that worked
                return data;
            } catch (error) {
                if (!(error instanceof OfflineError)) throw error;
                const copy = await cache?.get(key).catch(() => null);
                if (!copy) throw new OfflineError('Sem conexão e sem cópia local deste dado.');
                return { ...copy.value, offline: true, cached_at: copy.at, omitted: copy.omitted };
            }
        },
        forWorkspace: id => domainClient(base, id, cache),
    };
}

const normalizar = meta => ({
    id: meta.id, title: meta.title, ownerId: meta.owner, role: meta.role, classification: meta.classification,
    updatedAt: meta.updated_at, archivedAt: meta.archived_at,
});

export class DjangoHost {
    #base;
    #config;
    #backend;
    #syncing = null;
    store;
    identity;
    domain;
    persistent = true;
    syncEnabled;

    //One local database per user: documents cached on this device must not be reachable by someone else who logs in here
    constructor({ config, backend = new IndexedDbBackend(`ia-agora-${config.usuario.id}`) }) {
        this.#config = config;
        this.#base = config.api;
        this.#backend = backend;
        this.syncEnabled = Boolean(config.sync_url);
        this.persistent = !(backend instanceof MemoryBackend);
        this.identity = { id: config.usuario.id, name: config.usuario.name, kind: 'human', color: colorFor(config.usuario.id) };

        const cache = new DomainCache({ backend, maxLevel: config.offline_cache_level ?? 'interno' });
        cache.enforce().catch(() => {});                      // the policy may have been tightened since the last visit
        this.domain = domainClient(this.#base, null, cache);

        this.store = new YjsStateStore({
            backend,
            sync: config.sync_url ? { url: config.sync_url, session: id => api(this.#base, `/workspaces/${id}/token/`, { method: 'POST' }) } : null,
        });
        addEventListener('online', () => this.syncPending().catch(() => {}));
        this.syncPending().catch(() => {});
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

    // ---- local copy of the catalog ----

    async #localList() {
        return (await this.#backend.listWorkspaces()).filter(w => !w.archivedAt)
            .sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
    }

    //Keeps the local copy in step with what the server said, WITHOUT touching workspaces that exist only here (pending)
    async #remember(metas) {
        const local = new Map((await this.#backend.listWorkspaces()).map(m => [m.id, m]));
        for (const meta of metas) {
            const mine = local.get(meta.id);
            await this.#backend.putWorkspace({ ...meta, ...(mine?.dirtyTitle ? { title: mine.title, dirtyTitle: true } : {}), ...(mine?.dirtyArchive ? { archivedAt: mine.archivedAt, dirtyArchive: true } : {}) });
        }
    }

    // ---- WorkspaceCatalog ----

    async list() {
        try {
            const fromServer = (await api(this.#base, '/workspaces/')).workspaces.map(normalizar);
            await this.#remember(fromServer);
            //What the server no longer lists and is not pending here is gone (archived or access revoked): hide it
            const known = new Set(fromServer.map(m => m.id));
            for (const mine of await this.#backend.listWorkspaces()) {
                if (!known.has(mine.id) && !mine.pending && !mine.archivedAt) await this.#backend.putWorkspace({ ...mine, archivedAt: new Date().toISOString() });
            }
            this.syncPending().catch(() => {});
        } catch (error) {
            if (!(error instanceof OfflineError)) throw error;
        }
        return this.#localList();
    }

    //null when it does not exist or the user has no access (the API answers 404 for both, on purpose)
    async get(id) {
        try {
            const meta = normalizar(await api(this.#base, `/workspaces/${id}/`));
            await this.#remember([meta]);
            return meta;
        } catch (error) {
            if (error.status === 404) {
                const mine = await this.#backend.getWorkspace(id);
                return mine?.pending ? mine : null;             // not on the server YET: created offline
            }
            if (!(error instanceof OfflineError)) throw error;
            return (await this.#backend.getWorkspace(id)) ?? null;
        }
    }

    //The id is chosen HERE, so a workspace made offline already has its identity (and its document) before the server knows it
    async create(title = 'Novo workspace') {
        const id = uuid();
        try {
            const meta = normalizar(await api(this.#base, '/workspaces/', { method: 'POST', body: { id, title } }));
            await this.#remember([meta]);
            return meta;
        } catch (error) {
            if (!(error instanceof OfflineError)) throw error;
            const now = new Date().toISOString();
            const meta = { id, title, ownerId: this.identity.id, role: 'owner', classification: 'restrito', updatedAt: now, archivedAt: null, pending: true };
            await this.#backend.putWorkspace(meta);
            return meta;
        }
    }

    async rename(id, title) { return this.#change(id, { title, dirtyTitle: true }, () => api(this.#base, `/workspaces/${id}/`, { method: 'PATCH', body: { title } })); }
    async archive(id) { return this.#change(id, { archivedAt: new Date().toISOString(), dirtyArchive: true }, () => api(this.#base, `/workspaces/${id}/archive/`, { method: 'POST' })); }
    async touch(id) { return this.#backend.getWorkspace(id); }
    async adopt() { throw new Error('No portal, o acesso a um workspace é dado pelo dono; não há como adotar por link.'); }

    //Applies the change locally FIRST (the UI never waits for the network), then tells the server; offline, it stays pending
    async #change(id, patch, remote) {
        const mine = await this.#backend.getWorkspace(id);
        if (!mine) throw new Error(`Workspace desconhecido: ${id}`);
        const local = { ...mine, ...patch, updatedAt: new Date().toISOString() };
        await this.#backend.putWorkspace(local);
        if (mine.pending) return local;                          // the server will receive the final title/state when it creates it
        try {
            await remote();
            const synced = { ...local };
            delete synced.dirtyTitle; delete synced.dirtyArchive;
            await this.#backend.putWorkspace(synced);
            return synced;
        } catch (error) {
            if (error instanceof OfflineError) return local;     // stays dirty; syncPending() sends it later
            throw error;
        }
    }

    //Sends what was done offline. Safe to call any time; one run at a time. Returns how many things were sent.
    syncPending() {
        this.#syncing ??= (async () => {
            let sent = 0;
            for (const meta of await this.#backend.listWorkspaces()) {
                try {
                    if (meta.pending) {
                        const created = normalizar(await api(this.#base, '/workspaces/', { method: 'POST', body: { id: meta.id, title: meta.title } }));
                        if (meta.archivedAt) await api(this.#base, `/workspaces/${meta.id}/archive/`, { method: 'POST' });
                        await this.#backend.putWorkspace({ ...created, ...(meta.archivedAt ? { archivedAt: meta.archivedAt } : {}) });
                        sent++;
                    } else {
                        if (meta.dirtyTitle) { await api(this.#base, `/workspaces/${meta.id}/`, { method: 'PATCH', body: { title: meta.title } }); sent++; }
                        if (meta.dirtyArchive) { await api(this.#base, `/workspaces/${meta.id}/archive/`, { method: 'POST' }); sent++; }
                        if (meta.dirtyTitle || meta.dirtyArchive) {
                            const clean = { ...meta }; delete clean.dirtyTitle; delete clean.dirtyArchive;
                            await this.#backend.putWorkspace(clean);
                        }
                    }
                } catch (error) {
                    if (error instanceof OfflineError) break;     // still no network: try again later, keep everything
                    console.warn(`[DjangoHost] could not send ${meta.id}:`, error.message);   // refused by the server: keep it, do not loop on it
                }
            }
            return sent;
        })().finally(() => { this.#syncing = null; });
        return this.#syncing;
    }

    async pendingCount() {
        return (await this.#backend.listWorkspaces()).filter(m => m.pending || m.dirtyTitle || m.dirtyArchive).length;
    }

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
