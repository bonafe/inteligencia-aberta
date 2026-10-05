import { ComponentRegistry } from '../../agora/core/registry/component_registry.js';
import { registerCoreComponents } from '../../agora/components/catalog.js';
import { Workspace } from '../../agora/core/workspace/workspace.js';
import { MemoryStateStore } from '../../agora/collaboration/document/memory_state_store.js';
import '../../agora/ui/workspace/agora-workspace.js';
import { installIaPack } from '../pack/pack.js';
import { api, domainClient, DjangoHost, ApiError, OfflineError } from '../django_host.js';
import { DomainCache, filterForCache } from '../offline_cache.js';
import { MemoryBackend } from '../../agora/collaboration/providers/indexeddb_persistence.js';

//Zero-dependency runner (same shape as the Agora's own): each test throws on failure.
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const assert = (condition, message = 'assertion failed') => { if (!condition) throw new Error(message); };
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const settleOrTimeout = (promise, ms = 2000) => Promise.race([promise.then(() => true), wait(ms).then(() => false)]);

// ---- a fake portal: records requests, answers from a table ----

const realFetch = window.fetch;
const portal = (routes) => {
    const calls = [];
    window.fetch = async (url, init = {}) => {
        const path = String(url);
        calls.push({ path, method: init.method ?? 'GET', headers: init.headers ?? {}, body: init.body });
        //Longest matching prefix wins; anything that is not the fake portal (manifests, templates) goes to the real network
        const route = Object.entries(routes).filter(([prefix]) => path.startsWith(prefix)).sort((a, b) => b[0].length - a[0].length)[0];
        if (!route) return realFetch(url, init);
        const [status, body] = typeof route[1] === 'function' ? route[1](path, init) : route[1];
        return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
    };
    return calls;
};
const restore = () => { window.fetch = realFetch; };
//The network is gone: requests to the fake portal fail the way a real fetch does when it cannot connect
const cutNetwork = (...prefixes) => {
    const before = window.fetch;
    window.fetch = (url, init) => (prefixes.some(prefix => String(url).startsWith(prefix)) ? Promise.reject(new TypeError('Failed to fetch')) : before(url, init));
};
const guard = fn => async () => { try { await fn(); } finally { restore(); } };

const ENTITIES = [
    { id: 'a1', kind: 'empresa', label: 'Empresa Alfa', classification: 'restrito', info_type: 'fato' },
    { id: 'p1', kind: 'pessoa', label: 'Pessoa A', classification: 'confidencial', info_type: 'fato', above_workspace: true },
];

// ---- host adapter ----

test('api: GET carries no CSRF header, writes carry the cookie value', guard(async () => {
    document.cookie = 'csrftoken=abc%20123; path=/';
    const calls = portal({ '/agora/api/v1/': [200, { ok: true }] });
    await api('/agora/api/v1', '/workspaces/');
    await api('/agora/api/v1', '/workspaces/', { method: 'POST', body: { title: 'x' } });
    assert(!('X-CSRFToken' in calls[0].headers), 'GET must not send the token');
    assert(calls[1].headers['X-CSRFToken'] === 'abc 123' && calls[1].headers['Content-Type'] === 'application/json');
    assert(calls[1].body === '{"title":"x"}');
}));

test('api: errors become ApiError with the server message; empty params are not sent', guard(async () => {
    const calls = portal({ '/agora/api/v1/boom': [403, { error: 'Seu papel não permite' }], '/agora/api/v1/ok': [200, {}] });
    try { await api('/agora/api/v1', '/boom'); throw new Error('accepted'); }
    catch (error) { assert(error instanceof ApiError && error.status === 403 && error.message === 'Seu papel não permite', error.message); }
    await api('/agora/api/v1', '/ok', { params: { q: 'x', vazio: '', nada: null, workspace: undefined } });
    assert(calls.at(-1).path === '/agora/api/v1/ok?q=x', calls.at(-1).path);
}));

test('domain client is scoped to a workspace without the component knowing', guard(async () => {
    const calls = portal({ '/agora/api/v1/dominio/': [200, { results: [] }] });
    const client = domainClient('/agora/api/v1').forWorkspace('w-1');
    await client.get('/artefatos/', { q: 'alfa' });
    assert(calls[0].path === '/agora/api/v1/dominio/artefatos/?q=alfa&workspace=w-1', calls[0].path);
}));

const hostConfig = { api: '/agora/api/v1', sync_url: '', usuario: { id: '7', name: 'Maria' } };

test('django host: catalog maps the API, 404 means "no access", and sync is off without a URL', guard(async () => {
    portal({
        '/agora/api/v1/workspaces/w1/': [200, { id: 'w1', title: 'Alfa', role: 'participant', owner: '1', classification: 'restrito', updated_at: 'x', archived_at: null }],
        '/agora/api/v1/workspaces/nao-existe/': [404, { error: 'Workspace não encontrado' }],
        '/agora/api/v1/workspaces/': [200, { workspaces: [{ id: 'w1', title: 'Alfa', role: 'editor', owner: '1', classification: 'restrito', updated_at: 'x', archived_at: null }] }],
    });
    const host = new DjangoHost({ config: hostConfig, backend: new MemoryBackend() });
    assert(host.identity.id === '7' && host.syncEnabled === false && host.persistent === false);
    assert((await host.get('w1')).role === 'participant' && (await host.get('w1')).ownerId === '1');
    assert((await host.list())[0].title === 'Alfa');
    assert(await host.get('nao-existe') === null, '404 must read as "does not exist or no access"');
}));

test('django host: the sync session asks the portal for a token; the document opens with the role the portal gave', guard(async () => {
    const calls = portal({ '/agora/api/v1/workspaces/w1/token/': [200, { token: 'T', role: 'viewer', expires_in: 300 }] });
    const host = new DjangoHost({ config: { ...hostConfig, sync_url: 'ws://127.0.0.1:1' }, backend: new MemoryBackend() });
    assert(host.syncEnabled);
    const doc = await host.openDocument({ id: 'w1', role: 'viewer' });
    assert(doc.role === 'viewer', 'the catalog role is used until the server answers');
    await wait(150);
    assert(calls.some(c => c.path.endsWith('/workspaces/w1/token/') && c.method === 'POST'), 'it must ask for a token to connect');
    doc.provider.destroy();
}));

// ---- the pack ----

const newRegistry = async () => {
    const registry = new ComponentRegistry();
    await registerCoreComponents(registry);
    await installIaPack(registry);
    return registry;
};

test('pack: installs its types, converters and components', async () => {
    const registry = await newRegistry();
    assert(registry.has('ia-search') && registry.has('ia-entity') && registry.types.has('Entity'));
    const t = registry.types;
    assert(t.validate('Entity', { id: 'a' }) === null && t.validate('Entity', { label: 'x' }) !== null);
    assert(t.compatibility('Entity', 'String').ok && t.compatibility('Entity[]', 'Table').ok && t.compatibility('Row', 'Entity').ok);
    assert(!t.compatibility('Entity[]', 'Graph').ok);
    const table = t.compatibility('Entity[]', 'Table').convert(ENTITIES);
    assert(table.rows.length === 2 && table.rows[0].label === 'Empresa Alfa' && table.columns.length === 4);
    assert(t.validate('Table', table) === null);
    assert(t.compatibility('Row', 'Entity').convert(table.rows[1]).id === 'p1', 'a row from an entity table becomes an Entity again');
});

test('pack: components that ask for the domain capability declare it, and the generic core stays domain-free', async () => {
    const registry = await newRegistry();
    for (const name of ['ia-search', 'ia-entity']) assert(registry.getManifest(name).capabilities.includes('domain-data'), name);
    for (const manifest of registry.list().filter(m => m.name.startsWith('ultima-'))) {
        assert(!JSON.stringify(manifest).includes('Entity'), `${manifest.name} mentions a domain type`);
    }
});

// ---- the components against a fake domain ----

const DETAIL = {
    id: 'a1', kind: 'empresa', label: 'Empresa Alfa', classification: 'restrito', info_type: 'fato',
    claims: [{ predicate: 'schema:name', object: 'Empresa Alfa Ltda', producer: 'extruct', confidence: 0.7, state: 'ativa', classification: 'restrito' }],
    sources: [{ origem: 'Receita Federal', confianca: 0.95 }],
};
const GRAPH = { nodes: [{ id: 'a1', label: 'Empresa Alfa' }, { id: 'p1', label: 'Pessoa A' }], edges: [{ from: 'a1', to: 'p1', label: 'vínculo' }] };

const mountWorkspace = async (domainRoutes) => {
    const calls = portal(domainRoutes);
    const registry = await newRegistry();
    const doc = await new MemoryStateStore().open('w1');
    const workspace = new Workspace({ doc, registry, user: { id: 'u' }, domain: domainClient('/agora/api/v1'), id: 'w1' });
    const view = document.createElement('agora-workspace');
    view.style.cssText = 'display:block;width:700px;height:400px';
    document.body.append(view);
    assert(await settleOrTimeout(view.whenLoaded()), 'view never loaded');
    view.workspace = workspace;
    return { calls, registry, workspace, view };
};
const el = (view, id) => view.rootNode.querySelector(`[data-instance-id="${id}"]`);

const DOMAIN = {
    '/agora/api/v1/dominio/artefatos/a1/relacoes/': [200, GRAPH],
    '/agora/api/v1/dominio/artefatos/a1/': [200, DETAIL],
    '/agora/api/v1/dominio/artefatos/zz/': [404, { error: 'Objeto não encontrado' }],
    '/agora/api/v1/dominio/artefatos/': (path) => [200, { results: path.includes('q=alfa') ? [ENTITIES[0]] : ENTITIES }],
};

test('ia-search: searches through the host client, scoped to the workspace, and lists what it found', guard(async () => {
    const { calls, workspace, view } = await mountWorkspace(DOMAIN);
    const id = workspace.addInstance('ia-search', { config: { query: 'alfa' } });
    await wait(700);
    assert(calls.some(c => c.path.includes('/dominio/artefatos/?q=alfa') && c.path.includes('workspace=w1')), JSON.stringify(calls.map(c => c.path)));
    const items = [...el(view, id).rootNode.querySelectorAll('li button')];
    assert(items.length === 1 && items[0].textContent.includes('Empresa Alfa') && items[0].textContent.includes('restrito'));
    assert(workspace.bus.outputValue(id, 'results').length === 1, 'the results output carries the entities');
}));

test('ia-search → ia-entity: choosing a result feeds the entity component through a typed connection', guard(async () => {
    const { workspace, view } = await mountWorkspace(DOMAIN);
    const search = workspace.addInstance('ia-search', { config: { query: '' } });
    await wait(400);
    const entity = workspace.addInstance('ia-entity', { target: { panelId: workspace.layout.findPanelOf(search), side: 'right' } });
    await wait(500);
    workspace.bus.connect({ instance: search, port: 'selected' }, { instance: entity, port: 'entity' });
    [...el(view, search).rootNode.querySelectorAll('li button')][0].click();
    await wait(600);
    const root = el(view, entity).rootNode;
    assert(root.querySelector('#label').textContent === 'Empresa Alfa');
    assert(root.querySelector('#claims').textContent.includes('extruct') && root.querySelector('#claims').textContent.includes('70%'), 'claims show producer and confidence');
    assert(root.querySelector('#sources').textContent.includes('Receita Federal'));
    assert(root.querySelector('#warning').hasAttribute('hidden'), 'not above the workspace: no warning');
}));

test('ia-search results can feed the generic table through the Entity[] → Table converter', guard(async () => {
    const { workspace, view } = await mountWorkspace(DOMAIN);
    const search = workspace.addInstance('ia-search', { config: { query: '' } });
    await wait(400);
    const table = workspace.addInstance('ultima-table', { target: { panelId: workspace.layout.findPanelOf(search), side: 'bottom' } });
    await wait(400);
    workspace.bus.connect({ instance: search, port: 'results' }, { instance: table, port: 'rows' });
    await wait(300);
    const rows = el(view, table).rootNode.querySelectorAll('tbody tr');
    assert(rows.length === 2 && rows[0].textContent.includes('Empresa Alfa'), `table shows ${rows.length} rows`);
}));

test('ia-entity: the chosen entity is pinned in its config, so another person opening the workspace sees it', guard(async () => {
    const { workspace, view } = await mountWorkspace(DOMAIN);
    const search = workspace.addInstance('ia-search');
    await wait(400);
    const entity = workspace.addInstance('ia-entity', { target: { panelId: workspace.layout.findPanelOf(search), side: 'right' } });
    await wait(500);
    workspace.bus.connect({ instance: search, port: 'selected' }, { instance: entity, port: 'entity' });
    [...el(view, search).rootNode.querySelectorAll('li button')][0].click();
    await wait(500);
    assert(workspace.instance(entity).config.entity === 'a1', 'the selection must be saved in the shared document');

    //A second person: same document, nothing connected, no selection of their own
    const other = document.createElement('agora-workspace');
    other.style.cssText = 'display:block;width:700px;height:300px';
    document.body.append(other);
    assert(await settleOrTimeout(other.whenLoaded()));
    other.workspace = new Workspace({ doc: workspace.doc, registry: workspace.registry, user: { id: 'bob' }, domain: domainClient('/agora/api/v1'), id: 'w1' });
    await wait(800);
    assert(other.rootNode.querySelector('ia-entity').rootNode.querySelector('#label').textContent === 'Empresa Alfa');
}));

test('ia-entity: showing relations creates a graph, linked, in one undo step (the action of the spec)', guard(async () => {
    const { workspace, view } = await mountWorkspace(DOMAIN);
    const entity = workspace.addInstance('ia-entity', { config: { entity: 'a1' } });
    await wait(700);
    const { suggestions, port } = await workspace.invokeAction(entity, 'showRelations');
    assert(suggestions[0].component === 'ultima-graph' && port === 'relations');
    const graph = workspace.createLinked(entity, port, suggestions[0]);
    await wait(600);
    const nodes = el(view, graph).rootNode.querySelectorAll('.node');
    assert(nodes.length === 2, `graph shows ${nodes.length} nodes`);
    workspace.doc.undo();
    assert(workspace.instances().length === 1 && workspace.bus.list().length === 0);
}));

test('ia-entity: an object the user cannot see reads as "no access", and says nothing about the object', guard(async () => {
    const { workspace, view } = await mountWorkspace(DOMAIN);
    const entity = workspace.addInstance('ia-entity', { config: { entity: 'zz' } });
    await wait(600);
    const root = el(view, entity).rootNode;
    assert(root.querySelector('#error').textContent.includes('Sem acesso') && root.querySelector('#card').hasAttribute('hidden'));
    assert(!root.querySelector('#error').hasAttribute('hidden'));
}));

test('ia-entity: an entity more restricted than the workspace is flagged as reference-only', guard(async () => {
    const above = { ...DETAIL, above_workspace: true };
    const { workspace, view } = await mountWorkspace({ ...DOMAIN, '/agora/api/v1/dominio/artefatos/a1/': [200, above] });
    const entity = workspace.addInstance('ia-entity', { config: { entity: 'a1' } });
    await wait(600);
    assert(!el(view, entity).rootNode.querySelector('#warning').hasAttribute('hidden'));
}));

test('ia-search: a failing portal is reported on the component, not swallowed', guard(async () => {
    const { workspace, view } = await mountWorkspace({ '/agora/api/v1/dominio/artefatos/': [500, { error: 'banco fora do ar' }] });
    const id = workspace.addInstance('ia-search', { config: { query: 'x' } });
    await wait(600);
    const status = el(view, id).rootNode.querySelector('#status');
    assert(status.classList.contains('error') && status.textContent.includes('banco fora do ar'), status.textContent);
}));

test('ia-search: a viewer can search even though they cannot save the query', guard(async () => {
    const { workspace, view } = await mountWorkspace(DOMAIN);
    const id = workspace.addInstance('ia-search');
    await wait(400);
    workspace.doc.role = 'viewer';
    const component = el(view, id);
    component.rootNode.querySelector('#query').value = 'alfa';
    component.rootNode.querySelector('#form').dispatchEvent(new Event('submit', { cancelable: true }));
    await wait(500);
    assert(component.rootNode.querySelectorAll('li button').length === 1, 'the search must still run');
    assert(!workspace.instance(id).config.query, 'and nothing may be written');
}));


// ---- offline-first: what may be kept on the device ----

const LIST = { results: [
    { id: 'a1', kind: 'empresa', label: 'Alfa', classification: 'interno', info_type: 'fato' },
    { id: 'a2', kind: 'empresa', label: 'Beta', classification: 'restrito', info_type: 'fato' },
    { id: 'p1', kind: 'pessoa', label: 'Sigilosa', classification: 'confidencial', info_type: 'fato' },
] };

test('offline policy: items above the level are dropped BEFORE being stored, and counted', () => {
    const kept = filterForCache(LIST, 'restrito');
    assert(kept.value.results.map(r => r.id).join() === 'a1,a2' && kept.omitted === 1 && kept.level === 2);
    assert(filterForCache(LIST, 'interno').value.results.map(r => r.id).join() === 'a1');
    assert(filterForCache(LIST, 'confidencial').value.results.length === 3);
    assert(filterForCache(LIST, 'nenhum') === null, 'policy "nenhum" keeps nothing');
    assert(filterForCache(LIST, 'valor-desconhecido') === null, 'an unknown policy keeps nothing');
});

test('offline policy: an object above the level is not kept at all; its claims are filtered; unknown shapes are never kept', () => {
    const detail = { id: 'a1', classification: 'interno', claims: [{ predicate: 'x', classification: 'interno' }, { predicate: 'y', classification: 'confidencial' }], sources: [] };
    const kept = filterForCache(detail, 'restrito');
    assert(kept.value.claims.length === 1 && kept.omitted === 1);
    assert(filterForCache({ ...detail, classification: 'confidencial' }, 'restrito') === null);
    assert(filterForCache({ qualquer: 'coisa' }, 'confidencial') === null && filterForCache('texto', 'confidencial') === null && filterForCache(null, 'confidencial') === null);
});

test('offline policy: a graph loses the nodes above the level and the edges that touched them', () => {
    const graph = { nodes: [{ id: 'a', label: 'A', classification: 'interno' }, { id: 'b', label: 'B', classification: 'confidencial' }, { id: 'c', label: 'C', classification: 'interno' }],
        edges: [{ from: 'a', to: 'b' }, { from: 'a', to: 'c' }] };
    const kept = filterForCache(graph, 'restrito');
    assert(kept.value.nodes.map(n => n.id).join() === 'a,c' && kept.value.edges.length === 1 && kept.value.edges[0].to === 'c' && kept.omitted === 1);
});

test('domain cache: stores within the policy, refuses what is above it, and purges when the policy is tightened', async () => {
    const backend = new MemoryBackend();
    const lax = new DomainCache({ backend, maxLevel: 'confidencial' });
    await lax.put('k-list', LIST);
    await lax.put('k-detail', { id: 'p1', classification: 'confidencial', claims: [] });
    assert((await backend.cacheEntries()).length === 2);

    const strict = new DomainCache({ backend, maxLevel: 'interno' });
    assert(await strict.get('k-detail') === null, 'above the level: not served even though it is physically there');
    assert((await strict.get('k-list')) === null, 'the list entry holds a "confidencial" item, so its level is above');
    assert(await strict.enforce() === 2 && (await backend.cacheEntries()).length === 0, 'tightening the policy deletes what is stored above it');
});

test('domain cache: the least-recently stored entries go when it grows too large', async () => {
    const backend = new MemoryBackend();
    const cache = new DomainCache({ backend, maxLevel: 'interno' });
    for (let i = 0; i < 320; i++) await cache.put(`k${i}`, { results: [{ id: String(i), classification: 'publico' }] });
    await wait(300);
    const count = (await backend.cacheEntries()).length;
    assert(count <= 300 && count > 200, `kept ${count}`);
    assert(await cache.get('k319') !== null && await cache.get('k0') === null, 'the newest stays, the oldest goes');
});

test('domain client: reads online fill the cache; offline it answers from the copy, marked as offline', guard(async () => {
    portal({ '/agora/api/v1/dominio/artefatos/': [200, LIST] });
    const cache = new DomainCache({ backend: new MemoryBackend(), maxLevel: 'restrito' });
    const client = domainClient('/agora/api/v1', 'w1', cache);
    const online = await client.get('/artefatos/', { q: 'a' });
    assert(online.results.length === 3 && !online.offline);
    await wait(50);

    cutNetwork('/agora/api/v1/');
    const offline = await client.get('/artefatos/', { q: 'a' });
    assert(offline.offline === true && offline.results.map(r => r.id).join() === 'a1,a2', 'the confidential item was never stored');
    assert(offline.omitted === 1 && offline.cached_at);
    try { await client.get('/artefatos/', { q: 'nunca-consultado' }); throw new Error('accepted'); }
    catch (error) { assert(error instanceof OfflineError && error.status === 0 && error.message.includes('sem cópia local'), error.message); }
}));

test('domain client: a refusal from the server (403/404) is never answered from the cache', guard(async () => {
    const cache = new DomainCache({ backend: new MemoryBackend(), maxLevel: 'confidencial' });
    portal({ '/agora/api/v1/dominio/artefatos/a1/': [200, { id: 'a1', classification: 'interno', claims: [] }] });
    const client = domainClient('/agora/api/v1', null, cache);
    await client.get('/artefatos/a1/');
    await wait(50);
    restore();
    portal({ '/agora/api/v1/dominio/artefatos/a1/': [404, { error: 'Objeto não encontrado' }] });      // access was revoked
    try { await client.get('/artefatos/a1/'); throw new Error('served from cache'); }
    catch (error) { assert(error.status === 404, `got ${error.status}: ${error.message}`); }
}));

// ---- offline-first: the catalog ----

const metaFromServer = (id, extra = {}) => ({ id, title: `WS ${id}`, role: 'owner', owner: '7', classification: 'restrito', updated_at: new Date().toISOString(), archived_at: null, ...extra });

test('catalog offline: after one online visit, the list and each workspace are available without the server', guard(async () => {
    portal({
        '/agora/api/v1/workspaces/w2/': [200, metaFromServer('w2', { role: 'participant' })],
        '/agora/api/v1/workspaces/w1/': [200, metaFromServer('w1')],
        '/agora/api/v1/workspaces/': [200, { workspaces: [metaFromServer('w1'), metaFromServer('w2', { role: 'participant' })] }],
    });
    const host = new DjangoHost({ config: hostConfig, backend: new MemoryBackend() });
    assert((await host.list()).length === 2);
    cutNetwork('/agora/api/v1/');
    assert((await host.list()).map(m => m.id).sort().join() === 'w1,w2', 'the list comes from the local copy');
    assert((await host.get('w2')).role === 'participant', 'the last known role is kept');
    assert(await host.get('desconhecido') === null);
    const doc = await host.openDocument(await host.get('w1'));
    doc.set('meta.title', 'editado offline');
    assert(doc.get('meta.title') === 'editado offline', 'documents keep working');
}));

test('catalog offline: creating works, gets its identity at once, and reaches the server (same id) when the network returns', guard(async () => {
    const calls = portal({ '/agora/api/v1/workspaces/': [201, metaFromServer('x')] });
    cutNetwork('/agora/api/v1/');
    const host = new DjangoHost({ config: hostConfig, backend: new MemoryBackend() });
    const meta = await host.create('Criado no avião');
    assert(meta.pending === true && meta.role === 'owner' && /^[0-9a-f-]{36}$/.test(meta.id));
    assert((await host.list()).some(m => m.id === meta.id), 'it shows up in the list immediately');
    assert(await host.pendingCount() === 1);
    const doc = await host.openDocument(meta);
    doc.set('meta.title', 'Criado no avião');

    restore();
    const sent = [];
    window.fetch = async (url, init = {}) => {
        if (String(url).startsWith('/agora/api/v1/workspaces/')) {
            sent.push({ method: init.method, body: JSON.parse(init.body ?? '{}') });
            return new Response(JSON.stringify(metaFromServer(meta.id, { title: 'Criado no avião' })), { status: 201 });
        }
        return realFetch(url, init);
    };
    assert(await host.syncPending() === 1);
    assert(sent[0].method === 'POST' && sent[0].body.id === meta.id && sent[0].body.title === 'Criado no avião', 'the server receives the id chosen offline');
    assert(await host.pendingCount() === 0 && !(await host.get(meta.id)).pending);
    assert((await host.openDocument(meta)).get('meta.title') === 'Criado no avião', 'the document made offline is the same one');
}));

test('catalog offline: renaming and archiving are applied locally at once and sent later; the server refusing does not loop', guard(async () => {
    portal({ '/agora/api/v1/workspaces/': [200, { workspaces: [metaFromServer('w1')] }], '/agora/api/v1/workspaces/w1/': [200, metaFromServer('w1')] });
    const host = new DjangoHost({ config: hostConfig, backend: new MemoryBackend() });
    await host.list();
    cutNetwork('/agora/api/v1/');
    await host.rename('w1', 'Novo nome offline');
    assert((await host.get('w1')).title === 'Novo nome offline' && await host.pendingCount() === 1);
    await host.archive('w1');
    assert((await host.list()).length === 0, 'archived locally right away');

    restore();
    const calls = portal({ '/agora/api/v1/workspaces/w1/archive/': [200, {}], '/agora/api/v1/workspaces/w1/': [200, {}] });
    await host.syncPending();
    assert(calls.some(c => c.method === 'PATCH' && JSON.parse(c.body).title === 'Novo nome offline') && calls.some(c => c.path.endsWith('/archive/')));
    assert(await host.pendingCount() === 0);

    //a pending workspace the server refuses (e.g. role changed meanwhile) stays, and is not retried in a loop within one run
    cutNetwork('/agora/api/v1/');
    const orphan = await host.create('Recusado');
    restore();
    const refused = portal({ [`/agora/api/v1/workspaces/${orphan.id}/`]: [404, { error: 'não existe (ainda)' }], '/agora/api/v1/workspaces/': [403, { error: 'Você não pode criar workspaces nesta organização.' }] });
    assert(await host.syncPending() === 0 && refused.filter(c => c.method === 'POST').length === 1);
    assert((await host.get(orphan.id)).pending === true, 'kept, not lost');
}));

test('catalog: what the server stops listing is hidden locally; revoked access is not served from the copy', guard(async () => {
    portal({ '/agora/api/v1/workspaces/': [200, { workspaces: [metaFromServer('w1'), metaFromServer('w2')] }] });
    const host = new DjangoHost({ config: hostConfig, backend: new MemoryBackend() });
    assert((await host.list()).length === 2);
    restore();
    portal({ '/agora/api/v1/workspaces/w2/': [404, { error: 'Workspace não encontrado' }], '/agora/api/v1/workspaces/': [200, { workspaces: [metaFromServer('w1')] }] });
    assert((await host.list()).map(m => m.id).join() === 'w1', 'w2 disappeared from the server list');
    assert(await host.get('w2') === null, 'a 404 while online is not answered from the local copy');
}));

// ---- offline-first: the components say so ----

test('ia-search offline: shows the local copy and says it is a copy, and what was left out', guard(async () => {
    const { workspace, view } = await mountWorkspace(DOMAIN);
    const cache = new DomainCache({ backend: new MemoryBackend(), maxLevel: 'restrito' });
    workspace.domain.forWorkspace = null;
    const client = domainClient('/agora/api/v1', 'w1', cache);
    const w2 = new Workspace({ doc: await new MemoryStateStore().open('w-off'), registry: workspace.registry, user: { id: 'u' }, domain: client, id: 'w1' });
    const other = document.createElement('agora-workspace');
    other.style.cssText = 'display:block;width:700px;height:300px';
    document.body.append(other);
    assert(await settleOrTimeout(other.whenLoaded()));
    other.workspace = w2;
    const id = w2.addInstance('ia-search', { config: { query: 'alfa' } });
    await wait(700);
    const status = () => other.rootNode.querySelector(`[data-instance-id="${id}"]`).rootNode.querySelector('#status').textContent;
    assert(!status().includes('sem conexão'), status());

    cutNetwork('/agora/api/v1/');
    const component = other.rootNode.querySelector(`[data-instance-id="${id}"]`);
    component.rootNode.querySelector('#form').dispatchEvent(new Event('submit', { cancelable: true }));
    await wait(600);
    assert(status().includes('sem conexão: cópia local'), status());
    assert(component.rootNode.querySelectorAll('li button').length === 1);
}));

test('ia-search offline without a copy: a clear message, not a generic failure', guard(async () => {
    const { workspace, view } = await mountWorkspace({});
    cutNetwork('/agora/api/v1/');
    const id = workspace.addInstance('ia-search', { config: { query: 'nunca' } });
    await wait(600);
    const status = el(view, id).rootNode.querySelector('#status');
    assert(status.classList.contains('error') && status.textContent === 'Sem conexão e sem cópia local deste dado.', status.textContent);
}));

// ---- offline-first: the app shell list ----

test('precache: the list covers the app, the pack and the offline page, and is served', async () => {
    const list = await (await fetch('../precache.json')).json();
    assert(list.version && list.files.length > 40);
    for (const required of ['/static/agora-ia/offline.html', '/static/agora-ia/boot.js', '/static/agora-ia/django_host.js', '/static/agora-ia/offline_cache.js',
        '/static/agora-ia/pack/ia-search/ia-search.js', '/static/agora-ia/pack/ia-search/ia-search.html', '/static/agora-ia/pack/ia-entity/ia-entity.manifest.json',
        '/static/agora/core/components/agora_component.js', '/static/agora/vendor/yjs/yjs.js', '/static/agora/ui/shell/agora-shell.html']) {
        assert(list.files.includes(required), `missing ${required}`);
    }
    assert(!list.files.some(f => f.includes('/tests/')), 'tests do not belong in the app cache');
    for (const file of list.files) assert((await fetch(file.replace(/^\/static/, ''), { method: 'HEAD' })).ok, `${file} is listed but not served`);
});

// ---- runner ----

const originalConsoleLog = console.log;
const originalConsoleError = console.error;
console.log = () => {};
console.error = () => {};

const lines = [];
let failed = 0;
for (const { name, fn } of tests) {
    try { await fn(); lines.push(`PASS ${name}`); }
    catch (error) { failed++; lines.push(`FAIL ${name}\n     ${error.message}`); }
    finally { restore(); }
}
console.log = originalConsoleLog;
console.error = originalConsoleError;

const summary = `${tests.length - failed}/${tests.length} passed`;
document.getElementById('results').textContent = lines.join('\n');
document.getElementById('summary').textContent = summary;
const failures = lines.filter(l => l.startsWith('FAIL ')).map(l => l.slice(5).replace(/\n\s+/, ' — '));
document.title = (failed ? 'FAIL ' : 'PASS ') + summary + (failed ? ` | ${failures.join(' || ')}` : '');
