//In-memory StateStore (R-STORE-3): the collaborative document API without a network.
//Used by tests and by the local, serverless mode. A Yjs-backed store implements the same surface.
//
//A document is a tree of plain JSON values addressed by dot paths ("instances.cmp-1.config.depth").
//transact() groups changes into one undoable operation; a write outside transact() is its own operation.

import { LocalPresence } from '../presence/presence.js';

const split = path => (Array.isArray(path) ? path : String(path).split('.')).filter(Boolean);

export class MemoryWorkspaceDoc {
    #root = {};
    #listeners = new Set();                 //{ path, fn }
    #undo = [];
    #redo = [];
    #current = null;                        //open transaction: { label, changes: [{ path, before, after }] }
    presence = new LocalPresence();
    role = 'owner';
    onRole() { return () => {}; }
    #status = 'synced';
    #statusListeners = new Set();

    //'offline' | 'connecting' | 'saving' | 'synced' | 'error' (R-LF-1: the UI shows it)
    get status() { return this.#status; }
    set status(value) {
        if (value === this.#status) return;
        this.#status = value;
        for (const fn of [...this.#statusListeners]) { try { fn(value); } catch (error) { console.error('[MemoryWorkspaceDoc] status listener error:', error); } }
    }
    onStatus(fn) { this.#statusListeners.add(fn); return () => this.#statusListeners.delete(fn); }

    //Shared text at `path` (plain string here; the Yjs store merges concurrent edits)
    text(path) {
        const keys = split(path);
        return {
            ensure: () => {},
            getText: () => this.get(keys) ?? '',
            setText: next => this.transact('edit text', () => this.set(keys, next)),
            onChange: fn => this.onChange(keys, () => fn(this.get(keys) ?? '')),
        };
    }

    //Fires whenever the undo/redo stacks change (a transaction closes, a bare write, undo, redo).
    #historyListeners = new Set();
    onHistory(fn) { this.#historyListeners.add(fn); return () => this.#historyListeners.delete(fn); }
    #historyChanged() { for (const fn of [...this.#historyListeners]) { try { fn(); } catch (error) { console.error('[MemoryWorkspaceDoc] history listener error:', error); } } }

    get(path) {
        let node = this.#root;
        for (const key of split(path)) {
            if (node === null || typeof node !== 'object') return undefined;
            node = node[key];
        }
        return node === undefined ? undefined : structuredClone(node);
    }

    set(path, value) {
        const keys = split(path);
        if (!keys.length) throw new Error('set: empty path');
        this.#write(keys, value === undefined ? undefined : structuredClone(value), true);
    }

    delete(path) { this.set(path, undefined); }

    #write(keys, value, record) {
        const before = this.get(keys);
        let node = this.#root;
        for (const key of keys.slice(0, -1)) {
            if (node[key] === null || typeof node[key] !== 'object') node[key] = {};
            node = node[key];
        }
        const last = keys.at(-1);
        if (value === undefined) delete node[last]; else node[last] = value;

        if (record) {
            const change = { keys, before, after: value === undefined ? undefined : structuredClone(value) };
            if (this.#current) this.#current.changes.push(change);
            else { this.#undo.push({ label: keys.join('.'), changes: [change] }); this.#redo.length = 0; this.#historyChanged(); }   //a bare write is its own operation
        }
        this.#notify(keys);
    }

    //Groups every set/delete done in fn into one undoable operation.
    transact(label, fn) {
        if (this.#current) return fn();     //nested: joins the outer operation
        this.#current = { label, changes: [] };
        try { return fn(); }
        finally {
            const { changes } = this.#current;
            this.#current = null;
            if (changes.length) { this.#undo.push({ label, changes }); this.#redo.length = 0; this.#historyChanged(); }
        }
    }

    get canUndo() { return this.#undo.length > 0; }
    get canRedo() { return this.#redo.length > 0; }

    undo() { return this.#replay(this.#undo, this.#redo, change => change.before, true); }
    redo() { return this.#replay(this.#redo, this.#undo, change => change.after, false); }

    #replay(from, to, pick, reverse) {
        const operation = from.pop();
        if (!operation) return false;
        const changes = reverse ? [...operation.changes].reverse() : operation.changes;
        for (const change of changes) {
            const value = pick(change);
            this.#write(change.keys, value === undefined ? undefined : structuredClone(value), false);
        }
        to.push(operation);
        this.#historyChanged();
        return true;
    }

    //Calls fn(path) whenever something at, under or above `path` changes. Returns an unsubscribe function.
    onChange(path, fn) {
        const entry = { keys: split(path), fn };
        this.#listeners.add(entry);
        return () => this.#listeners.delete(entry);
    }

    #notify(changedKeys) {
        for (const { keys, fn } of [...this.#listeners]) {
            const n = Math.min(keys.length, changedKeys.length);
            if (keys.slice(0, n).every((key, i) => key === changedKeys[i])) {
                try { fn(changedKeys.join('.')); } catch (error) { console.error('[MemoryWorkspaceDoc] listener error:', error); }
            }
        }
    }

    snapshot() { return structuredClone(this.#root); }

    restore(snapshot) {
        this.#root = structuredClone(snapshot);
        this.#undo.length = 0; this.#redo.length = 0;
        this.#notify([]);
        this.#historyChanged();
    }
}

export class MemoryStateStore {
    #docs = new Map();
    async open(workspaceId) {
        if (!this.#docs.has(workspaceId)) this.#docs.set(workspaceId, new MemoryWorkspaceDoc());
        return this.#docs.get(workspaceId);
    }
}
