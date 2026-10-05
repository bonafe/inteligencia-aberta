//Renders the workspace's docking layout and hosts the component instances (docs 07, 7.3).
//
//Structural changes (layout, instances added/removed/renamed) repaint the panel tree, reusing the live
//instance elements. Config changes go straight to the element, and content changes (notes text) never
//repaint: moving a focused textarea would blur it. Which tab is active, and which instance is selected,
//are per-user view state and stay out of the document (R-LAY-5).
import { ReactiveComponent } from '../../vendor/ultima/reactive_component.js';
import { h } from '../dom.js';
import { COMPONENT_MIME } from '../palette/agora-palette.js';

const INSTANCE_MIME = 'application/x-agora-instance';
const EDGE = 0.25;
const MENU_ITEMS = [['right', 'Dividir à direita'], ['bottom', 'Dividir abaixo'], ['left', 'Dividir à esquerda'], ['top', 'Dividir acima']];

export class AgoraWorkspaceView extends ReactiveComponent {
    #workspace = null;
    #stop = [];
    #elements = new Map();          //instanceId -> { element, body }
    #active = new Map();            //panelId -> instanceId (view state)
    #tabsSeen = new Map();          //panelId -> Set of instanceIds already shown there
    #selected = null;
    #queued = false;
    #refocus = null;                //splitter id to refocus after a repaint

    constructor() {
        super({ templateUrl: './agora-workspace.html', shadowDom: true }, import.meta.url);
    }

    get workspace() { return this.#workspace; }
    set workspace(workspace) {
        this.#stop.forEach(stop => stop());
        this.#elements.forEach((entry, id) => { entry.element?.dispose?.(); this.#workspace?.bus.detach(id); });
        this.#elements.clear();
        this.#workspace = workspace;
        this.#stop = [
            workspace.doc.onChange('', path => this.#onDocChange(path)),
            workspace.onRole(role => {
                this.#elements.forEach(entry => entry.element?.onRole?.(role));
                this.#queuePaint();                                         //move/close controls appear or disappear
            }),
            workspace.doc.presence.onPeers(() => this.#paintBadges()),
        ];
        this.#queuePaint();
    }

    get selectedId() { return this.#selected; }

    select(id) {
        if (this.#selected === id) return;
        this.#selected = id;
        this.#workspace?.doc.presence.setLocal({ focus: id });
        this.rootNode.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('selected', tab.dataset.id === id));
        this.dispatchEvent(new CustomEvent('instance-selected', { detail: { id }, bubbles: true, composed: true }));
    }

    onLoad() { super.onLoad(); this.#queuePaint(); }

    #onDocChange(path) {
        const config = /^instances\.([^.]+)\.config(\.|$)/.exec(path);
        if (config) {
            const record = this.#workspace.instance(config[1]);
            if (record) this.#elements.get(config[1])?.element?.applyConfig?.(record.config);
            return;
        }
        if (path.startsWith('content.') || path.startsWith('meta')) return;
        this.#queuePaint();
    }

    #queuePaint() {
        if (this.#queued) return;
        this.#queued = true;
        queueMicrotask(() => { this.#queued = false; this.#paint(); });
    }

    // ---- presence on the tabs: who is looking at what (5.3) ----

    #paintBadges() {
        if (!this.loaded || !this.#workspace) return;
        const peers = this.#workspace.doc.presence.peers();
        for (const tab of this.rootNode.querySelectorAll('.tab')) {
            tab.querySelectorAll('.peer-dot').forEach(dot => dot.remove());
            const here = peers.filter(peer => peer.focus === tab.dataset.id);
            tab.prepend(...here.map(peer => h('span', {
                class: 'peer-dot', style: `background:${peer.user.color ?? '#667'}`,
                title: `${peer.user.name}${peer.activity?.instance === tab.dataset.id ? ' está editando' : ' está aqui'}`,
            }, peer.activity?.instance === tab.dataset.id ? '✎' : '')));
        }
    }

    // ---- painting ----

    #paint() {
        const workspace = this.#workspace;
        if (!this.loaded || !workspace) return;
        const root = this.rootNode.querySelector('#root');

        const live = new Set(workspace.instances().map(i => i.id));
        for (const [id, entry] of this.#elements) {
            if (!live.has(id)) { entry.element?.dispose?.(); this.#workspace.bus.detach(id); this.#elements.delete(id); }
        }
        if (this.#selected && !live.has(this.#selected)) { this.#selected = null; this.dispatchEvent(new CustomEvent('instance-selected', { detail: { id: null }, bubbles: true, composed: true })); }

        const tree = workspace.layout.tree();
        root.replaceChildren(tree ? this.#buildNode(tree) : this.#emptyState());
        this.#acceptComponentDrop(root, null);

        this.#paintBadges();
        if (this.#refocus) { root.querySelector(`[data-split="${this.#refocus}"]`)?.focus(); this.#refocus = null; }
    }

    #emptyState() {
        return h('div', { class: 'empty' }, h('h2', {}, 'Workspace vazio'), h('p', {}, 'Escolha um componente na paleta (clique ou Enter) ou arraste-o até aqui.'));
    }

    #buildNode(node) {
        return node.kind === 'split' ? this.#buildSplit(node) : this.#buildTabs(node);
    }

    #buildSplit(node) {
        const [first, second] = node.children.map(child => this.#buildNode(child));
        first.style.flex = `${node.ratio} 1 0`;
        second.style.flex = `${1 - node.ratio} 1 0`;
        const splitter = h('div', {
            class: 'splitter', role: 'separator', tabindex: this.#workspace.readOnly ? null : 0, 'data-split': node.id,
            'aria-orientation': node.dir === 'row' ? 'vertical' : 'horizontal', 'aria-valuenow': Math.round(node.ratio * 100), 'aria-valuemin': 10, 'aria-valuemax': 90,
        });
        const container = h('div', { class: `split ${node.dir}` }, first, splitter, second);
        if (!this.#workspace.readOnly) this.#makeResizable(splitter, container, node, first, second);
        return container;
    }

    #makeResizable(splitter, container, node, first, second) {
        const horizontal = node.dir === 'row';
        const ratioAt = event => {
            const rect = container.getBoundingClientRect();
            return horizontal ? (event.clientX - rect.left) / rect.width : (event.clientY - rect.top) / rect.height;
        };
        let ratio = node.ratio;
        splitter.addEventListener('pointerdown', event => {
            splitter.setPointerCapture(event.pointerId);
            const move = e => {
                ratio = Math.min(0.9, Math.max(0.1, ratioAt(e)));
                first.style.flex = `${ratio} 1 0`; second.style.flex = `${1 - ratio} 1 0`;
            };
            const up = () => {
                splitter.removeEventListener('pointermove', move); splitter.removeEventListener('pointerup', up);
                this.#workspace.layout.setRatio(node.id, ratio);
            };
            splitter.addEventListener('pointermove', move);
            splitter.addEventListener('pointerup', up);
        });
        splitter.addEventListener('keydown', event => {
            const keys = horizontal ? { ArrowLeft: -0.05, ArrowRight: 0.05 } : { ArrowUp: -0.05, ArrowDown: 0.05 };
            if (!(event.key in keys)) return;
            event.preventDefault();
            this.#refocus = node.id;
            this.#workspace.layout.setRatio(node.id, node.ratio + keys[event.key]);
        });
    }

    #buildTabs(node) {
        const workspace = this.#workspace;
        const readOnly = workspace.readOnly;
        const records = node.tabs.map(id => workspace.instance(id)).filter(Boolean);
        const ids = records.map(r => r.id);

        //Active tab: first one on first sight; afterwards the newest arrival; otherwise whatever the user chose
        const seen = this.#tabsSeen.get(node.id);
        const fresh = ids.filter(id => !seen?.has(id));
        let active = this.#active.get(node.id);
        if (!seen) active = ids[0];
        else if (fresh.length) active = fresh.at(-1);
        if (!ids.includes(active)) active = ids[0];
        this.#active.set(node.id, active);
        this.#tabsSeen.set(node.id, new Set(ids));

        const panel = h('div', { class: 'panel', 'data-panel': node.id });
        const select = id => { this.#active.set(node.id, id); this.#queuePaint(); this.select(id); };

        const tabs = records.map(record => h('div', {
            class: `tab${record.id === this.#selected ? ' selected' : ''}`, role: 'presentation', 'data-id': record.id,
            draggable: !readOnly,
            onDragstart: event => { event.dataTransfer.setData(INSTANCE_MIME, record.id); event.dataTransfer.effectAllowed = 'move'; },
        },
            h('button', { class: 'label', type: 'button', role: 'tab', 'aria-selected': record.id === active ? 'true' : 'false', tabindex: record.id === active ? 0 : -1, onClick: () => select(record.id), onKeydown: event => this.#tabKeys(event, ids, record.id, select) }, record.label),
            readOnly ? null : h('button', { class: 'close', type: 'button', 'aria-label': `Fechar ${record.label}`, onClick: () => workspace.removeInstance(record.id) }, '×'),
        ));

        const bar = h('div', { class: 'tabbar' }, h('div', { class: 'tabs', role: 'tablist' }, tabs));
        if (!readOnly) {
            bar.append(h('button', { type: 'button', 'aria-label': 'Mais ações do painel', 'aria-haspopup': 'menu', onClick: () => this.#toggleMenu(bar, active) }, '⋯'));
        }
        panel.append(bar);

        for (const record of records) {
            const body = h('div', { class: 'tabpanel', role: 'tabpanel', hidden: record.id !== active, 'aria-label': record.label });
            //Every instance is mounted, the inactive tabs just hidden: outputs are derived from the live component, so a
            //source on a background tab must still produce its values for whatever is connected to it (found in M4)
            this.#mount(record, body);
            panel.append(body);
        }
        panel.addEventListener('pointerdown', () => { if (this.#active.get(node.id)) this.select(this.#active.get(node.id)); }, true);

        this.#acceptDrops(panel, node.id);
        return panel;
    }

    #tabKeys(event, ids, current, select) {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const next = ids[(ids.indexOf(current) + step + ids.length) % ids.length];
        select(next);
        queueMicrotask(() => this.rootNode.querySelector(`.tab[data-id="${next}"] button.label`)?.focus());
    }

    #toggleMenu(bar, instanceId) {
        const existing = bar.querySelector('.menu');
        if (existing) { existing.remove(); return; }
        const panelId = this.#workspace.layout.findPanelOf(instanceId);
        const close = () => menu.remove();
        const menu = h('div', { class: 'menu', role: 'menu', onKeydown: event => { if (event.key === 'Escape') { close(); bar.querySelector('button[aria-haspopup]').focus(); } } },
            ...MENU_ITEMS.map(([side, label]) => h('button', { type: 'button', role: 'menuitem', onClick: () => { close(); this.#workspace.moveInstance(instanceId, { panelId, side }); } }, label)),
            h('button', { type: 'button', role: 'menuitem', onClick: () => { close(); this.#workspace.removeInstance(instanceId); } }, 'Fechar componente'));
        bar.append(menu);
        menu.querySelector('button').focus();
    }

    // ---- instances ----

    #mount(record, body) {
        let entry = this.#elements.get(record.id);
        if (!entry) {
            entry = { element: null, body };
            this.#elements.set(record.id, entry);
            this.#create(record, entry);
        }
        entry.body = body;
        body.append(entry.element ?? h('p', { class: 'notice' }, 'Carregando…'));
    }

    async #create(record, entry) {
        const workspace = this.#workspace;
        const manifest = workspace.registry.getManifest(record.type);
        let element;
        try {
            if (!manifest) throw new Error(`O componente "${record.type}" não está instalado. Sua configuração foi preservada.`);
            await workspace.registry.load(record.type);
            element = document.createElement(manifest.tag);
            element.attach({
                instanceId: record.id, manifest, config: record.config,
                services: {
                    setConfig: patch => workspace.setConfig(record.id, patch),
                    emit: (port, value) => {
                        try { workspace.bus.publish(record.id, port, value); } catch (error) { console.error(`[${manifest.name}]`, error.message); }
                    },
                    ports: workspace.bus.portsFor(record.id),
                    domain: workspace.domain ?? undefined,
                    chat: workspace.chat ?? undefined,
                    presence: {
                        //"I am editing here" (typing); cleared by passing null
                        setActivity: kind => workspace.doc.presence.setLocal({ activity: kind ? { instance: record.id, kind } : null }),
                    },
                    shared: manifest.capabilities?.includes('shared-content') ? workspace.sharedText(record.id) : undefined,
                },
            });
        } catch (error) {
            console.error(`[agora-workspace] could not create ${record.id}:`, error);
            element = h('p', { class: 'notice error', role: 'alert' }, error.message);
        }
        if (this.#elements.get(record.id) !== entry) { element.dispose?.(); return; }       //removed while loading
        entry.element = element;
        entry.body.replaceChildren(element);
        element.onRole?.(workspace.role);
        if (element.deliverInput) workspace.bus.attach(record.id, element);
    }

    // ---- drag and drop ----

    #zone(event, target) {
        const rect = target.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width;
        const y = (event.clientY - rect.top) / rect.height;
        if (x < EDGE) return 'left';
        if (x > 1 - EDGE) return 'right';
        if (y < EDGE) return 'top';
        if (y > 1 - EDGE) return 'bottom';
        return 'center';
    }

    #accepts(event) {
        const types = event.dataTransfer?.types ?? [];
        return !this.#workspace.readOnly && (types.includes(COMPONENT_MIME) || types.includes(INSTANCE_MIME));
    }

    #acceptDrops(panel, panelId) {
        const clear = () => panel.querySelectorAll(':scope > .hint').forEach(hint => hint.remove());
        panel.addEventListener('dragover', event => {
            if (!this.#accepts(event)) return;
            event.preventDefault();
            event.stopPropagation();
            const side = this.#zone(event, panel);
            const hint = panel.querySelector(':scope > .hint');
            if (hint?.dataset.side === side) return;
            clear();
            panel.append(h('div', { class: `hint ${side}`, 'data-side': side }));
        });
        panel.addEventListener('dragleave', event => { if (!panel.contains(event.relatedTarget)) clear(); });
        panel.addEventListener('drop', event => {
            if (!this.#accepts(event)) return;
            event.preventDefault();
            event.stopPropagation();
            const side = this.#zone(event, panel);
            clear();
            const type = event.dataTransfer.getData(COMPONENT_MIME);
            const instanceId = event.dataTransfer.getData(INSTANCE_MIME);
            try {
                if (type) this.#workspace.addInstance(type, { target: { panelId, side } });
                else if (instanceId) this.#workspace.moveInstance(instanceId, { panelId, side });
            } catch (error) { console.error('[agora-workspace] drop failed:', error); }
        });
    }

    //The empty workspace has no panel to receive the drop, so the whole area does
    #acceptComponentDrop(root) {
        if (this.#workspace.layout.rootId) return;
        const area = root.querySelector('.empty');
        area.addEventListener('dragover', event => { if (this.#accepts(event)) { event.preventDefault(); area.classList.add('drop'); } });
        area.addEventListener('dragleave', () => area.classList.remove('drop'));
        area.addEventListener('drop', event => {
            event.preventDefault();
            area.classList.remove('drop');
            const type = event.dataTransfer.getData(COMPONENT_MIME);
            if (type) this.#workspace.addInstance(type);
        });
    }
}

customElements.define('agora-workspace', AgoraWorkspaceView);
