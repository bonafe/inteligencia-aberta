//Yjs-backed workspace document: the same surface as MemoryWorkspaceDoc (get/set/delete/transact/undo/
//onChange/snapshot/restore/status/text), so nothing outside collaboration/ knows Yjs exists (R-STORE-2).
//
//Layout of the Y.Doc: five top-level Y.Maps (meta, instances, layout, connections, content). Objects
//become nested Y.Maps, so two people editing different fields of the same config merge (R-CONF-1);
//arrays and primitives are stored whole; shared text is a Y.Text.
import { Y } from '../../vendor/yjs/yjs.js';
import { LocalPresence } from '../presence/presence.js';

export const ROOTS = ['meta', 'instances', 'layout', 'connections', 'content'];
const LOCAL = Symbol('local-origin');
//What each role may write, mirrored from the server (server/agora-sync/src/permissions.js): null = anything.
//The server is the authority; this makes a client that tries anyway fail loudly instead of being disconnected.
const WRITABLE_ROOTS = { owner: null, editor: null, participant: new Set(['content']), viewer: new Set() };

const split = path => (Array.isArray(path) ? path : String(path).split('.')).filter(Boolean);
const isPlainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value) && !(value instanceof Y.AbstractType);
const toJson = value => (value instanceof Y.AbstractType ? value.toJSON() : value);

export class YjsWorkspaceDoc {
    ydoc;
    presence = new LocalPresence();
    #roots = new Map();
    #listeners = new Set();
    #historyListeners = new Set();
    #statusListeners = new Set();
    #status = 'synced';
    #depth = 0;
    #undo;

    constructor(ydoc = new Y.Doc()) {
        this.ydoc = ydoc;
        for (const name of ROOTS) {
            const map = ydoc.getMap(name);
            this.#roots.set(name, map);
            map.observeDeep(events => this.#onEvents(name, events));
        }
        //Undo is per user: only changes made through this object (origin LOCAL) are tracked; each transact() is one step
        this.#undo = new Y.UndoManager([...this.#roots.values()], { trackedOrigins: new Set([LOCAL]), captureTimeout: 0 });
        for (const event of ['stack-item-added', 'stack-item-popped', 'stack-cleared']) this.#undo.on(event, () => this.#scheduleHistory());
    }

    // ---- reading ----

    get(path) {
        const keys = split(path);
        if (!keys.length) return Object.fromEntries(ROOTS.map(name => [name, this.#roots.get(name).toJSON()]));
        let node = this.#roots.get(keys[0]);
        if (!node) return undefined;
        for (const key of keys.slice(1)) {
            if (!(node instanceof Y.Map)) return undefined;
            node = node.get(key);
        }
        const value = toJson(node);
        return value === undefined ? undefined : structuredClone(value);
    }

    // ---- writing ----

    set(path, value) {
        const keys = split(path);
        if (!keys.length) throw new Error('set: empty path');
        const root = this.#roots.get(keys[0]);
        if (!root) throw new Error(`set: "${keys[0]}" is not a document root (${ROOTS.join(', ')})`);
        this.#assertMayWrite(keys[0]);
        this.#write(() => {
            if (keys.length === 1) {                                    //replacing a whole root
                for (const key of [...root.keys()]) root.delete(key);
                if (isPlainObject(value)) for (const [k, v] of Object.entries(value)) this.#put(root, k, v);
                return;
            }
            let map = root;
            for (const key of keys.slice(1, -1)) {
                let next = map.get(key);
                if (!(next instanceof Y.Map)) { next = new Y.Map(); map.set(key, next); }
                map = next;
            }
            if (value === undefined) map.delete(keys.at(-1)); else this.#put(map, keys.at(-1), value);
        });
    }

    delete(path) { this.set(path, undefined); }

    #assertMayWrite(rootName) {
        const allowed = WRITABLE_ROOTS[this.#role];
        if (allowed && !allowed.has(rootName)) throw new Error(`Your role (${this.#role}) may not change "${rootName}" in this workspace`);
    }

    #put(map, key, value) {
        if (isPlainObject(value)) {
            const child = new Y.Map();
            map.set(key, child);                                        //integrate first, then fill: children are Y types too
            for (const [k, v] of Object.entries(value)) if (v !== undefined) this.#put(child, k, v);
        } else {
            map.set(key, structuredClone(value));
        }
    }

    #write(fn) {
        if (this.#depth > 0) return fn();
        return this.transact('write', fn);
    }

    //Groups every change made in fn into one undo step
    transact(label, fn) {
        if (this.#depth > 0) return fn();
        this.#undo.stopCapturing();
        this.#depth++;
        try { return this.ydoc.transact(fn, LOCAL); }
        finally { this.#depth--; }
    }

    // ---- shared text ----

    //Collaborative text at `path` behind the SharedText surface; edits are applied as minimal diffs so that
    //simultaneous typing in different places merges instead of overwriting.
    text(path) {
        const keys = split(path);
        const leafOf = () => {
            let node = this.#roots.get(keys[0]);
            for (const key of keys.slice(1)) node = node instanceof Y.Map ? node.get(key) : undefined;
            return node;
        };
        const find = () => { const node = leafOf(); return node instanceof Y.Text ? node : null; };
        //A plain string at the path (snapshots store text that way) is promoted to a Y.Text
        const ensure = () => { this.#assertMayWrite(keys[0]); this.#write(ensureNow); };
        const ensureNow = () => {
            const leaf = leafOf();
            if (leaf instanceof Y.Text) return;
            let map = this.#roots.get(keys[0]);
            for (const key of keys.slice(1, -1)) {
                let next = map.get(key);
                if (!(next instanceof Y.Map)) { next = new Y.Map(); map.set(key, next); }
                map = next;
            }
            const ytext = new Y.Text();
            map.set(keys.at(-1), ytext);
            if (typeof leaf === 'string') ytext.insert(0, leaf);
        };
        return {
            ensure,
            getText: () => find()?.toString() ?? (typeof leafOf() === 'string' ? leafOf() : ''),
            setText: next => { this.#assertMayWrite(keys[0]); this.transact('edit text', () => {
                ensureNow();
                const ytext = find();
                const current = ytext.toString();
                if (current === next) return;
                let start = 0;
                while (start < current.length && start < next.length && current[start] === next[start]) start++;
                let endCurrent = current.length, endNext = next.length;
                while (endCurrent > start && endNext > start && current[endCurrent - 1] === next[endNext - 1]) { endCurrent--; endNext--; }
                if (endCurrent > start) ytext.delete(start, endCurrent - start);
                if (endNext > start) ytext.insert(start, next.slice(start, endNext));
            }); },
            onChange: fn => this.onChange(keys, () => fn(find()?.toString() ?? '')),
        };
    }

    // ---- history ----

    get canUndo() { return this.#undo.undoStack.length > 0; }
    get canRedo() { return this.#undo.redoStack.length > 0; }
    undo() { return this.#undo.undo() != null; }
    redo() { return this.#undo.redo() != null; }
    onHistory(fn) { this.#historyListeners.add(fn); return () => this.#historyListeners.delete(fn); }
    //Yjs reports one undo as several stack events; tell listeners once, and only if the stacks really differ
    #lastHistory = 'false/false';
    #historyQueued = false;
    #scheduleHistory() {
        if (this.#historyQueued) return;
        this.#historyQueued = true;
        queueMicrotask(() => {
            this.#historyQueued = false;
            const now = `${this.canUndo}/${this.canRedo}`;
            if (now !== this.#lastHistory) { this.#lastHistory = now; this.#historyChanged(); }
        });
    }
    #historyChanged() { for (const fn of [...this.#historyListeners]) { try { fn(); } catch (error) { console.error('[YjsWorkspaceDoc] history listener error:', error); } } }

    // ---- change and status events ----

    onChange(path, fn) {
        const entry = { keys: split(path), fn };
        this.#listeners.add(entry);
        return () => this.#listeners.delete(entry);
    }

    #onEvents(rootName, events) {
        const changed = new Set();
        for (const event of events) {
            const base = [rootName, ...event.path];
            if (event.target instanceof Y.Map) for (const key of event.keysChanged) changed.add([...base, key].join('.'));
            else changed.add(base.join('.'));
        }
        for (const changedPath of changed) {
            const changedKeys = changedPath.split('.');
            for (const { keys, fn } of [...this.#listeners]) {
                const n = Math.min(keys.length, changedKeys.length);
                if (keys.slice(0, n).every((key, i) => key === changedKeys[i])) {
                    try { fn(changedPath); } catch (error) { console.error('[YjsWorkspaceDoc] listener error:', error); }
                }
            }
        }
    }

    //The user's role in this workspace, as the host/server last said (R-PERM-4: it can change while open)
    #role = 'owner';
    #roleListeners = new Set();
    get role() { return this.#role; }
    set role(value) {
        if (value === this.#role) return;
        this.#role = value;
        for (const fn of [...this.#roleListeners]) { try { fn(value); } catch (error) { console.error('[YjsWorkspaceDoc] role listener error:', error); } }
    }
    onRole(fn) { this.#roleListeners.add(fn); return () => this.#roleListeners.delete(fn); }

    //Control messages from the server (role changes, refused writes), for the UI
    #controlListeners = new Set();
    onControl(fn) { this.#controlListeners.add(fn); return () => this.#controlListeners.delete(fn); }
    _control(message) { for (const fn of [...this.#controlListeners]) { try { fn(message); } catch (error) { console.error('[YjsWorkspaceDoc] control listener error:', error); } } }

    get status() { return this.#status; }
    set status(value) {
        if (value === this.#status) return;
        this.#status = value;
        for (const fn of [...this.#statusListeners]) { try { fn(value); } catch (error) { console.error('[YjsWorkspaceDoc] status listener error:', error); } }
    }
    onStatus(fn) { this.#statusListeners.add(fn); return () => this.#statusListeners.delete(fn); }

    // ---- snapshots ----

    snapshot() { return this.get(''); }

    //Replaces the whole content (used to import data saved by earlier versions). Not undoable.
    restore(snapshot) {
        this.ydoc.transact(() => {
            for (const name of ROOTS) {
                const root = this.#roots.get(name);
                for (const key of [...root.keys()]) root.delete(key);
                for (const [key, value] of Object.entries(snapshot?.[name] ?? {})) if (value !== undefined) this.#put(root, key, value);
            }
        }, 'restore');
        this.#undo.clear();
    }
}
