import { AgoraComponent } from '../../../agora/core/components/agora_component.js';
import { h } from '../../../agora/ui/dom.js';

const LEVELS = { publico: 'público', interno: 'interno', restrito: 'restrito', confidencial: 'confidencial' };
const INFO = { fato: 'fato', opiniao: 'opinião', inferencia: 'inferência' };

export default class IaEntity extends AgoraComponent {
    #incoming = null;               //Entity arriving on the "entity" input; wins over the configured id while connected
    #current = null;                //id being shown
    #relations = null;
    #run = 0;

    constructor() {
        super({ templateUrl: './ia-entity.html', shadowDom: true }, import.meta.url);
    }

    onLoad() { super.onLoad(); this.#show(); }
    onConfig() { this.#show(); }
    onInput(port, value) {
        if (port !== 'entity') return;
        this.#incoming = value?.id ? value : null;
        //What is being investigated is part of the shared work: pin the chosen entity in this component's config so
        //everyone sees it and it survives reopening (a viewer, who cannot write, just shows it locally)
        if (this.#incoming && this.config.entity !== this.#incoming.id) {
            try { this.setConfig({ entity: this.#incoming.id }); } catch { /* read-only role */ }
        }
        this.#show();
    }

    get #id() { return this.#incoming?.id || this.config.entity || null; }

    async #show() {
        const id = this.#id;
        if (id === this.#current && this.#relations) return;
        this.#current = id;
        const run = ++this.#run;
        const root = this.rootNode;
        const set = (selector, hidden) => root.querySelector(selector).toggleAttribute('hidden', hidden);
        set('#error', true);
        if (!id) { set('#empty', false); set('#card', true); this.#relations = null; return; }
        set('#empty', true);

        try {
            //Details and relations are fetched with the permission of whoever is looking; nothing is kept in the document
            const [detail, graph] = await Promise.all([this.domain.get(`/artefatos/${id}/`), this.domain.get(`/artefatos/${id}/relacoes/`)]);
            if (run !== this.#run) return;
            this.#relations = graph;
            root.querySelector('#label').textContent = detail.label;
            root.querySelector('#meta').textContent = `${detail.kind} · nível ${LEVELS[detail.classification] ?? detail.classification} · ${INFO[detail.info_type] ?? detail.info_type}`;
            set('#warning', !detail.above_workspace);
            root.querySelector('#claims').replaceChildren(...(detail.claims.length ? detail.claims.map(c => h('li', {},
                `${c.predicate}: ${c.object ?? '—'} `,
                h('small', {}, `(${c.producer}${c.confidence != null ? `, confiança ${Math.round(c.confidence * 100)}%` : ''}${c.state === 'retratada' ? ', retratada' : ''})`),
            )) : [h('li', {}, 'Nenhuma alegação registrada.')]));
            root.querySelector('#sources').replaceChildren(...(detail.sources.length ? detail.sources.map(s => h('li', {}, String(s.origem ?? s.url ?? 'fonte'), s.confianca != null ? h('small', {}, ` (confiança ${Math.round(s.confianca * 100)}%)`) : null)) : [h('li', {}, 'Sem fonte registrada.')]));
            set('#card', false);
            this.emit('entity', { id: detail.id, label: detail.label, kind: detail.kind, classification: detail.classification });
            this.emit('relations', graph);
        } catch (error) {
            if (run !== this.#run) return;
            this.#relations = null;
            set('#card', true);
            const box = root.querySelector('#error');
            box.textContent = error.status === 404 ? 'Sem acesso a este objeto (ou ele não existe).' : `Não foi possível carregar: ${error.message}`;
            set('#error', false);
        }
    }

    //The graph of immediate relations; the same value the "relations" output carries
    onAction(name) {
        if (name !== 'showRelations') return undefined;
        if (!this.#relations) throw new Error('Escolha uma entidade primeiro.');
        return this.#relations;
    }

    describeContext() { return { entity: this.#id }; }
}
