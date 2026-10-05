//The Agora application shell: toolbar, palette, workspace and inspector around one open workspace.
//Everything it knows about the outside world comes from the host (identity, catalog, store) and the registry.
import { ReactiveComponent } from '../../vendor/ultima/reactive_component.js';
import { Workspace } from '../../core/workspace/workspace.js';
import { h } from '../dom.js';
import '../palette/agora-palette.js';
import '../workspace/agora-workspace.js';
import '../inspector/agora-inspector.js';

const STATUS_TEXT = { synced: 'Salvo', saving: 'Salvando…', error: 'Erro', offline: 'Offline (salvo aqui)', connecting: 'Conectando…' };
const ROLE_TEXT = { owner: 'Dono', editor: 'Editor', participant: 'Participante', viewer: 'Somente leitura' };
const ROLE_NOTICE = {
    participant: 'Você é participante: pode escrever nas notas, mas não alterar a composição do workspace.',
    viewer: 'Você tem acesso somente leitura a este workspace.',
};
const hashFor = id => `#/workspace/${id}`;
const idFromHash = () => /^#\/workspace\/(.+)$/.exec(location.hash)?.[1] ?? null;
const isTextField = element => element?.matches?.('input, textarea, select, [contenteditable=""], [contenteditable="true"]');

export class AgoraShell extends ReactiveComponent {
    #host = null;
    #registry = null;
    #workspace = null;
    #meta = null;
    #cleanup = [];
    #touchTimer = null;

    constructor() {
        super({ templateUrl: './agora-shell.html', shadowDom: true }, import.meta.url);
    }

    get workspace() { return this.#workspace; }

    #$(selector) { return this.rootNode.querySelector(selector); }

    //Shows a message under the toolbar. kind: 'error' | 'warning'
    notify(message, kind = 'error', action = null) {
        const banner = this.#$('#banner');
        banner.replaceChildren(message, ...(action ? [' ', h('button', { type: 'button', onClick: action.onClick }, action.label)] : []));
        banner.className = kind;
        banner.hidden = !message;
    }

    async init({ host, registry }) {
        await this.whenLoaded();
        this.#host = host;
        this.#registry = registry;
        this.#$('agora-palette').registry = registry;
        this.#wire();
        if (host.persistent === false) this.notify('Armazenamento do navegador indisponível (janela anônima ou dados de site bloqueados). Seu trabalho NÃO será salvo ao recarregar.', 'warning');
        const open = () => this.#openFromHash().catch(error => {
            console.error('[agora-shell] could not open the workspace:', error);
            this.notify(`Não foi possível abrir o workspace: ${error.message}. Detalhes no Console (F12).`);
        });
        addEventListener('hashchange', open);
        await open();
    }

    #wire() {
        const on = (selector, event, fn) => this.#$(selector).addEventListener(event, fn);
        on('#new', 'click', async () => { location.hash = hashFor((await this.#host.create()).id); });
        on('#duplicate', 'click', async () => { if (this.#meta) location.hash = hashFor((await this.#host.duplicate(this.#meta.id)).id); });
        on('#archive', 'click', async () => {
            if (!this.#meta) return;
            await this.#host.archive(this.#meta.id);
            location.hash = '';
        });
        on('#workspaces', 'change', event => { location.hash = hashFor(event.target.value); });
        on('#title', 'change', async event => {
            const title = event.target.value.trim() || 'Sem título';
            try { this.#workspace?.setTitle(title); } catch (error) { this.notify(error.message); return; }
            if (this.#meta) this.#meta = await this.#host.rename(this.#meta.id, title);
            await this.#refreshList();
        });
        on('#undo', 'click', () => this.#workspace?.canCompose && this.#workspace.doc.undo());
        on('#redo', 'click', () => this.#workspace?.canCompose && this.#workspace.doc.redo());
        for (const [button, pane] of [['#toggle-palette', '#palette-pane'], ['#toggle-inspector', '#inspector-pane']]) {
            on(button, 'click', event => {
                const hidden = this.#$(pane).toggleAttribute('hidden');
                event.currentTarget.setAttribute('aria-pressed', String(!hidden));
            });
        }
        this.rootNode.addEventListener('palette-add', event => {
            if (!this.#workspace) { this.notify('Nenhum workspace aberto ainda.'); return; }
            try { this.#workspace.addInstance(event.detail.type); } catch (error) {
                console.error('[agora-shell] could not add component:', error);
                this.notify(`Não foi possível adicionar o componente: ${error.message}`);
            }
        });
        this.rootNode.addEventListener('instance-selected', event => { this.#$('agora-inspector').selectedId = event.detail.id; });
        //Ctrl+Z / Ctrl+Shift+Z, unless the user is typing (text fields have their own undo)
        addEventListener('keydown', event => {
            if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'z' || isTextField(event.composedPath()[0])) return;
            event.preventDefault();
            if (!this.#workspace?.canCompose) return;
            if (event.shiftKey) this.#workspace.doc.redo(); else this.#workspace.doc.undo();
        });
    }

    async #openFromHash() {
        let id = idFromHash();
        if (id && !(await this.#host.get(id)) && this.#host.syncEnabled) await this.#host.adopt(id);      //a shared link: the content arrives through the sync
        if (!id || !(await this.#host.get(id)) || (await this.#host.get(id)).archivedAt) {
            const first = (await this.#host.list())[0] ?? await this.#host.create('Meu primeiro workspace');
            id = first.id;
            history.replaceState(null, '', hashFor(id));
        }
        await this.#open(id);
    }

    async #open(id) {
        this.#cleanup.forEach(stop => stop());
        this.#cleanup = [];

        this.#meta = await this.#host.get(id);
        const doc = await this.#host.openDocument(this.#meta);
        const workspace = new Workspace({ doc, registry: this.#registry, user: this.#host.identity, domain: this.#host.domain ?? null, id });
        this.#workspace = workspace;

        //The document is the source of truth for the title; the catalog only mirrors it for the list.
        //Only the creator seeds it: someone who adopted a shared workspace has not received the content yet,
        //and writing a title now would overwrite the real one when the sync arrives.
        if (!doc.get('meta.title') && this.#meta.ownerId === this.#host.identity.id && workspace.canCompose) workspace.setTitle(this.#meta.title);

        const me = this.#host.identity;
        doc.presence.setLocal({ user: { id: me.id, name: me.name, color: me.color, kind: me.kind }, focus: null, activity: null });

        this.#$('agora-workspace').workspace = workspace;
        this.#$('agora-inspector').workspace = workspace;
        this.#$('agora-inspector').selectedId = null;

        const showTitle = () => {
            const title = doc.get('meta.title') || this.#meta.title;
            this.#$('#title').value = title;
            document.title = `${title} — Ultima Agora`;
            if (title !== this.#meta.title) {                       //keep the catalog's copy in step with the document
                this.#host.rename(id, title).then(meta => { this.#meta = meta; return this.#refreshList(); }).catch(() => {});
            }
        };
        const applyRole = () => {
            this.#$('agora-palette').canAdd = workspace.canCompose;
            this.#$('#title').disabled = !workspace.canCompose;
            for (const button of ['#new', '#duplicate', '#archive']) this.#$(button).disabled = false;
            const role = this.#$('#role');
            role.hidden = workspace.role === 'owner';
            role.textContent = ROLE_TEXT[workspace.role] ?? workspace.role;
            this.notify(ROLE_NOTICE[workspace.role] ?? '', 'warning');
            this.#refreshHistory();
        };
        const showStatus = status => { this.#$('#status').textContent = STATUS_TEXT[status] ?? status; };
        const showPeers = () => this.#paintPeers(doc);

        showTitle(); applyRole(); showStatus(doc.status); showPeers();
        this.#cleanup.push(
            doc.onHistory(() => this.#refreshHistory()),
            doc.onChange('meta.title', showTitle),
            doc.onChange('', () => {
                clearTimeout(this.#touchTimer);
                this.#touchTimer = setTimeout(() => this.#host.touch(id).catch(() => {}), 1000);
            }),
            doc.onStatus(showStatus),
            workspace.onRole(() => { applyRole(); this.#paintPeers(doc); }),
            doc.onControl(message => {
                if (message.type === 'closed' && message.code === 4403) {
                    this.notify(`O servidor recusou alterações feitas neste dispositivo (seu papel: ${ROLE_TEXT[workspace.role] ?? workspace.role}).`, 'error', {
                        label: 'Descartar alterações locais',
                        onClick: async () => { await this.#host.discardLocal(id); location.reload(); },
                    });
                }
            }),
            doc.presence.onPeers(showPeers),
        );
        await this.#refreshList();
    }

    #refreshHistory() {
        const doc = this.#workspace?.doc;
        this.#$('#undo').disabled = !doc?.canUndo || !this.#workspace.canCompose;
        this.#$('#redo').disabled = !doc?.canRedo || !this.#workspace.canCompose;
    }

    //One chip per distinct person (a person with two tabs open counts once); you first
    #paintPeers(doc) {
        const me = this.#host.identity;
        const seen = new Map();
        for (const peer of doc.presence.peers()) if (!seen.has(peer.user.id)) seen.set(peer.user.id, peer.user);
        const chip = (user, self) => h('span', { class: `peer${self ? ' self' : ''}`, style: `background:${user.color ?? '#667'}`, title: self ? `${user.name} (você)` : user.name, 'aria-label': self ? `${user.name} (você)` : user.name },
            [...user.name.replace(/^Participante\s*/, '')][0]?.toUpperCase() ?? '?');
        this.#$('#peers').replaceChildren(...[...seen.values()].reverse().map(user => chip(user, false)), chip(me, true));
    }

    async #refreshList() {
        const select = this.#$('#workspaces');
        const list = await this.#host.list();
        select.replaceChildren(...list.map(w => h('option', { value: w.id, selected: w.id === this.#meta?.id }, w.title)));
    }
}

customElements.define('agora-shell', AgoraShell);
