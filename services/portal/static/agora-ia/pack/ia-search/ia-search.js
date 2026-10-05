import { AgoraComponent } from '../../../agora/core/components/agora_component.js';
import { h } from '../../../agora/ui/dom.js';

const copyNote = data => {
    if (!data.offline) return '';
    const hora = new Date(data.cached_at).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
    return ` — sem conexão: cópia local de ${hora}${data.omitted ? `; ${data.omitted} item(ns) não guardado(s) por nível de classificação` : ''}`;
};
const LEVELS = { publico: 'público', interno: 'interno', restrito: 'restrito', confidencial: 'confidencial' };

export default class IaSearch extends AgoraComponent {
    #results = [];
    #selected = null;                       //view state: id of the chosen result (local)
    #run = 0;                               //guards against a slow answer overwriting a newer one

    constructor() {
        super({ templateUrl: './ia-search.html', shadowDom: true }, import.meta.url);
    }

    onLoad() {
        super.onLoad();
        const root = this.rootNode;
        root.querySelector('#query').value = this.config.query ?? '';
        root.querySelector('#form').addEventListener('submit', event => {
            event.preventDefault();
            const query = root.querySelector('#query').value.trim();
            //Persisting the query needs the right to change the composition; a viewer can still search, locally
            try { this.setConfig({ query }); } catch { /* read-only role */ }
            this.#search(query);
        });
        //Outputs are derived state: a reopened workspace searches again so the connections have their values back
        //(an empty query lists the most recent artifacts)
        this.#search(this.config.query ?? '');
    }

    onConfig(config) {
        const box = this.rootNode?.querySelector('#query');
        if (box && this.rootNode.activeElement !== box && box.value !== (config.query ?? '')) { box.value = config.query ?? ''; this.#search(config.query); }
    }

    async #search(query) {
        const run = ++this.#run;
        const status = this.rootNode.querySelector('#status');
        status.className = '';
        status.textContent = 'Buscando…';
        try {
            const data = await this.domain.get('/artefatos/', { q: query, limite: this.config.limit });
            const results = data.results;
            if (run !== this.#run) return;
            this.#results = results;
            this.#selected = null;
            status.textContent = (results.length ? `${results.length} resultado(s)` : 'Nada encontrado.') + copyNote(data);
            this.#paint();
            this.emit('results', results.map(({ id, label, kind, classification }) => ({ id, label, kind, classification })));
        } catch (error) {
            if (run !== this.#run) return;
            status.className = 'error';
            status.textContent = error.status === 401 || error.status === 0 ? error.message : `Não foi possível buscar: ${error.message}`;
        }
    }

    #paint() {
        this.rootNode.querySelector('#results').replaceChildren(...this.#results.map(item => h('li', {}, h('button', {
            type: 'button', 'aria-pressed': String(item.id === this.#selected),
            onClick: () => { this.#selected = item.id; this.#paint(); this.emit('selected', { id: item.id, label: item.label, kind: item.kind, classification: item.classification }); },
        }, item.label, h('small', {}, `${item.kind} · ${LEVELS[item.classification] ?? item.classification}`),
        item.above_workspace ? h('small', { class: 'warn', title: 'Mais restrito que este workspace' }, ' ⚠') : null))));
    }

    describeContext() { return { query: this.config.query ?? '', results: this.#results.length }; }
}
