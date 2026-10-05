//Local persistence of workspace documents and of the dev catalog, in IndexedDB (local-first, R-LF-1).
//Only the document is stored here; domain data never is (R-DOC-3, R-LF-4).
import { MemoryWorkspaceDoc } from '../document/memory_state_store.js';

const DB_VERSION = 3;
const SAVE_DELAY_MS = 150;

export class IndexedDbBackend {
    #name;
    #dbPromise = null;
    constructor(name = 'ultima-agora') { this.#name = name; }

    #db() {
        this.#dbPromise ??= new Promise((resolve, reject) => {
            const request = indexedDB.open(this.#name, DB_VERSION);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains('documents')) db.createObjectStore('documents');                      //key: workspace id -> snapshot
                if (!db.objectStoreNames.contains('workspaces')) db.createObjectStore('workspaces', { keyPath: 'id' });  //catalog metadata
                if (!db.objectStoreNames.contains('cache')) db.createObjectStore('cache');
                if (!db.objectStoreNames.contains('local')) db.createObjectStore('local');                             //v3: what the user authored here (chat history, unsent messages): never subject to the data-cache policy                              //v2: offline copies of host data (key -> entry)
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
        return this.#dbPromise;
    }

    async #run(store, mode, fn) {
        const db = await this.#db();
        return new Promise((resolve, reject) => {
            const transaction = db.transaction(store, mode);
            const request = fn(transaction.objectStore(store));
            transaction.oncomplete = () => resolve(request?.result);
            transaction.onerror = transaction.onabort = () => reject(transaction.error);
        });
    }

    getDocument(id) { return this.#run('documents', 'readonly', s => s.get(id)); }
    putDocument(id, snapshot) { return this.#run('documents', 'readwrite', s => s.put(snapshot, id)); }
    deleteDocument(id) { return this.#run('documents', 'readwrite', s => s.delete(id)); }
    //Offline copies of data the host serves (domain objects the user already looked at). The host decides what may
    //be stored here (classification policy); this is only the storage.
    //Authored-here data (chat history, unsent messages). Kept apart from `cache` on purpose: the data-cache policy purges `cache`.
    getLocal(key) { return this.#run('local', 'readonly', s => s.get(key)); }
    putLocal(key, value) { return this.#run('local', 'readwrite', s => s.put(value, key)); }
    deleteLocal(key) { return this.#run('local', 'readwrite', s => s.delete(key)); }

    getCache(key) { return this.#run('cache', 'readonly', s => s.get(key)); }
    putCache(key, entry) { return this.#run('cache', 'readwrite', s => s.put(entry, key)); }
    deleteCache(key) { return this.#run('cache', 'readwrite', s => s.delete(key)); }
    async cacheEntries() {
        const db = await this.#db();
        return new Promise((resolve, reject) => {
            const transaction = db.transaction('cache', 'readonly');
            const entries = [];
            const cursor = transaction.objectStore('cache').openCursor();
            cursor.onsuccess = () => { const c = cursor.result; if (c) { entries.push([c.key, c.value]); c.continue(); } };
            transaction.oncomplete = () => resolve(entries);
            transaction.onerror = transaction.onabort = () => reject(transaction.error);
        });
    }
    listWorkspaces() { return this.#run('workspaces', 'readonly', s => s.getAll()); }
    getWorkspace(id) { return this.#run('workspaces', 'readonly', s => s.get(id)); }
    putWorkspace(meta) { return this.#run('workspaces', 'readwrite', s => s.put(meta)); }

    async close() { (await this.#dbPromise)?.close(); this.#dbPromise = null; }

    //Deletes the whole local database (documents, catalog, cached host data). Irreversible.
    async destroy() {
        await this.close();
        await new Promise(resolve => { const request = indexedDB.deleteDatabase(this.#name); request.onsuccess = request.onerror = request.onblocked = resolve; });
    }
}

//Same surface as IndexedDbBackend, in memory: the fallback when IndexedDB is unavailable (private windows,
//blocked site data). Nothing survives a reload.
export class MemoryBackend {
    #documents = new Map();
    #workspaces = new Map();
    #cache = new Map();
    #local = new Map();
    async getLocal(key) { return this.#local.get(key); }
    async putLocal(key, value) { this.#local.set(key, structuredClone(value)); }
    async deleteLocal(key) { this.#local.delete(key); }
    async getCache(key) { return this.#cache.get(key); }
    async putCache(key, entry) { this.#cache.set(key, structuredClone(entry)); }
    async deleteCache(key) { this.#cache.delete(key); }
    async cacheEntries() { return structuredClone([...this.#cache.entries()]); }
    async getDocument(id) { return this.#documents.get(id); }
    async putDocument(id, snapshot) { this.#documents.set(id, structuredClone(snapshot)); }
    async deleteDocument(id) { this.#documents.delete(id); }
    async listWorkspaces() { return structuredClone([...this.#workspaces.values()]); }
    async getWorkspace(id) { return structuredClone(this.#workspaces.get(id)); }
    async putWorkspace(meta) { this.#workspaces.set(meta.id, structuredClone(meta)); }
    async close() {}
    async destroy() { this.#documents.clear(); this.#workspaces.clear(); this.#cache.clear(); this.#local.clear(); }
}

//StateStore that loads a document from IndexedDB and saves it, debounced, on every change.
//doc.status: 'synced' (everything written) | 'saving' | 'error'.
export class PersistentStateStore {
    #backend;
    #docs = new Map();
    constructor(backend) { this.#backend = backend; }

    async open(workspaceId) {
        if (this.#docs.has(workspaceId)) return this.#docs.get(workspaceId);
        const doc = new MemoryWorkspaceDoc();
        const snapshot = await this.#backend.getDocument(workspaceId);
        if (snapshot) doc.restore(snapshot);

        let timer = null;
        const flush = async () => {
            clearTimeout(timer); timer = null;
            try { await this.#backend.putDocument(workspaceId, doc.snapshot()); if (!timer) doc.status = 'synced'; }
            catch (error) { doc.status = 'error'; console.error('[PersistentStateStore] could not save:', error); }
        };
        doc.flush = flush;
        doc.onChange('', () => {
            doc.status = 'saving';
            clearTimeout(timer);
            timer = setTimeout(flush, SAVE_DELAY_MS);
        });
        //A closing tab must not lose the last edits
        addEventListener('pagehide', () => { if (timer) flush(); });

        this.#docs.set(workspaceId, doc);
        return doc;
    }

    forget(workspaceId) { this.#docs.delete(workspaceId); }
}
