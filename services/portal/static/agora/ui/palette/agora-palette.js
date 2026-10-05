//Component catalog built from manifests alone (R-PAL-1). Click/Enter adds (R-PAL-2: same as dragging).
import { ReactiveComponent } from '../../vendor/ultima/reactive_component.js';
import { h } from '../dom.js';

export const COMPONENT_MIME = 'application/x-agora-component';
const CATEGORY_TITLES = { content: 'Conteúdo', visualization: 'Visualização', collaboration: 'Colaboração', intelligence: 'Inteligência' };

export class AgoraPalette extends ReactiveComponent {
    #registry = null;
    #stopListening = [];
    #canAdd = true;

    constructor() {
        super({ templateUrl: './agora-palette.html', shadowDom: true }, import.meta.url);
    }

    set registry(registry) {
        this.#stopListening.forEach(stop => stop());
        this.#registry = registry;
        const refresh = () => this.#paint();
        registry.addEventListener('registered', refresh);
        registry.addEventListener('unregistered', refresh);
        this.#stopListening = [() => registry.removeEventListener('registered', refresh), () => registry.removeEventListener('unregistered', refresh)];
        this.#paint();
    }

    //Viewers and participants cannot add components (R-PAL-5)
    set canAdd(value) { this.#canAdd = value; this.#paint(); }

    onLoad() {
        super.onLoad();
        this.rootNode.querySelector('#search').addEventListener('input', () => this.#paint());
        this.#paint();
    }

    #paint() {
        if (!this.loaded || !this.#registry) return;
        const term = this.rootNode.querySelector('#search').value;
        const list = this.rootNode.querySelector('#list');
        const manifests = this.#registry.list({ search: term });
        const byCategory = Map.groupBy(manifests, m => m.category ?? 'other');

        list.replaceChildren(...(manifests.length ? [...byCategory].flatMap(([category, items]) => [
            h('h3', {}, CATEGORY_TITLES[category] ?? category),
            ...items.map(manifest => h('button', {
                class: 'item', type: 'button', role: 'listitem', draggable: this.#canAdd, disabled: !this.#canAdd,
                title: manifest.description ?? manifest.title,
                onClick: () => this.#add(manifest.name),
                onDragstart: event => { event.dataTransfer.setData(COMPONENT_MIME, manifest.name); event.dataTransfer.effectAllowed = 'copy'; },
            }, manifest.title, manifest.description ? h('small', {}, manifest.description) : null)),
        ]) : [h('p', { class: 'empty' }, 'Nenhum componente encontrado.')]));
    }

    #add(type) {
        this.dispatchEvent(new CustomEvent('palette-add', { detail: { type }, bubbles: true, composed: true }));
    }
}

customElements.define('agora-palette', AgoraPalette);
