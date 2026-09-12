/* Trilha de etapas de uma execução do pipeline.
 *
 * Compartilhada pelo painel (lista de execuções, atualizada ao vivo) e pela
 * página de detalhe. Espera as classes .passo/.st-* de static/css/eventos.css.
 */

const MARCAS_ETAPA = {
    ok: '✓', vazio: '⚠', falhou: '✕',
    iniciado: '⏳', retentando: '↻', ignorado: '–',
};

/* Etapas que acontecem uma vez por item (um fragmento, por exemplo) contam
 * itens DISTINTOS, não eventos: o catch-up periódico redespacha tasks, e o
 * mesmo fragmento pode gerar vários eventos. Contar eventos mostrava
 * "embeddings 15" para nove fragmentos. */
const ETAPAS_CONTAVEIS = new Set(['embedding.concluido']);

function desenharTrilha(el, trilha, etapas) {
    el.innerHTML = '';
    trilha.forEach(passo => {
        const dados = etapas[passo.stage];
        const span = document.createElement('span');
        span.className = 'passo ' + (dados ? 'st-' + dados.status : 'pendente');

        const marca = document.createElement('span');
        marca.className = 'marca';
        marca.textContent = dados ? (MARCAS_ETAPA[dados.status] || '·') : '·';
        span.appendChild(marca);

        span.appendChild(document.createTextNode(passo.rotulo));

        if (dados && dados.n) {
            const n = document.createElement('span');
            n.className = 'n';
            n.textContent = dados.total ? `${dados.n}/${dados.total}` : dados.n;
            span.appendChild(n);
        }
        if (dados && dados.msg) span.title = dados.msg;
        el.appendChild(span);
    });
}

/* Aplica um evento recebido ao vivo sobre as etapas já conhecidas, com a mesma
 * regra de contagem que `apps/events/projecao.py` usa no servidor. */
function aplicarEventoNasEtapas(etapas, ev) {
    const anterior = etapas[ev.stage] || {};
    const entrada = {
        status: ev.status,
        ms: ev.duration_ms,
        msg: ev.message,
    };

    if (ETAPAS_CONTAVEIS.has(ev.stage)) {
        const itens = (anterior.itens || []).slice();
        const payload = ev.payload || {};
        const item = payload.indice !== undefined && payload.indice !== null
            ? payload.indice
            : ev.subject_id;
        if (item !== undefined && item !== null && !itens.includes(item)) {
            itens.push(item);
            // Ordenado pelo mesmo motivo que no servidor: a trilha não pode
            // depender da ordem de chegada dos eventos.
            itens.sort((a, b) => (typeof a === 'number' && typeof b === 'number')
                ? a - b : String(a).localeCompare(String(b)));
        }
        entrada.itens = itens;
        entrada.n = itens.length || (anterior.n || 0) + 1;
        if (payload.total) entrada.total = payload.total;
    } else {
        entrada.n = anterior.n;
        entrada.total = anterior.total;
    }

    etapas[ev.stage] = entrada;
    return etapas;
}
