//Catalog of component definitions. The palette is built from manifests alone; component code is
//only loaded when an instance is created (R-REG-1).
import { validateManifest } from './manifest.js';
import { TypeRegistry, registerCoreTypes } from '../connections/types.js';

export class ComponentRegistry extends EventTarget {
    #definitions = new Map();       //name -> { manifest, load, component }
    types = new TypeRegistry();     //port types; applications register their own next to the core ones

    constructor() {
        super();
        registerCoreTypes(this.types);
    }

    //manifest: object; load: () => Promise<module> whose default export (or `component`) is the class
    register({ manifest, load, replaces = null }) {
        const errors = validateManifest(manifest);
        for (const section of ['inputs', 'outputs']) {
            for (const [port, spec] of Object.entries(manifest?.[section] ?? {})) {
                if (typeof spec?.type === 'string' && !this.types.has(spec.type)) errors.push(`${section}.${port}.type: unknown type "${spec.type}" (register it in registry.types first)`);
            }
        }
        if (errors.length) throw new Error(`Invalid manifest${manifest?.name ? ` "${manifest.name}"` : ''}:\n  ${errors.join('\n  ')}`);
        if (typeof load !== 'function') throw new TypeError(`register("${manifest.name}"): "load" must be a function`);

        const existing = this.#definitions.get(manifest.name);
        if (existing && existing.manifest.version !== replaces) {
            throw new Error(`Component "${manifest.name}" is already registered (version ${existing.manifest.version}); pass replaces: "${existing.manifest.version}" to override it`);
        }
        this.#definitions.set(manifest.name, { manifest: structuredClone(manifest), load, component: null });
        this.dispatchEvent(new CustomEvent('registered', { detail: { name: manifest.name } }));
    }

    //Fetches a .manifest.json and registers it; the module path in the manifest is relative to the manifest.
    async registerFromUrl(manifestUrl) {
        const url = new URL(manifestUrl, document.baseURI);
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Could not load manifest ${url}: HTTP ${response.status}`);
        const manifest = await response.json();
        this.register({ manifest, load: () => import(new URL(manifest.module, url)) });
        return manifest;
    }

    unregister(name) {
        if (this.#definitions.delete(name)) this.dispatchEvent(new CustomEvent('unregistered', { detail: { name } }));
    }

    has(name) { return this.#definitions.has(name); }

    getManifest(name) { return this.#definitions.get(name)?.manifest ?? null; }

    list({ category = null, search = '' } = {}) {
        const term = search.trim().toLowerCase();
        return [...this.#definitions.values()].map(d => d.manifest)
            .filter(m => !category || m.category === category)
            .filter(m => !term || `${m.title} ${m.description ?? ''} ${m.name}`.toLowerCase().includes(term))
            .sort((a, b) => a.title.localeCompare(b.title));
    }

    //Loads the component class on first use and defines its custom element.
    async load(name) {
        const definition = this.#definitions.get(name);
        if (!definition) throw new Error(`Component "${name}" is not registered`);
        if (!definition.component) {
            const module = await definition.load();
            const component = module.default ?? module.component;
            if (typeof component !== 'function') throw new Error(`Component "${name}": the loaded module has no default export or "component" class`);
            if (!customElements.get(definition.manifest.tag)) customElements.define(definition.manifest.tag, component);
            definition.component = component;
        }
        return definition.component;
    }

    //Components with an input port that accepts values of `type` (subtypes and one conversion allowed).
    typesAccepting(type) {
        return this.list().filter(m => Object.values(m.inputs ?? {}).some(port => this.types.compatibility(type, port.type).ok));
    }

    typesProducing(type) {
        return this.list().filter(m => Object.values(m.outputs ?? {}).some(port => this.types.compatibility(port.type, type).ok));
    }
}
