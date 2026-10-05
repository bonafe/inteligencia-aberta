//Base class of every Agora catalog component: ReactiveComponent plus the instance contract
//(docs/especificacao/03-componentes-e-contrato.md, 3.6). Capabilities are granted by the shell from
//the manifest; a component that did not declare one gets a clear error when it touches it (R-CAP-1).
import { ReactiveComponent } from '../../vendor/ultima/reactive_component.js';
import { mix } from './mix.js';

//Services the shell injects through attach(). Each one backs a capability.
const SERVICE_CAPABILITY = { ports: 'connectable', shared: 'shared-content', presence: 'presence-aware', domain: 'domain-data' };

export const AgoraInstance = (Base) => class extends Base {
    #instanceId = null;
    #manifest = null;
    #config = {};
    #services = {};
    #attached = false;
    #pendingInputs = new Map();     //port -> last value received before onLoad() (R-LIFE-1)

    //Called by the shell right after the element is created and before it finishes loading.
    //services: { setConfig(patch), emit(port, value), ports, shared, presence, domain }
    attach({ instanceId, manifest, config = {}, services = {} }) {
        if (this.#attached) throw new Error(`[${this.constructor.name}] attach() called twice`);
        this.#instanceId = instanceId;
        this.#manifest = manifest;
        this.#config = Object.freeze({ ...this.#defaults(manifest), ...config });
        this.#services = services;
        this.#attached = true;
        this.dataset.instanceId = instanceId;
    }

    #defaults(manifest) {
        return Object.fromEntries(Object.entries(manifest.config ?? {})
            .filter(([, spec]) => spec.default !== undefined).map(([name, spec]) => [name, spec.default]));
    }

    #requireAttached() {
        if (!this.#attached) throw new Error(`[${this.constructor.name}] used before attach(): create it through the shell/registry`);
    }

    #requireCapability(service) {
        this.#requireAttached();
        const capability = SERVICE_CAPABILITY[service];
        if (!this.#manifest.capabilities?.includes(capability)) {
            throw new Error(`[${this.#manifest.name}] "this.${service}" needs the "${capability}" capability, which its manifest does not declare`);
        }
        return this.#services[service];
    }

    get instanceId() { return this.#instanceId; }
    get manifest() { return this.#manifest; }
    get config() { this.#requireAttached(); return this.#config; }
    get ports() { return this.#requireCapability('ports'); }
    get shared() { return this.#requireCapability('shared'); }
    get presence() { return this.#requireCapability('presence'); }
    get domain() { return this.#requireCapability('domain'); }

    //The shell calls this when the document's config for this instance changes (including our own setConfig).
    applyConfig(config) {
        this.#config = Object.freeze({ ...this.#defaults(this.#manifest), ...config });
        this.onConfig(this.#config);
    }

    setConfig(patch) {
        this.#requireAttached();
        this.#services.setConfig?.(patch);
    }

    //Publishes a value on an output port declared in the manifest.
    emit(port, value) {
        this.#requireCapability('ports');
        if (!this.#manifest.outputs?.[port]) {
            console.error(`[${this.#manifest.name}] emit("${port}"): not an output port in the manifest`);
            return;
        }
        this.#services.emit?.(port, value);
    }

    //Entry point used by the bus. Values arriving before onLoad() are queued, keeping the last per port.
    deliverInput(port, value) {
        if (!this.loaded) { this.#pendingInputs.set(port, value); return; }
        this.#safely(() => this.onInput(port, value), `onInput("${port}")`);
    }

    invokeAction(name, args) {
        if (!this.#manifest.actions?.[name]) throw new Error(`[${this.#manifest.name}] unknown action "${name}"`);
        return this.onAction(name, args);
    }

    #safely(fn, label) {
        try { return fn(); } catch (error) { console.error(`[${this.constructor.name}] error in ${label}:`, error); }
    }

    onLoad() {
        super.onLoad();
        for (const [port, value] of this.#pendingInputs) this.#safely(() => this.onInput(port, value), `onInput("${port}")`);
        this.#pendingInputs.clear();
    }

    //Called by the shell when the instance is removed for good. Not tied to disconnectedCallback: moving an
    //element between panels disconnects it too, and that must not look like a removal.
    dispose() {
        this.#safely(() => this.onDispose(), 'onDispose()');
    }

    //Extension points
    onInput(port, value) {}
    onAction(name, args) {}
    onConfig(config) {}
    onRole(role) {}
    onDispose() {}
    describeContext() { return {}; }
};

export const AgoraComponent = mix(ReactiveComponent).with(AgoraInstance);

//Frozen at the end of M2 (D-04). See docs/contrato-agoracomponent.md before changing anything the contract lists.
AgoraComponent.CONTRACT_VERSION = 1;
