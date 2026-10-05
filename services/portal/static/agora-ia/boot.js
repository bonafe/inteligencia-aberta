//Starts the Ultima Agora inside the portal. Anything that goes wrong must be visible on the page, not only in the Console.
const fatal = message => {
    const box = document.getElementById('fatal');
    box.hidden = false;
    box.textContent = `Não foi possível iniciar o Ultima Agora: ${message} (detalhes no Console, F12)`;
};
addEventListener('unhandledrejection', event => fatal(event.reason?.message ?? event.reason));

try {
    const config = JSON.parse(document.getElementById('agora-config').textContent);
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
} catch (error) {
    console.error(error);
    fatal(error.message);
}
