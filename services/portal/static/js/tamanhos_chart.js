// Lista compacta de medidores (D3) — tamanho de cada representação que o
// pipeline de extração produz para a mesma captura (MHTML bruto → texto →
// dados estruturados), na barra lateral do visualizador. Escala logarítmica:
// MHTML bruto (megabytes) e um JSON de extruct (poucos bytes) convivem na
// mesma régua sem que o segundo vire uma barra invisível.

// Mesma ordem/hues da paleta categórica validada do design system (passo
// escuro, 8 slots). Barra sem dado (bytes null) usa var(--border) em vez de
// entrar nessa lista — sinaliza "etapa não produziu isto nesta captura".
const TAMANHOS_CORES = [
    '#3987e5', '#d95926', '#199e70', '#c98500',
    '#d55181', '#008300', '#9085e9', '#e66767',
];

function formatarBytes(n) {
    if (n == null) return '—';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function desenharGraficoTamanhos(container, dados) {
    if (!dados || !dados.length || typeof d3 === 'undefined') {
        container.innerHTML = '';
        return;
    }

    const valores = dados.filter(d => d.bytes != null).map(d => d.bytes);
    const maxVal = d3.max(valores) || 1;
    // Domínio [1, maxVal*1.1] → 0–100% de largura da faixa; barra maior nunca
    // encosta na borda direita.
    const escala = d3.scaleLog().domain([1, maxVal * 1.1]).range([2, 100]).clamp(true);

    const linhas = d3.select(container)
        .selectAll('.tam-row')
        .data(dados, d => d.chave)
        .join(enter => {
            const row = enter.append('div').attr('class', 'tam-row');
            const head = row.append('div').attr('class', 'tam-row-head');
            head.append('span').attr('class', 'tam-row-label');
            head.append('span').attr('class', 'tam-row-value');
            row.append('div').attr('class', 'tam-row-track')
                .append('div').attr('class', 'tam-row-fill');
            return row;
        });

    linhas
        .attr('class', d => `tam-row${d.bytes == null ? ' sem-dado' : ''}`)
        .attr('title', d => d.bytes != null ? `${d.label}: ${d.bytes.toLocaleString('pt-BR')} bytes` : `${d.label}: sem dado nesta captura`);

    linhas.select('.tam-row-label').text(d => d.label);
    linhas.select('.tam-row-value').text(d => formatarBytes(d.bytes));
    linhas.select('.tam-row-fill')
        .style('width', d => `${d.bytes != null ? escala(d.bytes) : 2}%`)
        .style('background-color', (d, i) => d.bytes != null ? TAMANHOS_CORES[i % TAMANHOS_CORES.length] : 'var(--border)');
}
