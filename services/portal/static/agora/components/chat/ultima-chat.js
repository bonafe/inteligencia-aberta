import { AgoraComponent } from '../../core/components/agora_component.js';
import { h } from '../../ui/dom.js';
import { renderMarkdown } from '../markdown/markdown.js';
import { colorFor } from '../../collaboration/presence/presence.js';

const timeOf = iso => new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });

//Same component as a tab in the workspace or as the shell's drawer (R-CHAT-1): it only needs `this.chat`.
export default class UltimaChat extends AgoraComponent {
    #editing = null;                        // message id being edited (view state)
    #stop = null;
    #divider = null;                        // seq of the last message read when the view opened: where "novas" starts
    #sticky = true;                         // follow new messages while the reader is at the bottom

    constructor() {
        super({ templateUrl: './ultima-chat.html', shadowDom: true }, import.meta.url);
    }

    onLoad() {
        super.onLoad();
        const chat = this.chat;
        const root = this.rootNode;
        if (!chat) { this.#notice('O chat não está disponível neste workspace.'); root.querySelector('#composer').hidden = true; return; }

        this.#divider = chat.lastRead;
        const text = root.querySelector('#text');
        root.querySelector('#composer').addEventListener('submit', event => { event.preventDefault(); this.#submit(); });
        text.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); this.#submit(); } });
        const log = root.querySelector('#log');
        log.addEventListener('scroll', () => { this.#sticky = log.scrollHeight - log.scrollTop - log.clientHeight < 40; });
        this.addEventListener('focusin', () => this.#markRead());
        log.addEventListener('pointerdown', () => this.#markRead());

        this.#stop = chat.onChange(() => this.#paint());
        chat.ready.then(() => { this.#divider = chat.lastRead; this.#paint(); this.#markRead(); });
        this.#paint();
    }

    onRole() { this.#paint(); }
    onDispose() { this.#stop?.(); }

    //Read only counts when the chat is actually in front of the person
    get #visible() { return this.isConnected && this.getClientRects().length > 0 && document.visibilityState === 'visible'; }
    #markRead() { if (this.#visible) this.chat?.markRead(); }

    #notice(message) {
        const box = this.rootNode.querySelector('#notice');
        box.textContent = message ?? '';
        box.toggleAttribute('hidden', !message);
    }

    #submit() {
        const chat = this.chat;
        const text = this.rootNode.querySelector('#text');
        const body = text.value.trim();
        if (!body) return;
        try { chat.send(body); text.value = ''; this.#sticky = true; this.#notice(null); }
        catch (error) { this.#notice(error.message); }
    }

    #body(message) {
        let html = renderMarkdown(message.body);                              // escaped first: no input becomes markup (R-CHAT-4)
        const me = this.chat.me.name.toLowerCase();
        for (const token of new Set([me.replace(/\s+/g, ''), me.split(/\s+/)[0]])) {
            const safe = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            html = html.replace(new RegExp(`(^|[\\s>(])(@${safe})(?![\\p{L}\\p{N}_.-])`, 'giu'), '$1<mark>$2</mark>');
        }
        const element = h('div', { class: 'body' });
        element.innerHTML = html;
        return element;
    }

    #paint() {
        const chat = this.chat;
        if (!chat || !this.loaded) return;
        const root = this.rootNode;
        const log = root.querySelector('#log');
        const composer = root.querySelector('#composer');

        if (!chat.available()) { this.#notice('Chat em tempo real indisponível: este workspace não está ligado a um servidor de colaboração.'); composer.hidden = true; }
        else if (!chat.canWrite) { this.#notice('Seu papel só permite ler o chat.'); composer.hidden = true; }
        else { composer.hidden = false; this.#notice(null); }

        const messages = chat.messages;
        const nodes = [];
        if (chat.hasMore) nodes.push(h('button', { class: 'older', type: 'button', onClick: () => chat.loadOlder() }, 'Mensagens anteriores'));
        if (!messages.length) nodes.push(h('p', { class: 'empty' }, 'Nenhuma mensagem ainda.'));
        let dividerShown = false;
        for (const message of messages) {
            if (!dividerShown && this.#divider !== null && message.seq > this.#divider && message.author.id !== chat.me.id && !message.pending) {
                nodes.push(h('div', { class: 'divider', role: 'separator' }, 'Novas mensagens'));
                dividerShown = true;
            }
            nodes.push(this.#messageNode(message));
        }
        const keepBottom = this.#sticky;
        log.replaceChildren(...nodes);
        if (keepBottom) log.scrollTop = log.scrollHeight;
    }

    #messageNode(message) {
        const chat = this.chat;
        const mine = message.author.id === chat.me.id;
        const state = message.pending === 'failed' ? h('span', { class: 'state failed' }, message.error ?? 'Não enviada')
            : message.pending ? h('span', { class: 'state' }, message.pending === 'sending' ? 'Enviando…' : 'Pendente (sem conexão)') : null;
        const classes = ['message', mine ? 'mine' : '', message.pending ? 'pending' : '', !mine && chat.mentionsMe(message) ? 'mentions-me' : ''].filter(Boolean).join(' ');

        let content;
        if (message.deletedAt) content = h('p', { class: 'body deleted' }, 'Mensagem removida');
        else if (this.#editing === message.id) content = this.#editor(message);
        else content = this.#body(message);

        const canAct = !message.pending && !message.deletedAt && this.#editing !== message.id && chat.canWrite;
        const actions = canAct ? h('span', { class: 'actions' },
            mine ? h('button', { type: 'button', onClick: () => { this.#editing = message.id; this.#paint(); } }, 'Editar') : null,
            mine ? h('button', { type: 'button', onClick: () => this.#run(() => chat.remove(message.id)) }, 'Remover') : null) : null;

        return h('article', { class: classes, 'data-id': message.id, 'aria-label': `${message.author.name}, ${timeOf(message.createdAt)}` },
            h('header', {},
                h('span', { class: 'author', style: `color:${colorFor(message.author.id)}` }, mine ? 'Você' : message.author.name),
                h('time', { datetime: message.createdAt }, timeOf(message.createdAt)),
                message.editedAt && !message.deletedAt ? h('span', { class: 'state' }, '(editada)') : null,
                state, actions),
            content,
            message.pending === 'failed' ? h('div', { class: 'failed-actions' },
                h('button', { type: 'button', onClick: () => chat.retry(message.clientId) }, 'Tentar de novo'),
                h('button', { type: 'button', onClick: () => chat.discard(message.clientId) }, 'Descartar')) : null);
    }

    #editor(message) {
        const field = h('textarea', { rows: 2, '.value': message.body, 'aria-label': 'Editar mensagem' });
        const save = () => { const body = field.value.trim(); if (body && body !== message.body) this.#run(() => this.chat.edit(message.id, body)); this.#editing = null; this.#paint(); };
        field.addEventListener('keydown', event => {
            if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); save(); }
            if (event.key === 'Escape') { this.#editing = null; this.#paint(); }
        });
        queueMicrotask(() => field.focus());
        return h('div', { class: 'edit' }, field, h('div', { class: 'row' },
            h('button', { type: 'button', onClick: save }, 'Salvar'),
            h('button', { type: 'button', onClick: () => { this.#editing = null; this.#paint(); } }, 'Cancelar')));
    }

    #run(action) { try { action(); this.#notice(null); } catch (error) { this.#notice(error.message); } }

    describeContext() { return { messages: this.chat?.messages.length ?? 0, unread: this.chat?.unread ?? 0 }; }
}
