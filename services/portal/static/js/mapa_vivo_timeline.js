/* Linha do tempo de capturas do Mapa Vivo — outra leitura do mesmo fluxo de
 * eventos que mapa_vivo.js já consome. Não abre WebSocket nem polling próprio:
 * se inscreve via window.MapaVivo.aoNovaCaptura (carga inicial + tempo real)
 * para não duplicar esse cano. Clicar um item dá highlight no nó equivalente
 * do grafo via window.MapaVivo.destacarNo. */

(function () {
    // vis-timeline monta dentro deste filho, não do próprio #mapa-timeline —
    // a lib força position:relative inline no container passado ao construtor,
    // o que anularia o position:absolute/bottom:0 que ancora o pai no rodapé.
    const container = document.getElementById('mapa-timeline-montagem');
    if (!container || !window.VisTimelineLib) return;

    function truncar(texto, max) {
        if (!texto) return '';
        return texto.length > max ? texto.slice(0, max - 1) + '…' : texto;
    }

    // Nada de nomes de mês/dia da semana nos rótulos: o bundle standalone do
    // vis-timeline (8.5.4, deprecated) não empacota locale pt-br do moment
    // (só de/en/es/fr/it/ja/nl/pl/ru/tr/uk), e passar funções em vez de string
    // nos tokens de format faz essa versão quebrar com "t.replace is not a
    // function" (o validador interno assume string). Tokens só numéricos
    // (DD/MM/YYYY/HH/mm) não precisam de locale nenhum — funcionam iguais em
    // qualquer idioma porque não têm palavra para traduzir.
    const format = {
        minorLabels: { weekday: 'DD/MM', month: 'MM/YY' },
        majorLabels: {
            second: 'DD/MM HH:mm',
            minute: 'DD/MM/YYYY',
            hour: 'DD/MM/YYYY',
            weekday: 'MM/YYYY',
            day: 'MM/YYYY',
            week: 'MM/YYYY',
        },
    };

    // Janela inicial: a que o usuário deixou salva, senão hoje. Sem start/end
    // o vis-timeline faz fit() sozinho e abre já mostrando tudo — aqui ele
    // abre onde parou (ou no dia atual), e dar zoom out continua livre (sem
    // min/max) até enxergar a história inteira.
    const CHAVE_RANGE = 'mapaVivo.timelineRange';

    function lerRangeSalvo() {
        try {
            const bruto = localStorage.getItem(CHAVE_RANGE);
            if (!bruto) return null;
            const { start, end } = JSON.parse(bruto);
            return { start: new Date(start), end: new Date(end) };
        } catch (err) { return null; }
    }

    function salvarRange() {
        try {
            const janela = timeline.getWindow();
            localStorage.setItem(CHAVE_RANGE, JSON.stringify({
                start: janela.start.toISOString(), end: janela.end.toISOString(),
            }));
        } catch (err) { /* localStorage indisponível (modo privado etc.) */ }
    }

    const inicioHoje = new Date(); inicioHoje.setHours(0, 0, 0, 0);
    const fimHoje = new Date(inicioHoje.getTime() + 24 * 60 * 60 * 1000);
    const janelaInicial = lerRangeSalvo() || { start: inicioHoje, end: fimHoje };

    const itens = new VisTimelineLib.DataSet([]);
    const timeline = new VisTimelineLib.Timeline(container, itens, {
        height: '100%',
        stack: true,
        zoomable: true,
        moveable: true,
        showCurrentTime: true,
        margin: { item: 8, axis: 12 },
        tooltip: { followMouse: true },
        format,
        start: janelaInicial.start,
        end: janelaInicial.end,
    });

    timeline.on('rangechanged', salvarRange);

    function adicionarItem(captura) {
        if (!captura || !captura.id || !captura.timestamp) return;
        const url = captura.meta && captura.meta.url;
        const dado = {
            id: captura.id,
            start: captura.timestamp,
            content: truncar(captura.label || url || captura.id, 28),
            title: url || captura.label || '',
        };
        if (itens.get(captura.id)) itens.update(dado);
        else itens.add(dado);
    }

    timeline.on('select', props => {
        if (!props.items.length) return;
        if (window.MapaVivo && window.MapaVivo.destacarNo) window.MapaVivo.destacarNo(props.items[0]);
    });

    // A carga inicial (nós já existentes no servidor) e todo evento
    // `captura.registrada` ao vivo passam por aqui — ver notificarNovaCaptura
    // em mapa_vivo.js.
    if (window.MapaVivo && window.MapaVivo.aoNovaCaptura) {
        window.MapaVivo.aoNovaCaptura(adicionarItem);
    }

    // ─── Redimensionar arrastando a alça ────────────────────────────────────
    // A altura vive em --mapa-timeline-h (mapa_vivo.css): grafo, timeline e
    // alça leem a mesma variável, então só precisamos escrevê-la.
    const alca = document.getElementById('mapa-resize');
    const raiz = document.documentElement;
    const ALTURA_MIN = 70;

    function alturaMaxima() {
        return Math.max(window.innerHeight - 250, ALTURA_MIN);
    }

    function alturaAtual() {
        return parseInt(getComputedStyle(raiz).getPropertyValue('--mapa-timeline-h'), 10) || 130;
    }

    function definirAltura(px) {
        const altura = Math.min(Math.max(px, ALTURA_MIN), alturaMaxima());
        raiz.style.setProperty('--mapa-timeline-h', altura + 'px');
        try { localStorage.setItem('mapaVivo.timelineH', altura); } catch (err) { /* modo privado etc. */ }
        timeline.redraw();
        if (window.MapaVivo && window.MapaVivo.redesenharRede) window.MapaVivo.redesenharRede();
    }

    if (alca) {
        let arrastando = false;
        alca.addEventListener('pointerdown', ev => {
            arrastando = true;
            alca.classList.add('arrastando');
            alca.setPointerCapture(ev.pointerId);
            ev.preventDefault();
        });
        alca.addEventListener('pointermove', ev => {
            if (!arrastando) return;
            definirAltura(window.innerHeight - ev.clientY);
        });
        const soltar = () => { arrastando = false; alca.classList.remove('arrastando'); };
        alca.addEventListener('pointerup', soltar);
        alca.addEventListener('pointercancel', soltar);
        // Só reencaixa a altura atual dentro dos novos limites — não força o máximo.
        window.addEventListener('resize', () => definirAltura(alturaAtual()));
    }
})();
