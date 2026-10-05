import { AgoraComponent } from '../../core/components/agora_component.js';

const NS = 'http://www.w3.org/2000/svg';
const WIDTH = 800, HEIGHT = 520, RADIUS = 26;
const svg = (tag, attributes = {}, ...children) => {
    const element = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attributes)) {
        if (key.startsWith('on')) element.addEventListener(key.slice(2).toLowerCase(), value); else element.setAttribute(key, value);
    }
    element.append(...children);
    return element;
};

//Deterministic layouts: nodes start on a circle; "force" then relaxes with repulsion between all nodes
//and springs along the edges (no randomness, so every participant sees the same picture).
export function layoutGraph(graph, mode) {
    const count = graph.nodes.length;
    const positions = new Map(graph.nodes.map((node, i) => {
        const angle = (2 * Math.PI * i) / Math.max(count, 1);
        return [node.id, { x: WIDTH / 2 + Math.cos(angle) * 190, y: HEIGHT / 2 + Math.sin(angle) * 190 }];
    }));
    if (mode !== 'force' || count < 3) return positions;

    const ideal = Math.sqrt((WIDTH * HEIGHT) / count) * 0.8;
    for (let step = 0; step < 150; step++) {
        const force = new Map(graph.nodes.map(node => [node.id, { x: 0, y: 0 }]));
        for (const a of graph.nodes) for (const b of graph.nodes) {
            if (a.id >= b.id) continue;
            const pa = positions.get(a.id), pb = positions.get(b.id);
            const dx = pa.x - pb.x || 0.01, dy = pa.y - pb.y || 0.01;
            const distance = Math.hypot(dx, dy);
            const push = (ideal * ideal) / distance;
            force.get(a.id).x += (dx / distance) * push; force.get(a.id).y += (dy / distance) * push;
            force.get(b.id).x -= (dx / distance) * push; force.get(b.id).y -= (dy / distance) * push;
        }
        for (const edge of graph.edges) {
            const pa = positions.get(edge.from), pb = positions.get(edge.to);
            if (!pa || !pb) continue;
            const dx = pa.x - pb.x, dy = pa.y - pb.y;
            const distance = Math.hypot(dx, dy) || 0.01;
            const pull = (distance * distance) / ideal;
            force.get(edge.from).x -= (dx / distance) * pull; force.get(edge.from).y -= (dy / distance) * pull;
            force.get(edge.to).x += (dx / distance) * pull; force.get(edge.to).y += (dy / distance) * pull;
        }
        const cooling = 12 * (1 - step / 150) + 0.5;
        for (const node of graph.nodes) {
            const f = force.get(node.id), p = positions.get(node.id);
            const magnitude = Math.hypot(f.x, f.y) || 1;
            p.x = Math.min(WIDTH - 60, Math.max(60, p.x + (f.x / magnitude) * Math.min(magnitude, cooling)));
            p.y = Math.min(HEIGHT - 40, Math.max(40, p.y + (f.y / magnitude) * Math.min(magnitude, cooling)));
        }
    }
    return positions;
}

export default class UltimaGraph extends AgoraComponent {
    #graph = null;
    #selected = null;           //node id (view state, local)

    constructor() {
        super({ templateUrl: './ultima-graph.html', shadowDom: true }, import.meta.url);
    }

    onLoad() { super.onLoad(); this.#paint(); }
    onConfig() { this.#paint(); }
    onInput(port, value) {
        if (port !== 'graph') return;
        this.#graph = value ?? null;
        this.#selected = null;
        this.#paint();
    }

    #paint() {
        if (!this.loaded) return;
        const canvas = this.rootNode.querySelector('#svg');
        const empty = !this.#graph?.nodes?.length;
        this.rootNode.querySelector('#empty').toggleAttribute('hidden', !empty);
        canvas.toggleAttribute('hidden', empty);            //SVG elements have no .hidden property: only the attribute works
        if (empty) return;

        const positions = layoutGraph(this.#graph, this.config.layout);
        const edges = this.#graph.edges.filter(e => positions.has(e.from) && positions.has(e.to)).flatMap(edge => {
            const a = positions.get(edge.from), b = positions.get(edge.to);
            const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1;
            const x2 = b.x - ((b.x - a.x) / distance) * RADIUS, y2 = b.y - ((b.y - a.y) / distance) * RADIUS;
            return [
                svg('line', { class: 'edge', x1: a.x, y1: a.y, x2, y2 }),
                edge.label ? svg('text', { class: 'edge-label', x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 4 }, edge.label) : null,
            ].filter(Boolean);
        });
        const nodes = this.#graph.nodes.map(node => {
            const { x, y } = positions.get(node.id);
            const choose = () => { this.#selected = node.id; this.#paint(); this.emit('selectedNode', { id: node.id, label: node.label ?? node.id }); };
            return svg('g', {
                class: 'node', transform: `translate(${x} ${y})`, tabindex: 0, role: 'button', 'aria-pressed': String(node.id === this.#selected),
                'aria-label': node.label ?? node.id, onClick: choose, onKeydown: event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(); } },
            }, svg('circle', { r: RADIUS }), svg('text', { y: 4 }, (node.label ?? node.id).slice(0, 12)));
        });

        canvas.replaceChildren(
            svg('defs', {}, svg('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
                svg('path', { d: 'M0 0 L10 5 L0 10 z', fill: 'currentColor', opacity: '.5' }))),
            ...edges, ...nodes);
    }

    describeContext() {
        return { nodes: this.#graph?.nodes.length ?? 0, edges: this.#graph?.edges.length ?? 0, selected: this.#selected };
    }
}
