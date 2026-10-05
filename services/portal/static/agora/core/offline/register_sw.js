//Registers the offline app shell (sw.js). The browser only updates a service worker when its script changes, and
//a new app version changes precache.json, not sw.js — so the cache version goes in the registration URL: a new
//version is a new URL, which makes the browser install the new worker (and its new cache).
//
//Offline (the list cannot be fetched) nothing is registered or replaced: what is installed keeps working.
//Needs https or localhost; anywhere else it quietly does nothing and the app just works online.
export async function registerOfflineShell({ script = 'sw.js', precache = 'precache.json', shell = null } = {}) {
    if (!('serviceWorker' in navigator)) return null;
    try {
        const list = await (await fetch(precache, { cache: 'no-cache' })).json();
        const query = new URLSearchParams({ precache, v: list.version });
        if (shell) query.set('shell', shell);
        return await navigator.serviceWorker.register(`${script}?${query}`);
    } catch {
        return (await navigator.serviceWorker.getRegistration().catch(() => null)) ?? null;
    }
}
