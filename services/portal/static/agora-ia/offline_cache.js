//Local copies of the domain data the user already looked at, so work can continue without a network.
//
//This is a security decision, not a convenience: whatever is stored here travels with the device. The instance sets the
//highest classification level that may be kept (`offline_cache_level`, AGORA_OFFLINE_CACHE_NIVEL on the portal) and this
//module enforces it three ways:
//  1. a response is filtered BEFORE it is stored: items above the level are dropped, and never written;
//  2. an unknown response shape is not stored at all (when in doubt, keep nothing);
//  3. lowering the level purges what was already stored above it (enforce()).
export const LEVELS = { nenhum: -1, publico: 0, interno: 1, restrito: 2, confidencial: 3 };
const MAX_ENTRIES = 300;
const levelOf = name => LEVELS[name] ?? LEVELS.confidencial;      //an unknown level counts as the most restricted

//Returns { value, level, omitted } to store, or null when nothing may be kept
export function filterForCache(data, maxLevel) {
    const max = LEVELS[maxLevel] ?? LEVELS.nenhum;
    if (max < 0 || data === null || typeof data !== 'object') return null;
    const allowed = item => levelOf(item?.classification) <= max;
    const top = items => Math.max(...items.map(item => levelOf(item.classification)), 0);

    if (Array.isArray(data.results)) {                                         // a list of entities
        const kept = data.results.filter(allowed);
        return { value: { ...data, results: kept }, level: top(kept), omitted: data.results.length - kept.length };
    }
    if (Array.isArray(data.nodes) && Array.isArray(data.edges)) {              // a graph: drop nodes above the level and their edges
        const nodes = data.nodes.filter(allowed);
        const ids = new Set(nodes.map(node => node.id));
        return { value: { ...data, nodes, edges: data.edges.filter(e => ids.has(e.from) && ids.has(e.to)) }, level: top(nodes), omitted: data.nodes.length - nodes.length };
    }
    if (data.id && data.classification !== undefined) {                       // one object, with its claims
        if (!allowed(data)) return null;
        const claims = (data.claims ?? []).filter(allowed);
        return { value: { ...data, claims }, level: Math.max(levelOf(data.classification), top(claims)), omitted: (data.claims ?? []).length - claims.length };
    }
    return null;
}

export class DomainCache {
    #backend;
    #maxLevel;

    constructor({ backend, maxLevel = 'interno' }) {
        this.#backend = backend;
        this.#maxLevel = maxLevel;
    }

    static keyFor(path, params = {}) {
        const sorted = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '').sort(([a], [b]) => a.localeCompare(b));
        return `${path}?${new URLSearchParams(sorted)}`;
    }

    async put(key, data) {
        const kept = filterForCache(data, this.#maxLevel);
        if (!kept) return false;
        await this.#backend.putCache(key, { ...kept, at: new Date().toISOString() });
        this.#trim().catch(() => {});
        return true;
    }

    async get(key) {
        const entry = await this.#backend.getCache(key);
        //Defense in depth: even if something above the level got in (older, laxer policy), it is not served
        return entry && entry.level <= (LEVELS[this.#maxLevel] ?? -1) ? entry : null;
    }

    //Removes whatever is stored above the current level (the policy may have been tightened since it was stored)
    async enforce() {
        const max = LEVELS[this.#maxLevel] ?? -1;
        let removed = 0;
        for (const [key, entry] of await this.#backend.cacheEntries()) {
            if (!(entry?.level <= max)) { await this.#backend.deleteCache(key); removed++; }
        }
        return removed;
    }

    async #trim() {
        const entries = await this.#backend.cacheEntries();
        if (entries.length <= MAX_ENTRIES) return;
        for (const [key] of entries.sort((a, b) => a[1].at.localeCompare(b[1].at)).slice(0, entries.length - MAX_ENTRIES + 30)) await this.#backend.deleteCache(key);
    }
}
