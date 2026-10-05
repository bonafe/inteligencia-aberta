//Workspace chat on the client (docs/especificacao/06, 6.1). Transport-agnostic: it talks to a SyncProvider through
//sendChat() and receives events through receive(). Everything the user sees is LOCAL first:
//
//  - the history is kept on this device, so the chat reads offline (what was authored here is never purged by the
//    data-cache policy: it is workspace content, like the notes);
//  - sending is optimistic and goes through an OUTBOX: offline, a message stays "pendente" and is sent, once, when the
//    connection comes back (the server deduplicates by clientId, so a retry can never repeat a message);
//  - unread counts and mentions are per person and per device.
import { uuid } from '../../core/ids.js';

const MAX_LOCAL = 300;                    // history kept per workspace on this device

export class ChatClient {
    #workspaceId; #me; #storage; #send; #canWrite;
    #messages = [];                       // confirmed by the server, in order of seq
    #outbox = [];                         // { clientId, body, createdAt, state: 'pending'|'sending'|'failed', error? }
    #lastRead = 0;
    #hasMore = false;
    #listeners = new Set();
    #loaded;

    //storage: { getLocal(key), putLocal(key, value) } | null.  send(request) -> boolean (false = not connected).
    //me: { id, name }.  canWrite(): the user's role may write the chat (live, it can change in a session).
    constructor({ workspaceId, me, storage = null, send = () => false, canWrite = () => true, available = () => true }) {
        this.#workspaceId = workspaceId;
        this.#me = me;
        this.#storage = storage;
        this.#send = send;
        this.#canWrite = canWrite;
        this.available = available;
        this.#loaded = this.#load();
    }

    get ready() { return this.#loaded; }
    get me() { return this.#me; }
    get canWrite() { return this.#canWrite(); }
    get hasMore() { return this.#hasMore; }
    get lastRead() { return this.#lastRead; }

    //Confirmed messages followed by the ones still waiting to go out (what the person expects to see)
    get messages() {
        return [...this.#messages, ...this.#outbox.map(o => ({ id: `local:${o.clientId}`, clientId: o.clientId, author: this.#me, body: o.body, createdAt: o.createdAt, mentions: [], pending: o.state, error: o.error }))];
    }

    get unread() { return this.#messages.filter(m => m.seq > this.#lastRead && m.author.id !== this.#me.id && !m.deletedAt).length; }
    get unreadMentions() { return this.#messages.filter(m => m.seq > this.#lastRead && m.author.id !== this.#me.id && !m.deletedAt && this.mentionsMe(m)).length; }

    //"@Maria" matches the person called Maria (first name or the full name without spaces), case-insensitively
    mentionsMe(message) {
        const mine = new Set([this.#me.name.toLowerCase().replace(/\s+/g, ''), this.#me.name.toLowerCase().split(/\s+/)[0]]);
        return (message.mentions ?? []).some(token => mine.has(token));
    }

    onChange(fn) { this.#listeners.add(fn); return () => this.#listeners.delete(fn); }
    #changed() { for (const fn of [...this.#listeners]) { try { fn(); } catch (error) { console.error('[chat] listener error:', error); } } this.#save().catch(() => {}); }

    // ---- persistence on this device ----

    get #key() { return `chat:${this.#workspaceId}`; }

    async #load() {
        const saved = await this.#storage?.getLocal(this.#key).catch(() => null);
        if (saved) {
            this.#messages = saved.messages ?? [];
            this.#outbox = (saved.outbox ?? []).map(o => ({ ...o, state: 'pending' }));         // anything "sending" when the page closed is retried
            this.#lastRead = saved.lastRead ?? 0;
            this.#hasMore = saved.hasMore ?? false;
        }
        this.#listeners.forEach(fn => fn());
    }

    async #save() {
        await this.#loaded;
        await this.#storage?.putLocal(this.#key, { messages: this.#messages.slice(-MAX_LOCAL), outbox: this.#outbox, lastRead: this.#lastRead, hasMore: this.#hasMore });
    }

    // ---- events from the server ----

    receive(event) {
        switch (event.type) {
            case 'history': {
                //History wins for messages we already have (edits and deletions that happened while we were away)
                const byId = new Map([...this.#messages, ...event.messages].map(m => [m.id, m]));
                this.#messages = [...byId.values()].sort((a, b) => a.seq - b.seq);
                //A page that reaches our oldest known message tells whether anything older exists
                if ((event.messages[0]?.seq ?? Infinity) <= (this.#messages[0]?.seq ?? Infinity)) this.#hasMore = event.hasMore;
                break;
            }
            case 'message': this.#confirm(event.message); break;
            case 'updated': case 'deleted': this.#replace(event.message); break;
            case 'error': this.#fail(event); return this.#changed();
            default: return;
        }
        this.#changed();
    }

    //A confirmed message may be one of ours waiting in the outbox (same clientId): it replaces the pending copy
    #confirm(message) {
        this.#outbox = this.#outbox.filter(o => o.clientId !== message.clientId || message.author.id !== this.#me.id);
        if (this.#messages.some(m => m.id === message.id)) return this.#replace(message);
        this.#messages.push(message);
        this.#messages.sort((a, b) => a.seq - b.seq);
    }

    #replace(message) {
        const index = this.#messages.findIndex(m => m.id === message.id);
        if (index === -1) this.#confirm(message); else this.#messages[index] = message;
    }

    #fail(event) {
        const item = this.#outbox.find(o => o.clientId === event.ref);
        if (!item) return;
        //Rate limiting is temporary (keep it, it goes out later); anything else is a refusal the person must see
        if (event.code === 'rate') item.state = 'pending';
        else { item.state = 'failed'; item.error = event.message; }
    }

    // ---- actions ----

    //Queues the message and tries to send it at once; returns the clientId. Never throws for "offline": it just waits.
    send(body) {
        const text = String(body ?? '').trim();
        if (!text) throw new Error('A mensagem está vazia.');
        if (!this.#canWrite()) throw new Error('Seu papel só permite ler o chat.');
        const item = { clientId: uuid(), body: text, createdAt: new Date().toISOString(), state: 'pending' };
        this.#outbox.push(item);
        this.#changed();
        this.flush();
        return item.clientId;
    }

    //Sends what is waiting, oldest first. Safe to call whenever the connection may have come back.
    flush() {
        for (const item of this.#outbox) {
            if (item.state === 'failed') continue;
            if (!this.#send({ type: 'send', clientId: item.clientId, body: item.body })) break;     // not connected: keep order, try later
            item.state = 'sending';
        }
        this.#changed();
    }

    retry(clientId) {
        const item = this.#outbox.find(o => o.clientId === clientId);
        if (item) { item.state = 'pending'; delete item.error; this.flush(); }
    }

    discard(clientId) { this.#outbox = this.#outbox.filter(o => o.clientId !== clientId); this.#changed(); }

    edit(id, body) { if (!this.#send({ type: 'edit', id, body: String(body).trim() })) throw new Error('Sem conexão: não é possível editar agora.'); }
    remove(id) { if (!this.#send({ type: 'delete', id })) throw new Error('Sem conexão: não é possível remover agora.'); }

    loadOlder() {
        const first = this.#messages[0];
        return this.#hasMore && first ? this.#send({ type: 'history', before: first.seq, limit: 100 }) : false;
    }

    markRead() {
        const last = this.#messages.at(-1)?.seq ?? 0;
        if (last > this.#lastRead) { this.#lastRead = last; this.#changed(); }
    }

    //Called when the connection is (re)established: ask for what we missed and send what was waiting
    onConnected() {
        this.#send({ type: 'history', limit: 100 });
        this.flush();
    }
}
