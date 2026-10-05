//StateStore for the real application: Yjs document + IndexedDB persistence + optional sync server.
//Local-first (R-LF-1): open() answers from IndexedDB at once; the network connects in the background and
//everything merges when it does.
//
//sync: { url, session(workspaceId) -> Promise<{ token, role }> }   omit for a purely local store
import { Y, awarenessProtocol } from '../../vendor/yjs/yjs.js';
import { YjsWorkspaceDoc } from '../document/yjs_state_store.js';
import { YjsPresence } from '../presence/presence.js';
import { SyncProvider, webSocketTransport } from './sync_provider.js';

const SAVE_DELAY_MS = 150;
const PERSISTENCE = 'persistence';

export class YjsStateStore {
    #backend;
    #sync;
    #docs = new Map();

    constructor({ backend, sync = null }) {
        this.#backend = backend;
        this.#sync = sync;
    }

    get synced() { return this.#sync !== null; }

    async #load(workspaceId, ydoc, doc) {
        const stored = await this.#backend.getDocument(workspaceId);
        if (stored instanceof Uint8Array) Y.applyUpdate(ydoc, stored, PERSISTENCE);
        else if (stored) doc.restore(stored);                 //data saved by M1/M2, as a plain JSON snapshot
    }

    //role: what the catalog last knew about this user in this workspace (until the server says otherwise)
    async open(workspaceId, { role = 'owner' } = {}) {
        if (this.#docs.has(workspaceId)) return this.#docs.get(workspaceId);
        const ydoc = new Y.Doc();
        const doc = new YjsWorkspaceDoc(ydoc);
        doc.role = role;
        await this.#load(workspaceId, ydoc, doc);

        const awareness = new awarenessProtocol.Awareness(ydoc);
        doc.presence = new YjsPresence(awareness);

        let timer = null;
        let provider = null;
        const refreshStatus = () => {
            doc.status = provider && provider.status !== 'synced' ? provider.status : (timer ? 'saving' : 'synced');
        };
        const flush = async () => {
            clearTimeout(timer); timer = null;
            try { await this.#backend.putDocument(workspaceId, Y.encodeStateAsUpdate(ydoc)); }
            catch (error) { doc.status = 'error'; console.error('[YjsStateStore] could not save:', error); return; }
            refreshStatus();
        };
        doc.flush = flush;
        ydoc.on('update', (update, origin) => {
            if (origin === PERSISTENCE) return;
            clearTimeout(timer);
            timer = setTimeout(flush, SAVE_DELAY_MS);
            refreshStatus();
        });
        addEventListener('pagehide', () => { if (timer) flush(); });

        if (this.#sync) {
            let session = null;
            provider = new SyncProvider({
                ydoc, awareness,
                openTransport: async () => {
                    session = await this.#sync.session(workspaceId);
                    if (session.role) doc.role = session.role;
                    return webSocketTransport(`${this.#sync.url}/${encodeURIComponent(workspaceId)}`, session.token);
                },
                onStatus: refreshStatus,
                onControl: message => {
                    if (message.type === 'role') doc.role = message.role;
                    if (message.type === 'closed' && message.code === 4403) doc.role = 'viewer';
                    doc._control(message);
                },
            });
            doc.provider = provider;
            doc.status = 'connecting';
            provider.connect();
        }

        this.#docs.set(workspaceId, doc);
        return doc;
    }

    //Creates a workspace from a plain snapshot (duplicate, import of old data)
    async importSnapshot(workspaceId, snapshot) {
        const ydoc = new Y.Doc();
        new YjsWorkspaceDoc(ydoc).restore(snapshot);
        await this.#backend.putDocument(workspaceId, Y.encodeStateAsUpdate(ydoc));
    }

    //Throws away this device's copy of a workspace (local edits the server refused would otherwise be
    //re-sent, and refused, on every connection). The next open starts from what the server has.
    async discard(workspaceId) {
        this.forget(workspaceId);
        await this.#backend.deleteDocument(workspaceId);
    }

    //Stops every open document (their saves would recreate what is being wiped)
    closeAll() { for (const id of [...this.#docs.keys()]) this.forget(id); }

    forget(workspaceId) {
        this.#docs.get(workspaceId)?.provider?.destroy();
        this.#docs.delete(workspaceId);
    }
}
