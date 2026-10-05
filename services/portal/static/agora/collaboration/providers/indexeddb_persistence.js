//Local persistence of workspace documents and of the dev catalog, in IndexedDB (local-first, R-LF-1).
//Only the document is stored here; domain data never is (R-DOC-3, R-LF-4).
import { MemoryWorkspaceDoc } from '../document/memory_state_store.js';

const DB_VERSION = 1;
const SAVE_DELAY_MS = 150;

export class IndexedDbBackend {
    #name;
    #dbPromise = null;
    constructor(name = 'ultima-agora') { this.#name = name; }

    #db() {
        this.#dbPromise ??= new Promise((resolve, reject) => {
            const request = indexedDB.open(this.#name, DB_VERSION);
            request.onupgradeneeded = () => {
                request.result.createObjectStore('documents');                      //key: workspace id -> snapshot
                request.result.createObjectStore('workspaces', { keyPath: 'id' });  //dev catalog metadata
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
    listWorkspaces() { return this.#run('workspaces', 'readonly', s => s.getAll()); }
    getWorkspace(id) { return this.#run('workspaces', 'readonly', s => s.get(id)); }
    putWorkspace(meta) { return this.#run('workspaces', 'readwrite', s => s.put(meta)); }

    async close() { (await this.#dbPromise)?.close(); this.#dbPromise = null; }
}

//Same surface as IndexedDbBackend, in memory: the fallback when IndexedDB is unavailable (private windows,
//blocked site data). Nothing survives a reload.
export class MemoryBackend {
    #documents = new Map();
    #workspaces = new Map();
    async getDocument(id) { return this.#documents.get(id); }
    async putDocument(id, snapshot) { this.#documents.set(id, structuredClone(snapshot)); }
    async deleteDocument(id) { this.#documents.delete(id); }
    async listWorkspaces() { return structuredClone([...this.#workspaces.values()]); }
    async getWorkspace(id) { return structuredClone(this.#workspaces.get(id)); }
    async putWorkspace(meta) { this.#workspaces.set(meta.id, structuredClone(meta)); }
    async close() {}
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
