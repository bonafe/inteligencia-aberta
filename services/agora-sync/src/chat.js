//Workspace chat (docs/especificacao/06, 6.1): an append-only log per workspace, OUTSIDE the collaborative document.
//
//Why a log and not the CRDT: messages grow without bound, have their own retention, and a viewer may read but not write.
//Each line of <workspace>.chat.jsonl is one event ({t:'msg'} | {t:'edit'} | {t:'del'}); the state is rebuilt by replay.
//The author is ALWAYS taken from the token (never from the client), and deleting redacts the text (a tombstone remains,
//so history keeps its order and the other participants see that something was removed).
import { appendFileSync, existsSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

export const MAX_BODY = 4000;
const DAY_MS = 86_400_000;

export class ChatError extends Error {
    constructor(code, message) { super(message); this.code = code; }
}

//@Maria, @maria.silva -> ['maria', 'maria.silva'] (lowercase, unique): who the message mentions, for notifications
export const mentionsIn = body => [...new Set([...body.matchAll(/(^|[\s(])@([\p{L}\p{N}_.-]{1,40})/gu)].map(m => m[2].toLowerCase().replace(/[.-]+$/, '')))];

export class ChatLog {
    #file; #retentionMs; #now;
    #messages = [];                         // in order of seq
    #byId = new Map();
    #byClient = new Map();                  // "authorId:clientId" -> message (makes sending idempotent)
    #seq = 0;

    constructor({ file = null, retentionDays = 0, now = () => Date.now() } = {}) {
        this.#file = file;
        this.#retentionMs = retentionDays > 0 ? retentionDays * DAY_MS : 0;
        this.#now = now;
        if (file && existsSync(file)) this.#replay(readFileSync(file, 'utf8'));
        this.#applyRetention();
    }

    #replay(text) {
        for (const line of text.split('\n')) {
            if (!line.trim()) continue;
            try {
                const event = JSON.parse(line);
                if (event.t === 'msg') this.#insert(event.m);
                else if (event.t === 'edit') { const m = this.#byId.get(event.id); if (m && !m.deletedAt) { m.body = event.body; m.mentions = mentionsIn(event.body); m.editedAt = event.at; } }
                else if (event.t === 'del') { const m = this.#byId.get(event.id); if (m) { m.body = ''; m.mentions = []; m.deletedAt = event.at; } }
            } catch { /* a torn last line (crash mid-write) must not lose the rest of the history */ }
        }
    }

    #insert(message) {
        this.#messages.push(message);
        this.#byId.set(message.id, message);
        this.#byClient.set(`${message.author.id}:${message.clientId}`, message);
        this.#seq = Math.max(this.#seq, message.seq);
    }

    //A failing disk must not corrupt the in-memory state or crash the session: the caller turns it into a ChatError
    #append(event) {
        if (!this.#file) return;
        try { appendFileSync(this.#file, `${JSON.stringify(event)}\n`); }
        catch { throw new ChatError('storage', 'Não foi possível gravar a mensagem agora. Tente de novo em instantes.'); }
    }

    //Drops what is older than the retention window and compacts the file (the window is a per-instance policy, D-11)
    #applyRetention() {
        if (!this.#retentionMs) return;
        const cutoff = this.#now() - this.#retentionMs;
        const kept = this.#messages.filter(m => Date.parse(m.createdAt) >= cutoff);
        if (kept.length === this.#messages.length) return;
        this.#messages = kept;
        this.#byId = new Map(kept.map(m => [m.id, m]));
        this.#byClient = new Map(kept.map(m => [`${m.author.id}:${m.clientId}`, m]));
        this.#rewrite();
    }

    //Rewrites the file from the CURRENT state (edits applied, deletions redacted): the only way text really leaves the disk
    #rewrite() {
        if (!this.#file) return;
        const temporary = `${this.#file}.tmp`;
        writeFileSync(temporary, this.#messages.map(m => JSON.stringify({ t: 'msg', m })).join('\n') + (this.#messages.length ? '\n' : ''));
        renameSync(temporary, this.#file);
    }

    static #clean(body) {
        if (typeof body !== 'string' || !body.trim()) throw new ChatError('empty', 'A mensagem está vazia.');
        const text = body.replace(/\r\n/g, '\n').trim();
        if (text.length > MAX_BODY) throw new ChatError('too_long', `A mensagem passa de ${MAX_BODY} caracteres.`);
        return text;
    }

    //Returns { message, duplicate }. The same (author, clientId) twice is the same message: retries after a flaky network are safe.
    add(author, { body, clientId }) {
        if (typeof clientId !== 'string' || !clientId || clientId.length > 64) throw new ChatError('bad_request', 'clientId inválido.');
        const existing = this.#byClient.get(`${author.id}:${clientId}`);
        if (existing) return { message: existing, duplicate: true };
        const text = ChatLog.#clean(body);
        const message = { id: randomUUID(), seq: this.#seq + 1, clientId, author: { id: author.id, name: author.name }, body: text, mentions: mentionsIn(text), createdAt: new Date(this.#now()).toISOString() };
        this.#append({ t: 'msg', m: message });                // write first: if the disk refuses, nothing exists in memory either
        this.#insert(message);
        return { message, duplicate: false };
    }

    edit(id, requesterId, body) {
        const message = this.#byId.get(id);
        if (!message) throw new ChatError('not_found', 'Mensagem não encontrada.');
        if (message.author.id !== requesterId) throw new ChatError('forbidden', 'Só o autor pode editar a mensagem.');
        if (message.deletedAt) throw new ChatError('gone', 'A mensagem foi removida.');
        const text = ChatLog.#clean(body);
        if (text === message.body) return message;
        const at = new Date(this.#now()).toISOString();
        this.#append({ t: 'edit', id, body: text, at });
        message.body = text; message.mentions = mentionsIn(text); message.editedAt = at;
        return message;
    }

    //The author may delete their own; a workspace owner may delete anyone's (moderation). The text is redacted for good.
    remove(id, requesterId, { isOwner = false } = {}) {
        const message = this.#byId.get(id);
        if (!message) throw new ChatError('not_found', 'Mensagem não encontrada.');
        if (message.author.id !== requesterId && !isOwner) throw new ChatError('forbidden', 'Só o autor, ou o dono do workspace, pode remover a mensagem.');
        if (message.deletedAt) return message;
        const before = { body: message.body, mentions: message.mentions };
        message.body = ''; message.mentions = []; message.deletedAt = new Date(this.#now()).toISOString();
        try { this.#rewrite(); }                              // appending a "deleted" line would leave the original text on disk
        catch {
            Object.assign(message, before); delete message.deletedAt;
            throw new ChatError('storage', 'Não foi possível remover a mensagem agora. Tente de novo em instantes.');
        }
        return message;
    }

    //The newest `limit` messages before `beforeSeq` (or the newest of all), oldest first, and whether older ones remain
    page({ beforeSeq = Infinity, limit = 100 } = {}) {
        const eligible = this.#messages.filter(m => m.seq < beforeSeq);
        const slice = eligible.slice(-Math.max(1, Math.min(limit, 200)));
        return { messages: slice, hasMore: eligible.length > slice.length };
    }

    get size() { return this.#messages.length; }
}
