//Starts the Ultima Agora inside the portal. Anything that goes wrong must be visible on the page, not only in the Console.
//
//Offline-first: online, the page (Django) carries the config and boot.js remembers it; offline, the service worker serves
//offline.html (no config) and boot.js uses the one it remembered. Either way the app, the catalog, the documents and
//the data already looked at are on this device.
const fatal = message => {
    const box = document.getElementById('fatal');
    box.hidden = false;
    box.textContent = `Não foi possível iniciar o Ultima Agora: ${message} (detalhes no Console, F12)`;
};
addEventListener('unhandledrejection', event => fatal(event.reason?.message ?? event.reason));

const CONFIG_KEY = 'agora.config';

function loadConfig() {
    const inline = document.getElementById('agora-config');
    if (inline) {
        const config = JSON.parse(inline.textContent);
        try { localStorage.setItem(CONFIG_KEY, JSON.stringify(config)); } catch { /* storage blocked: works online, just not offline */ }
        return config;
    }
    let remembered = null;
    try { remembered = JSON.parse(localStorage.getItem(CONFIG_KEY)); } catch { /* ignore */ }
    if (!remembered?.usuario) throw new Error('Para usar o Agora sem rede, abra-o uma vez com conexão neste navegador.');
    return remembered;
}

try {
    const config = loadConfig();
    const { ComponentRegistry } = await import('../agora/core/registry/component_registry.js');
    const { registerCoreComponents } = await import('../agora/components/catalog.js');
    const { installIaPack } = await import('./pack/pack.js');
    const { DjangoHost } = await import('./django_host.js');
    await import('../agora/ui/shell/agora-shell.js');

    const registry = new ComponentRegistry();
    await registerCoreComponents(registry);
    await installIaPack(registry);
    const host = await DjangoHost.create({ config });
    await document.querySelector('agora-shell').init({ host, registry });

    //After the app is up (does not delay it): keep the app shell available without a network
    const registerShell = () => import('../agora/core/offline/register_sw.js').then(m => m.registerOfflineShell({
        script: '/agora/sw.js', precache: '/static/agora-ia/precache.json', shell: '/static/agora-ia/offline.html',
    }));
    if (document.readyState === 'complete') registerShell(); else addEventListener('load', registerShell);
} catch (error) {
    console.error(error);
    fatal(error.message);
}
