import { AgoraComponent } from '../../../agora/core/components/agora_component.js';
import { h } from '../../../agora/ui/dom.js';

const AUTO = '';           //"deixe o cluster escolher a máquina" (o roteador pega a mais rápida)

//Conversa com os modelos de linguagem das máquinas do cluster. Cada mensagem vai ao servidor (com a sessão de quem
//escreve) e o histórico inteiro viaja a cada rodada; nada fica guardado lá. O histórico mora em sessionStorage:
//só neste navegador, só nesta aba, e fora do documento compartilhado do workspace.
export default class IaLlmChat extends AgoraComponent {
    #models = [];          //[{ modelo, maquinas: [{ id, apelido, tokens_por_segundo }] }]
    #messages = [];        //[{ role, content, meta? }]
    #busy = false;

    constructor() {
        super({ templateUrl: './ia-llm-chat.html', shadowDom: true }, import.meta.url);
    }

    get #storageKey() { return `ia-llm-chat:${this.instanceId}`; }

    onLoad() {
        super.onLoad();
        const root = this.rootNode;
        this.#restore();
        root.querySelector('#model').addEventListener('change', () => this.#fillMachines());
        root.querySelector('#clear').addEventListener('click', () => { this.#messages = []; this.#persist(); this.#paint(); });
        root.querySelector('#form').addEventListener('submit', event => { event.preventDefault(); this.#send(); });
        root.querySelector('#input').addEventListener('keydown', event => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); this.#send(); }
        });
        this.#paint();
        this.#loadModels();
    }

    #status(text, error = false) {
        const status = this.rootNode.querySelector('#status');
        status.className = error ? 'error' : '';
        status.textContent = text;
    }

    #restore() {
        try {
            const saved = JSON.parse(sessionStorage.getItem(this.#storageKey) ?? '[]');
            if (Array.isArray(saved)) this.#messages = saved.filter(m => m && typeof m.content === 'string' && ['user', 'assistant'].includes(m.role));
        } catch { /* storage blocked or corrupted: start empty */ }
    }

    #persist() {
        try { sessionStorage.setItem(this.#storageKey, JSON.stringify(this.#messages)); } catch { /* the chat works without it */ }
    }

    async #loadModels() {
        this.#status('Buscando modelos…');
        try {
            const data = await this.domain.get('/llm/modelos/');
            this.#models = data.modelos ?? [];
        } catch (error) {
            this.#models = [];
            this.#status(error.status === 401 || error.status === 0 ? error.message : `Não foi possível listar os modelos: ${error.message}`, true);
            this.#fillModels();
            return;
        }
        this.#fillModels();
        this.#status(this.#models.length ? '' : 'Nenhum modelo disponível agora: instale um em /cluster/ e espere o heartbeat (até 30 s).', !this.#models.length);
    }

    #fillModels() {
        const select = this.rootNode.querySelector('#model');
        const previous = select.value || this.config.model;
        select.replaceChildren(...this.#models.map(item => h('option', { value: item.modelo, selected: item.modelo === previous }, item.modelo)));
        this.#fillMachines();
        this.#refreshControls();
    }

    #fillMachines() {
        const model = this.#models.find(item => item.modelo === this.rootNode.querySelector('#model').value);
        const select = this.rootNode.querySelector('#machine');
        const options = [h('option', { value: AUTO }, 'Automático (a mais rápida)')];
        for (const machine of model?.maquinas ?? []) {
            options.push(h('option', { value: machine.id }, machine.tokens_por_segundo ? `${machine.apelido} · ${machine.tokens_por_segundo} tok/s` : machine.apelido));
        }
        select.replaceChildren(...options);
    }

    #refreshControls() {
        const root = this.rootNode;
        const unavailable = this.#busy || !this.#models.length;
        root.querySelector('#send').disabled = unavailable;
        root.querySelector('#model').disabled = this.#busy;
        root.querySelector('#machine').disabled = this.#busy;
    }

    #paint() {
        const log = this.rootNode.querySelector('#log');
        log.replaceChildren(...this.#messages.map(message => h('li', { class: message.role }, message.content,
            message.meta ? h('small', {}, message.meta) : null)));
        log.scrollTop = log.scrollHeight;
    }

    async #send() {
        const root = this.rootNode;
        const input = root.querySelector('#input');
        const text = input.value.trim();
        const model = root.querySelector('#model').value;
        if (this.#busy || !text || !model) return;

        this.#messages.push({ role: 'user', content: text });
        input.value = '';
        this.#busy = true;
        this.#refreshControls();
        this.#paint();
        this.#status('Aguardando a resposta…');
        const body = { modelo: model, mensagens: this.#messages.map(({ role, content }) => ({ role, content })) };
        const machine = root.querySelector('#machine').value;
        if (machine !== AUTO) body.maquina = machine;
        try {
            const answer = await this.domain.post('/llm/chat/', body);
            const speed = answer.tokens_por_segundo ? ` · ${answer.tokens_por_segundo} tok/s` : '';
            this.#messages.push({ role: 'assistant', content: answer.conteudo || '(resposta vazia)', meta: `${answer.modelo} em ${answer.maquina.apelido}${speed}` });
            this.#status('');
            if (this.config.model !== model) { try { this.setConfig({ model }); } catch { /* read-only role */ } }
        } catch (error) {
            this.#messages.pop();                                    //the question was not answered: give it back to be sent again
            input.value = text;
            this.#status(error.status === 0 || error.status === 401 ? error.message : `Falhou: ${error.message}`, true);
        } finally {
            this.#busy = false;
            this.#persist();
            this.#refreshControls();
            this.#paint();
        }
    }

    describeContext() { return { model: this.rootNode.querySelector('#model')?.value ?? '', messages: this.#messages.length }; }
}
