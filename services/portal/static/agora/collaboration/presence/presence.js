//Ephemeral presence (docs/especificacao/05, 5.3): who is here and what they are looking at. Never part of
//the document. Two implementations share one surface: LocalPresence (nobody else) and YjsPresence
//(backed by the Yjs awareness protocol; only collaboration/ may know that).
//
//  local state: { user: { id, name, color, kind }, focus: instanceId | null, activity: { instance, kind } | null }

const COLORS = ['#e6492d', '#2a9d8f', '#e9a100', '#8a5cf6', '#d6409f', '#2f80ed', '#3aa655', '#c26a2c'];
export const colorFor = id => COLORS[[...String(id)].reduce((sum, ch) => (sum * 31 + ch.charCodeAt(0)) >>> 0, 7) % COLORS.length];

class PresenceBase {
    #listeners = new Set();
    onPeers(fn) { this.#listeners.add(fn); return () => this.#listeners.delete(fn); }
    _notify() { for (const fn of [...this.#listeners]) { try { fn(this.peers()); } catch (error) { console.error('[presence] listener error:', error); } } }
}

export class LocalPresence extends PresenceBase {
    #local = {};
    get local() { return this.#local; }
    setLocal(patch) { this.#local = { ...this.#local, ...patch }; }
    peers() { return []; }
}

export class YjsPresence extends PresenceBase {
    #awareness;
    constructor(awareness) {
        super();
        this.#awareness = awareness;
        awareness.on('change', () => this._notify());
    }
    get local() { return this.#awareness.getLocalState() ?? {}; }
    setLocal(patch) { this.#awareness.setLocalState({ ...this.local, ...patch }); }
    //Everyone else, one entry per connected client, newest data first; states without a user are ignored
    peers() {
        return [...this.#awareness.getStates()]
            .filter(([clientId, state]) => clientId !== this.#awareness.clientID && state?.user)
            .map(([clientId, state]) => ({ clientId, ...state }));
    }
}
