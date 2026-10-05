import { AgoraComponent } from '../../../agora/core/components/agora_component.js';
import { h } from '../../../agora/ui/dom.js';

const LEVELS = { publico: 'público', interno: 'interno', restrito: 'restrito', confidencial: 'confidencial' };

//Documents (captured pages) that mention the chosen entity. The subject is the entity's NAME, pinned in this component's
//config so everyone sees the same thing; the documents themselves are fetched with each viewer's own permission.
export default class IaNews extends AgoraComponent {
    #incoming = null;
    #documents = [];
    #selected = null;
    #run = 0;
    #shown = null;

    constructor() {
        super({ templateUrl: './ia-news.html', shadowDom: true }, import.meta.url);
    }

    onLoad() { super.onLoad(); this.#search(); }
    onConfig() { this.#search(); }

    onInput(port, value) {
        if (port !== 'entity') return;
        this.#incoming = value?.label ? value : null;
        if (this.#incoming && this.config.subject !== this.#incoming.label) {
            try { this.setConfig({ subject: this.#incoming.label }); } catch { /* read-only role: shown locally */ }
        }
        this.#search();
    }

    get #subject() { return this.#incoming?.label || this.config.subject || ''; }

    async #search() {
        const subject = this.#subject;
        if (subject === this.#shown && this.#documents.length) return;
        this.#shown = subject;
        const root = this.rootNode;
        const run = ++this.#run;
        root.querySelector('#empty').toggleAttribute('hidden', Boolean(subject));
        root.querySelector('#panel').toggleAttribute('hidden', !subject);
        if (!subject) { this.#documents = []; return; }

        root.querySelector('#title').textContent = `Documentos sobre “${subject}”`;
        const status = root.querySelector('#status');
        status.className = '';
        status.textContent = 'Buscando…';
        try {
            const data = await this.domain.get('/artefatos/', { q: subject, tipo: 'documento', limite: this.config.limit });
            if (run !== this.#run) return;
            this.#documents = data.results;
            this.#selected = null;
            const hora = data.offline ? new Date(data.cached_at).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : null;
            status.textContent = (data.results.length ? `${data.results.length} documento(s)` : 'Nenhum documento menciona este assunto.')
                + (hora ? ` — sem conexão: cópia local de ${hora}${data.omitted ? `; ${data.omitted} não guardado(s) por nível de classificação` : ''}` : '');
            this.#paint();
            this.emit('results', data.results.map(({ id, label, kind, classification }) => ({ id, label, kind, classification })));
        } catch (error) {
            if (run !== this.#run) return;
            status.className = 'error';
            status.textContent = error.status === 401 || error.status === 0 ? error.message : `Não foi possível buscar: ${error.message}`;
        }
    }

    #paint() {
        this.rootNode.querySelector('#list').replaceChildren(...this.#documents.map(item => h('li', {}, h('button', {
            type: 'button', 'aria-pressed': String(item.id === this.#selected),
            onClick: () => { this.#selected = item.id; this.#paint(); this.emit('selected', { id: item.id, label: item.label, kind: item.kind, classification: item.classification }); },
        }, item.label, h('small', {}, LEVELS[item.classification] ?? item.classification), item.above_workspace ? h('small', { class: 'warn', title: 'Mais restrito que este workspace' }, ' ⚠') : null))));
    }

    describeContext() { return { subject: this.#subject, documents: this.#documents.length }; }
}
