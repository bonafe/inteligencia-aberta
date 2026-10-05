//The component bus (docs/especificacao/04): typed ports joined by connections stored in the document.
//
//  doc.connections.<id>   { from: { instance, port }, to: { instance, port }, createdBy, createdVia, createdAt }
//
//Connections are persistent and collaborative; the VALUES flowing through them are local, derived state
//(R-PORT-4): each output keeps its last value here, and inputs are recomputed from the connections, so
//reopening a workspace or mounting a component late always converges to the right values.
import { uuid } from '../ids.js';

export class BusError extends Error {}

const signature = value => (value === undefined ? '∅' : JSON.stringify(value));
const keyOf = (instance, port) => `${instance}.${port}`;

export class Bus {
    #workspace;
    #elements = new Map();          //instanceId -> live element
    #outputs = new Map();           //"instance.port" -> { value, signature }
    #delivered = new Map();         //"instance.port" of an input -> signature last delivered
    #inputs = new Map();            //"instance.port" of an input -> value last delivered
    #queued = false;
    #lastStamp = 0;
    #stop;

    constructor(workspace) {
        this.#workspace = workspace;
        this.#stop = workspace.doc.onChange('connections', () => this.#queueSync());
    }

    get #types() { return this.#workspace.registry.types; }
    dispose() { this.#stop(); }

    // ---- registration of live elements ----

    attach(instanceId, element) { this.#elements.set(instanceId, element); this.#queueSync(); }

    detach(instanceId) {
        this.#elements.delete(instanceId);
        for (const map of [this.#outputs, this.#delivered, this.#inputs]) {
            for (const key of [...map.keys()]) if (key.startsWith(`${instanceId}.`)) map.delete(key);
        }
        this.#queueSync();
    }

    element(instanceId) { return this.#elements.get(instanceId) ?? null; }

    // ---- values ----

    //Called by a component (through AgoraComponent.emit) or by an action result.
    publish(instanceId, port, value) {
        const manifest = this.#manifestOf(instanceId);
        const spec = manifest?.outputs?.[port];
        if (!spec) throw new BusError(`"${instanceId}" não tem a saída "${port}"`);
        const problem = this.#types.validate(spec.type, value);
        if (problem) throw new BusError(`Valor inválido em ${manifest.name}.${port}: ${problem}`);

        const key = keyOf(instanceId, port);
        const next = signature(value);
        if (spec.retained !== false && this.#outputs.get(key)?.signature === next) return;       //same value: nothing to deliver
        this.#outputs.set(key, { value, signature: next, transient: spec.retained === false });
        this.#queueSync();
    }

    outputValue(instanceId, port) { return this.#outputs.get(keyOf(instanceId, port))?.value; }
    inputValue(instanceId, port) { return this.#inputs.get(keyOf(instanceId, port)); }

    //The object a component sees as `this.ports`
    portsFor(instanceId) { return { value: port => this.inputValue(instanceId, port) }; }

    // ---- connections ----

    list() {
        return Object.entries(this.#workspace.doc.get('connections') ?? {}).map(([id, connection]) => ({ id, ...connection }));
    }

    #manifestOf(instanceId) {
        const record = this.#workspace.instance(instanceId);
        return record ? this.#workspace.registry.getManifest(record.type) : null;
    }

    #reaches(fromInstance, toInstance, connections) {
        const seen = new Set();
        const visit = id => {
            if (id === toInstance) return true;
            if (seen.has(id)) return false;
            seen.add(id);
            return connections.filter(c => c.from.instance === id).some(c => visit(c.to.instance));
        };
        return visit(fromInstance);
    }

    //Why a connection would be refused, or null when it is fine. Used by connect() and by the UI to list options.
    check(from, to) {
        const source = this.#manifestOf(from.instance);
        const target = this.#manifestOf(to.instance);
        if (!source || !target) return { reason: 'Componente não instalado ou instância inexistente' };
        const output = source.outputs?.[from.port];
        const input = target.inputs?.[to.port];
        if (!output) return { reason: `${source.name} não tem a saída "${from.port}"` };
        if (!input) return { reason: `${target.name} não tem a entrada "${to.port}"` };
        const compatibility = this.#types.compatibility(output.type, input.type);
        if (!compatibility.ok) return { reason: `${output.type} não é aceito por "${to.port}" (espera ${input.type})` };
        if (from.instance === to.instance || this.#reaches(to.instance, from.instance, this.list())) {
            return { reason: 'A conexão criaria um ciclo' };
        }
        return { reason: null, via: compatibility.via, input };
    }

    //Creates a connection; throws BusError with a readable reason when refused (R-CONN-1, R-CONN-2).
    //An already-connected single input is only replaced with { replace: true } (R-PORT-2).
    connect(from, to, { createdVia = 'manual', replace = false } = {}) {
        const verdict = this.check(from, to);
        if (verdict.reason) throw new BusError(verdict.reason);

        const existing = this.list().filter(c => c.to.instance === to.instance && c.to.port === to.port);
        const duplicate = existing.find(c => c.from.instance === from.instance && c.from.port === from.port);
        if (duplicate) return duplicate.id;
        if (verdict.input.multiple !== true && existing.length && !replace) {
            throw new BusError(`A entrada "${to.port}" já está conectada; substitua a conexão existente`);
        }

        const id = `conn-${uuid().slice(0, 8)}`;
        this.#workspace.doc.transact(`connect ${id}`, () => {
            if (verdict.input.multiple !== true) for (const old of existing) this.#workspace.doc.delete(['connections', old.id]);
            const createdAt = this.#lastStamp = Math.max(Date.now(), this.#lastStamp + 1);          //strictly increasing: gives "várias" inputs a stable order
            this.#workspace.doc.set(['connections', id], { from, to, createdBy: this.#workspace.user.id, createdVia, createdAt });
        });
        return id;
    }

    disconnect(connectionId) {
        this.#workspace.doc.transact(`disconnect ${connectionId}`, () => this.#workspace.doc.delete(['connections', connectionId]));
    }

    //Removes every connection touching an instance. Called inside the instance's removal transaction.
    removeConnectionsOf(instanceId) {
        for (const c of this.list()) {
            if (c.from.instance === instanceId || c.to.instance === instanceId) this.#workspace.doc.delete(['connections', c.id]);
        }
    }

    //Outputs of other instances that could feed `inputPort` of `instanceId`.
    compatibleSources(instanceId, inputPort) {
        return this.#workspace.instances().filter(i => i.id !== instanceId).flatMap(source => {
            const manifest = this.#workspace.registry.getManifest(source.type);
            return Object.keys(manifest?.outputs ?? {}).flatMap(port => {
                const verdict = this.check({ instance: source.id, port }, { instance: instanceId, port: inputPort });
                return verdict.reason ? [] : [{ instance: source.id, label: source.label, port, via: verdict.via }];
            });
        });
    }

    // ---- delivery ----

    #queueSync() {
        if (this.#queued) return;
        this.#queued = true;
        queueMicrotask(() => { this.#queued = false; this.#sync(); });
    }

    //Recomputes every live input from the connections and delivers what changed. One pass, in connection
    //order, so deliveries are ordered and values equal to the previous one are never re-sent (4.4.1).
    #sync() {
        const connections = this.list().sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0) || a.id.localeCompare(b.id));
        for (const [instanceId, element] of this.#elements) {
            const manifest = this.#manifestOf(instanceId);
            for (const [port, spec] of Object.entries(manifest?.inputs ?? {})) {
                const key = keyOf(instanceId, port);
                const values = connections
                    .filter(c => c.to.instance === instanceId && c.to.port === port)
                    .flatMap(c => this.#valueFor(c, spec));
                const desired = spec.multiple === true ? (values.length ? values : undefined) : values.at(-1);
                const next = signature(desired);
                if (this.#delivered.get(key) === next || (!this.#delivered.has(key) && desired === undefined)) {
                    this.#delivered.set(key, next);
                    continue;
                }
                this.#delivered.set(key, next);
                this.#inputs.set(key, desired);
                element.deliverInput(port, desired);
            }
        }
        for (const [key, output] of this.#outputs) if (output.transient) this.#outputs.delete(key);
    }

    #valueFor(connection, inputSpec) {
        const output = this.#outputs.get(keyOf(connection.from.instance, connection.from.port));
        if (!output) return [];
        const outputType = this.#manifestOf(connection.from.instance)?.outputs?.[connection.from.port]?.type;
        try {
            const { ok, convert } = this.#types.compatibility(outputType, inputSpec.type);
            return ok ? [convert ? convert(output.value) : output.value] : [];
        } catch (error) {
            console.error('[bus] conversion failed:', error);
            return [];
        }
    }
}
