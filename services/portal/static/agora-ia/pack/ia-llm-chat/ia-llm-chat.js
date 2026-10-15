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
        root.querySelector('#machine').addEventListener('change', () => this.#fillModels());
        root.querySelector('#refresh').addEventListener('click', () => this.#loadModels());
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
            this.#fillMachines();
            return;
        }
        this.#fillMachines();
        this.#status(this.#models.length ? '' : 'Nenhum modelo disponível agora: instale um em /cluster/ e espere o heartbeat (até 30 s).', !this.#models.length);
    }

    //A máquina vem primeiro, porque é ela que tem os modelos: ao trocá-la, a lista de modelos é refeita só com os dela.
    //"Automático" mostra todos os modelos do cluster e deixa o roteador escolher a máquina mais rápida.
    #machines() {
        const byId = new Map();
        for (const item of this.#models) for (const machine of item.maquinas) byId.set(machine.id, machine.apelido);
        return [...byId].map(([id, apelido]) => ({ id, apelido })).sort((a, b) => a.apelido.localeCompare(b.apelido));
    }

    #fillMachines() {
        const select = this.rootNode.querySelector('#machine');
        const previous = select.value;
        const machines = this.#machines();
        select.replaceChildren(
            h('option', { value: AUTO }, 'Automático (a mais rápida)'),
            ...machines.map(machine => h('option', { value: machine.id, selected: machine.id === previous }, machine.apelido)),
        );
        this.#fillModels();
    }

    #fillModels() {
        const root = this.rootNode;
        const machine = root.querySelector('#machine').value;
        const select = root.querySelector('#model');
        const previous = select.value || this.config.model;
        const available = this.#models
            .map(item => ({ modelo: item.modelo, on: item.maquinas.filter(m => machine === AUTO || m.id === machine) }))
            .filter(item => item.on.length);
        const label = item => {
            if (machine === AUTO) return item.on.length > 1 ? `${item.modelo} (em ${item.on.length} máquinas)` : `${item.modelo} (${item.on[0].apelido})`;
            return item.on[0].tokens_por_segundo ? `${item.modelo} · ${item.on[0].tokens_por_segundo} tok/s` : item.modelo;
        };
        select.replaceChildren(...available.map(item => h('option', { value: item.modelo, selected: item.modelo === previous }, label(item))));
        this.#refreshControls();
    }

    #refreshControls() {
        const root = this.rootNode;
        const unavailable = this.#busy || !this.#models.length;
        root.querySelector('#send').disabled = unavailable;
        root.querySelector('#model').disabled = this.#busy;
        root.querySelector('#machine').disabled = this.#busy;
        root.querySelector('#refresh').disabled = this.#busy;
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
