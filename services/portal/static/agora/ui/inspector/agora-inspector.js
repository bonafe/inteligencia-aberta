//Configuration panel for the selected instance, generated from the manifest's config schema (R-INS-1).
//Edits are committed on 'change' (blur/Enter), so one edit is one undo step, and go through Workspace.setConfig.
import { ReactiveComponent } from '../../vendor/ultima/reactive_component.js';
import { h } from '../dom.js';

export class AgoraInspector extends ReactiveComponent {
    #workspace = null;
    #selectedId = null;
    #stop = null;
    #queued = false;
    #actionResult = null;           //{ id, message, error?, suggestions?, port? } of the last action run (view state)

    constructor() {
        super({ templateUrl: './agora-inspector.html', shadowDom: true }, import.meta.url);
    }

    set workspace(workspace) {
        this.#stop?.();
        this.#workspace = workspace;
        const stops = [
            workspace?.doc.onChange('', path => {
                if (!path.startsWith('content.') && !this.rootNode.activeElement?.matches?.('input, textarea, select')) this.#queuePaint();
            }),
            workspace?.onRole(() => this.#queuePaint()),
        ];
        this.#stop = () => stops.forEach(stop => stop?.());
        this.#queuePaint();
    }

    set selectedId(id) { this.#selectedId = id; this.#actionResult = null; this.#queuePaint(); }

    onLoad() { super.onLoad(); this.#queuePaint(); }

    #queuePaint() {
        if (this.#queued) return;
        this.#queued = true;
        queueMicrotask(() => { this.#queued = false; this.#paint(); });
    }

    #paint() {
        if (!this.loaded) return;
        const container = this.rootNode.querySelector('#inspector');
        const workspace = this.#workspace;
        const record = workspace && this.#selectedId ? workspace.instance(this.#selectedId) : null;
        if (!record) { container.replaceChildren(h('p', { class: 'empty' }, 'Selecione um componente para configurá-lo.')); return; }

        const manifest = workspace.registry.getManifest(record.type);
        const id = this.#selectedId;
        const readOnly = workspace.readOnly;

        const fields = Object.entries(manifest?.config ?? {}).map(([name, spec]) => this.#field(id, name, spec, record.config[name] ?? spec.default, readOnly));

        container.replaceChildren(...[
            h('h2', {}, record.label),
            h('h3', {}, 'Geral'),
            h('label', {}, h('span', {}, 'Nome'),
                h('input', { type: 'text', '.value': record.label, disabled: readOnly, onChange: event => { const label = event.target.value.trim(); if (label) { workspace.setLabel(id, label); container.querySelector('h2').textContent = label; } } })),
            fields.length ? h('h3', {}, 'Dados') : null,
            ...fields,
            ...this.#connections(id, record, manifest, readOnly),
            ...this.#actions(id, manifest, readOnly),
            h('h3', {}, 'Informações'),
            h('dl', {},
                h('dt', {}, 'Tipo'), h('dd', {}, `${record.type} ${record.typeVersion}${manifest ? '' : ' (não instalado)'}`),
                h('dt', {}, 'Criado por'), h('dd', {}, `${record.createdBy} (${record.createdVia})`),
                h('dt', {}, 'Id'), h('dd', {}, id)),
            readOnly ? null : h('button', { class: 'danger', type: 'button', onClick: () => workspace.removeInstance(id) }, 'Remover componente'),
        ].filter(Boolean));
    }

    // ---- connections (R-CONN-5, MVP form: a selector per input) ----

    #connections(id, record, manifest, readOnly) {
        const workspace = this.#workspace;
        const bus = workspace.bus;
        const inputs = Object.entries(manifest?.inputs ?? {});
        const outputs = Object.entries(manifest?.outputs ?? {});
        if (!inputs.length && !outputs.length) return [];
        const all = bus.list();
        const labelOf = instanceId => workspace.instance(instanceId)?.label ?? instanceId;

        const inputRows = inputs.map(([port, spec]) => {
            const current = all.filter(c => c.to.instance === id && c.to.port === port);
            const sources = bus.compatibleSources(id, port);
            const error = h('div', { class: 'error', role: 'alert' });
            const value = current[0] ? `${current[0].from.instance}|${current[0].from.port}` : '';
            return h('label', {}, h('span', {}, `Entrada ${port} (${spec.type})${spec.multiple ? ' — várias' : ''}`),
                h('select', {
                    disabled: readOnly, 'aria-label': `Receber em ${port}`,
                    onChange: event => {
                        try {
                            workspace.doc.transact(`connect ${port}`, () => {
                                for (const c of current) bus.disconnect(c.id);
                                if (event.target.value) {
                                    const [instance, sourcePort] = event.target.value.split('|');
                                    bus.connect({ instance, port: sourcePort }, { instance: id, port }, { replace: true });
                                }
                            });
                        } catch (failure) { error.textContent = failure.message; }
                        this.#queuePaint();
                    },
                },
                h('option', { value: '', selected: value === '' }, '— sem conexão —'),
                sources.map(source => h('option', { value: `${source.instance}|${source.port}`, selected: `${source.instance}|${source.port}` === value },
                    `${source.label} · ${source.port}${source.via ? ` (converte: ${source.via})` : ''}`))),
                error);
        });

        const outputRows = outputs.map(([port, spec]) => {
            const targets = all.filter(c => c.from.instance === id && c.from.port === port);
            return h('div', { class: 'port-out' }, h('span', {}, `Saída ${port} (${spec.type})`),
                targets.length ? targets.map(c => h('div', { class: 'link' },
                    `→ ${labelOf(c.to.instance)} · ${c.to.port}`,
                    readOnly ? null : h('button', { type: 'button', 'aria-label': `Remover conexão para ${labelOf(c.to.instance)}`, onClick: () => bus.disconnect(c.id) }, '✕')))
                    : h('div', { class: 'link muted' }, 'sem destinos'));
        });
        return [h('h3', {}, 'Conexões'), ...inputRows, ...outputRows];
    }

    // ---- actions (4.6) ----

    #actions(id, manifest, readOnly) {
        const actions = Object.entries(manifest?.actions ?? {});
        if (!actions.length) return [];
        const workspace = this.#workspace;
        const result = this.#actionResult?.id === id ? this.#actionResult : null;

        const run = async name => {
            this.#actionResult = { id, message: 'Executando…' };
            this.#queuePaint();
            try {
                const outcome = await workspace.invokeAction(id, name);
                const suggestions = outcome.created ? [] : outcome.suggestions;
                this.#actionResult = {
                    id, port: outcome.port, suggestions,
                    message: outcome.created ? 'Componente criado e conectado.' : suggestions.length ? 'Mostrar como:' : 'Resultado produzido; nenhum componente instalado o exibe.',
                };
            } catch (failure) {
                this.#actionResult = { id, message: failure.message, error: true };
            }
            this.#queuePaint();
        };

        return [
            h('h3', {}, 'Ações'),
            ...actions.map(([name, spec]) => h('button', { class: 'action', type: 'button', disabled: readOnly, onClick: () => run(name) }, spec.title)),
            result ? h('div', { class: result.error ? 'error' : 'action-result', role: result.error ? 'alert' : 'status' },
                result.message,
                (result.suggestions ?? []).map(candidate => h('button', {
                    class: 'action', type: 'button',
                    onClick: () => {
                        try { workspace.createLinked(id, result.port, candidate); this.#actionResult = { id, message: 'Componente criado e conectado.' }; }
                        catch (failure) { this.#actionResult = { id, message: failure.message, error: true }; }
                        this.#queuePaint();
                    },
                }, `${candidate.title}${candidate.via ? ` (converte: ${candidate.via})` : ''}`))) : null,
        ];
    }

    #field(id, name, spec, value, readOnly) {
        const error = h('div', { class: 'error', role: 'alert' });
        const commit = parsed => {
            const errors = this.#workspace.setConfig(id, { [name]: parsed });
            error.textContent = errors.join(' ');
            if (errors.length) this.#queuePaint();
        };
        const common = { disabled: readOnly };
        let input;
        switch (spec.type) {
            case 'text': input = h('textarea', { ...common, '.value': value ?? '', onChange: e => commit(e.target.value) }); break;
            case 'boolean': input = h('input', { ...common, type: 'checkbox', '.checked': !!value, onChange: e => commit(e.target.checked) }); break;
            case 'integer': case 'number':
                input = h('input', { ...common, type: 'number', min: spec.min, max: spec.max, step: spec.type === 'integer' ? 1 : 'any', '.value': value ?? '', onChange: e => commit(e.target.value === '' ? spec.default : Number(e.target.value)) }); break;
            case 'enum':
                input = h('select', { ...common, onChange: e => commit(e.target.value) }, spec.values.map(v => h('option', { value: v, selected: v === value }, v))); break;
            case 'list':
                input = h('textarea', { ...common, '.value': JSON.stringify(value ?? [], null, 1), onChange: e => { try { commit(JSON.parse(e.target.value)); } catch { error.textContent = 'JSON inválido.'; } } }); break;
            case 'color': case 'date':
                input = h('input', { ...common, type: spec.type, '.value': value ?? '', onChange: e => commit(e.target.value) }); break;
            default:
                input = h('input', { ...common, type: 'text', '.value': value ?? '', onChange: e => commit(e.target.value) });
        }
        return h('label', {}, h('span', {}, spec.title ?? name), input, error);
    }
}

customElements.define('agora-inspector', AgoraInspector);
