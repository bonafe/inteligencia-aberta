//Docking layout: a binary tree of panels stored in the workspace document (R-LAY-1..3, D-23).
//
//  doc.layout.root            id of the root panel
//  doc.layout.panels.<id>     { kind: 'tabs',  tabs: [instanceId, ...] }
//                             { kind: 'split', dir: 'row' | 'column', ratio: 0..1, children: [panelId, panelId] }
//
//The active tab and maximized panel are per-user view state, so they are NOT in the document (R-LAY-5).
//Every operation runs in one doc.transact(), so each is a single undo step.

export const SIDES = ['center', 'left', 'right', 'top', 'bottom'];
const MIN_RATIO = 0.1;

import { uuid } from '../ids.js';
const newId = () => `p-${uuid().slice(0, 8)}`;

export class Layout {
    #doc;
    constructor(doc) { this.#doc = doc; }

    get rootId() { return this.#doc.get('layout.root') ?? null; }
    panel(id) { return this.#doc.get(['layout', 'panels', id]) ?? null; }
    #setPanel(id, panel) { this.#doc.set(['layout', 'panels', id], panel); }

    //Nested view of the tree for rendering: tabs panels carry `tabs`, splits carry `children` (resolved).
    tree(id = this.rootId) {
        const panel = id && this.panel(id);
        if (!panel) return null;
        return panel.kind === 'split' ? { id, ...panel, children: panel.children.map(child => this.tree(child)) } : { id, ...panel };
    }

    panelIds() { return Object.keys(this.#doc.get('layout.panels') ?? {}); }

    findPanelOf(instanceId) {
        return this.panelIds().find(id => { const p = this.panel(id); return p.kind === 'tabs' && p.tabs.includes(instanceId); }) ?? null;
    }

    parentOf(panelId) {
        return this.panelIds().find(id => { const p = this.panel(id); return p.kind === 'split' && p.children.includes(panelId); }) ?? null;
    }

    instanceIds() {
        return this.panelIds().flatMap(id => { const p = this.panel(id); return p.kind === 'tabs' ? p.tabs : []; });
    }

    //Leftmost/topmost tabs panel: where new instances go when nothing else is specified.
    defaultPanel() {
        let id = this.rootId;
        while (id) { const p = this.panel(id); if (p.kind === 'tabs') return id; id = p.children[0]; }
        return null;
    }

    //Puts an instance in the layout. side 'center' adds a tab to the target panel; other sides split it.
    place(instanceId, { panelId = null, side = 'center' } = {}) {
        if (!SIDES.includes(side)) throw new Error(`layout.place: invalid side "${side}"`);
        return this.#doc.transact(`place ${instanceId}`, () => {
            const target = panelId ?? this.defaultPanel();
            if (!target) {                                              //empty workspace
                const id = newId();
                this.#setPanel(id, { kind: 'tabs', tabs: [instanceId] });
                this.#doc.set('layout.root', id);
                return id;
            }
            const targetPanel = this.panel(target);
            if (targetPanel?.kind !== 'tabs') throw new Error(`layout.place: panel "${target}" is not a tabs panel`);
            if (side === 'center') {
                this.#setPanel(target, { ...targetPanel, tabs: [...targetPanel.tabs.filter(t => t !== instanceId), instanceId] });
                return target;
            }
            const parentId = this.parentOf(target);                      //before the split exists, or it becomes the parent
            const created = newId();
            this.#setPanel(created, { kind: 'tabs', tabs: [instanceId] });
            const splitId = newId();
            const first = side === 'left' || side === 'top';
            this.#setPanel(splitId, {
                kind: 'split', dir: side === 'left' || side === 'right' ? 'row' : 'column', ratio: 0.5,
                children: first ? [created, target] : [target, created],
            });
            this.#replaceChild(parentId, target, splitId);
            return created;
        });
    }

    //Takes the instance out of its panel, collapsing panels that become empty.
    remove(instanceId) {
        this.#doc.transact(`remove ${instanceId}`, () => this.#detach(instanceId));
    }

    #detach(instanceId) {
        const id = this.findPanelOf(instanceId);
        if (!id) return;
        const panel = this.panel(id);
        const tabs = panel.tabs.filter(t => t !== instanceId);
        if (tabs.length) this.#setPanel(id, { ...panel, tabs }); else this.#removePanel(id);
    }

    #removePanel(id) {
        const parentId = this.parentOf(id);
        if (!parentId) { this.#doc.delete('layout.root'); }
        else {
            const sibling = this.panel(parentId).children.find(c => c !== id);
            this.#replaceInParent(parentId, sibling);
            this.#doc.delete(['layout', 'panels', parentId]);
        }
        this.#doc.delete(['layout', 'panels', id]);
    }

    //Makes `replacement` take the place that `oldId` has in the tree (root or a split's child slot).
    #replaceInParent(oldId, replacement) { this.#replaceChild(this.parentOf(oldId), oldId, replacement); }

    #replaceChild(parentId, oldId, replacement) {
        if (!parentId) { this.#doc.set('layout.root', replacement); return; }
        const parent = this.panel(parentId);
        this.#setPanel(parentId, { ...parent, children: parent.children.map(c => c === oldId ? replacement : c) });
    }

    //Moves an instance to another panel/side. Dropping the only tab of a panel onto its own edge is a no-op.
    move(instanceId, { panelId, side = 'center' }) {
        const source = this.findPanelOf(instanceId);
        if (!source) throw new Error(`layout.move: "${instanceId}" is not in the layout`);
        const sourcePanel = this.panel(source);
        if (panelId === source && (side !== 'center' ? sourcePanel.tabs.length === 1 : true)) return;
        this.#doc.transact(`move ${instanceId}`, () => {
            this.#detach(instanceId);
            this.place(instanceId, { panelId, side });
        });
    }

    reorderTab(panelId, instanceId, index) {
        const panel = this.panel(panelId);
        if (panel?.kind !== 'tabs' || !panel.tabs.includes(instanceId)) return;
        const tabs = panel.tabs.filter(t => t !== instanceId);
        tabs.splice(Math.max(0, Math.min(index, tabs.length)), 0, instanceId);
        this.#setPanel(panelId, { ...panel, tabs });
    }

    setRatio(splitId, ratio) {
        const panel = this.panel(splitId);
        if (panel?.kind !== 'split') return;
        this.#setPanel(splitId, { ...panel, ratio: Math.min(1 - MIN_RATIO, Math.max(MIN_RATIO, ratio)) });
    }
}
