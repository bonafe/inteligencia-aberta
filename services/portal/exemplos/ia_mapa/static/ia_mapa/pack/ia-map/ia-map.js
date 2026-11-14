import { AgoraComponent } from '/static/agora/core/components/agora_component.js';

const SVG = 'http://www.w3.org/2000/svg';

//Pontos dos artefatos com coordenadas. Os dados vêm da rota de leitura da própria extensão
//(/agora/api/v1/dominio/mapa/pontos/), com a permissão de quem está olhando; sem servidor de mapas.
export default class IaMap extends AgoraComponent {
    constructor() {
        super({ templateUrl: './ia-map.html', shadowDom: true }, import.meta.url);
    }

    onLoad() { super.onLoad(); this.#paint(); }

    async #paint() {
        const root = this.rootNode;
        const status = root.querySelector('#status');
        const plot = root.querySelector('#plot');
        try {
            const data = await this.domain.get('/mapa/pontos/');
            plot.replaceChildren();
            const pontos = data.results;
            status.className = '';
            status.textContent = (pontos.length ? `${pontos.length} ponto(s)` : 'Nenhum artefato tem coordenadas.') + (data.offline ? ' — sem conexão: cópia local' : '');
            if (!pontos.length) return;
            const lats = pontos.map(p => p.lat), lons = pontos.map(p => p.lon);
            const [minLat, maxLat, minLon, maxLon] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
            const x = lon => 10 + (maxLon === minLon ? 90 : (lon - minLon) / (maxLon - minLon) * 180);
            const y = lat => 110 - (maxLat === minLat ? 50 : (lat - minLat) / (maxLat - minLat) * 100);
            for (const p of pontos) {
                const c = document.createElementNS(SVG, 'circle');
                c.setAttribute('cx', x(p.lon)); c.setAttribute('cy', y(p.lat)); c.setAttribute('r', 3);
                const t = document.createElementNS(SVG, 'title'); t.textContent = p.label; c.append(t);
                c.addEventListener('click', () => this.emit('selected', { id: p.id, label: p.label, kind: 'ponto', classification: p.classification }));
                plot.append(c);
            }
        } catch (error) {
            status.className = 'error';
            status.textContent = `Não foi possível carregar: ${error.message}`;
        }
    }
}
