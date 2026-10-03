/* Mapa Vivo — traduz o mesmo stream de /ws/eventos/ que o painel de eventos
 * consome em caixas nascendo/animando/conectando. Layout e física por
 * vis-network (carregado via CDN em mapa_vivo.html, sem build) — é o que dá a
 * sensação de "nó novo entrando" que uma versão SVG/CSS artesanal não tinha.
 * A tradução stage→nó (REGRAS/processarEvento) é pura e independente da
 * biblioteca de renderização. */

const ICONES = {
    navegador: '🌐', dominio: '🗂️', artifact: '📄', document_text: '📝', fragmentos: '🧩',
    estruturacao_llm: '🤖', comparacao: '⚖️', maquina: '🖥️',
};
const TAMANHOS = {
    navegador: 46, dominio: 34, artifact: 28, document_text: 20, fragmentos: 18,
    estruturacao_llm: 18, comparacao: 18, maquina: 30,
};
const TAMANHOS_FONTE = { navegador: 22, dominio: 15, maquina: 14 };
// Massa alimenta a repulsão do forceAtlas2Based (força ∝ massa₁ × massa₂):
// artifact mais pesado repele outros artifact com mais força (as capturas de
// um mesmo domínio se espalham), domínio mais pesado espalha os clusters de
// domínio entre si, e máquina (também um hub — uma só conectada a muitas
// capturas/execuções) precisa da mesma força pra não ficar espremida no meio
// do que ela processou.
const MASSAS = { artifact: 4, dominio: 3, maquina: 3 };
const NAVEGADOR_ID = 'navegador';
// Ícone fixo por tipo, para nós que não têm favicon próprio (ex.: o
// navegador, que não é uma página). Convenção do mapa: nó é imagem sempre que
// houver uma — favicon (artifact/dominio) ou ícone fixo (aqui) — o dot+emoji
// de ICONES é só o fallback para quando não há imagem nenhuma.
const IMAGENS_FIXAS = { navegador: window.MAPA_VIVO_ICONE_NAVEGADOR };
const CORES_STATUS = {
    ok: '#3fb950', vazio: '#d29922', falhou: '#f85149',
    iniciado: '#58a6ff', retentando: '#58a6ff', ignorado: '#6e7681',
};

let network, nodesDS, edgesDS, container;
const infoPorId = new Map();     // id -> {id, tipo, status, meta, label}
const edgesPendentes = [];       // {origem, destino, tipo} esperando algum lado nascer
const processando = new Set();   // ids com status iniciado/retentando (pulso)
const ordemArtifacts = [];       // ids de artifact na ordem de chegada, p/ soft cap

// ─── Posições dos nós, lembradas entre recarregamentos ─────────────────────
// Só salvar a câmera (posição/zoom) não bastava: a física do forceAtlas2Based
// parte de posições iniciais diferentes a cada carga e encontra outro
// equilíbrio, então os NÓS mudavam de lugar por baixo de uma câmera "igual".
// Aqui a gente planta cada nó na posição salva — física ainda roda, mas parte
// de perto do arranjo anterior em vez de do zero.
const CHAVE_POSICOES = 'mapaVivo.nodePositions';
let posicoesSalvas = {};
try {
    posicoesSalvas = JSON.parse(localStorage.getItem(CHAVE_POSICOES) || '{}');
} catch (err) { posicoesSalvas = {}; }

function salvarPosicoesRede() {
    if (!network) return;
    try {
        localStorage.setItem(CHAVE_POSICOES, JSON.stringify(network.getPositions()));
    } catch (err) { /* localStorage indisponível (modo privado etc.) — não é crítico */ }
}

// ─── Ponte com mapa_vivo_timeline.js ────────────────────────────────────────
// Uma captura nova (carga inicial ou evento ao vivo) notifica quem se inscreveu
// — hoje só a timeline, que não tem WebSocket próprio e não duplica esse cano.
const ouvintesNovaCaptura = [];
function notificarNovaCaptura(captura) {
    ouvintesNovaCaptura.forEach(fn => { try { fn(captura); } catch (err) { /* um ouvinte ruim não pode travar o mapa */ } });
}

function corDoStatus(status) { return CORES_STATUS[status] || CORES_STATUS.ignorado; }

function truncar(texto, max) {
    if (!texto) return '';
    return texto.length > max ? texto.slice(0, max - 1) + '…' : texto;
}

function dominioDe(url) {
    if (!url) return 'desconhecido';
    try {
        const host = new URL(url).hostname;
        return host.startsWith('www.') ? host.slice(4) : host;
    } catch (err) {
        return 'desconhecido';
    }
}

// Imagem do nó: o favicon da própria página/domínio quando existe, senão o
// ícone fixo do tipo (hoje só o navegador tem um) — null cai no dot+emoji.
function imagemDoNo(tipo, meta) {
    return (meta && meta.favicon) || IMAGENS_FIXAS[tipo] || null;
}

function rotuloNo(tipo, label, temImagem) {
    // Com imagem o ícone já é o próprio nó — o emoji na frente do rótulo
    // viraria duplicata.
    return temImagem ? truncar(label, 20) : `${ICONES[tipo] || '●'}\n${truncar(label, 20)}`;
}

// Imagem recortada em círculo lê ligeiramente menor que um dot preenchido do
// mesmo "size" — compensamos aumentando um pouco o tamanho do nó.
function tamanhoNo(tipo, imagem) {
    const base = TAMANHOS[tipo] || 18;
    return imagem ? Math.round(base * 1.3) : base;
}

function estiloNo(tipo, status, imagem) {
    const cor = corDoStatus(status);
    const base = {
        size: tamanhoNo(tipo, imagem),
        borderWidth: status === 'falhou' ? 3 : 2,
        font: { color: '#c9d1d9', size: TAMANHOS_FONTE[tipo] || 12, face: 'Inter, sans-serif', multi: false, vadjust: -2 },
        mass: MASSAS[tipo] || 1,
    };
    if (imagem) {
        // shape 'circularImage': recorta a imagem num círculo — mais "leve" que
        // o retângulo cru do shape 'image' e, com background transparente, deixa
        // o alpha do favicon aparecer de verdade (sem isso vis-network preenche
        // atrás da imagem com um azul padrão, virando a "caixa quadrada").
        return Object.assign(base, {
            shape: 'circularImage',
            image: imagem,
            color: {
                background: 'rgba(0,0,0,0)', border: cor,
                highlight: { background: 'rgba(0,0,0,0)', border: cor },
                hover: { background: 'rgba(0,0,0,0)', border: cor },
            },
        });
    }
    return Object.assign(base, {
        shape: 'dot',
        color: { background: '#161b22', border: cor, highlight: { background: '#1f2428', border: cor }, hover: { background: '#1f2428', border: cor } },
    });
}

// ─── Nós ─────────────────────────────────────────────────────────────────────
function garantirNo(id, tipo, meta, label, status) {
    let info = infoPorId.get(id);
    if (info) {
        if (meta) Object.assign(info.meta, meta);
        return info;
    }
    info = { id, tipo, status: status || 'iniciado', meta: meta || {}, label };
    infoPorId.set(id, info);
    // O navegador é o centro fixo do mapa: sem isso, o próprio hub fica à
    // deriva quando o forceAtlas2Based redistribui as capturas ao redor dele.
    const fixacao = tipo === 'navegador' ? { x: 0, y: 0, fixed: { x: true, y: true } } : {};
    const imagem = imagemDoNo(tipo, info.meta);
    const posSalva = posicoesSalvas[id];
    // fixed:true, não só x/y iniciais — testado ao vivo: sem isso, o
    // forceAtlas2Based simplesmente puxa o nó de volta pro mesmo equilíbrio de
    // sempre em poucos ciclos, e a posição salva não sobrevive nem um segundo.
    // Continua arrastável na mão (fixed não trava drag) — e um arrasto manual
    // grava a nova posição via dragEnd, então o "pino" acompanha o usuário.
    const posicaoFixa = posSalva ? { x: posSalva.x, y: posSalva.y, fixed: { x: true, y: true } } : {};
    nodesDS.add(Object.assign(
        { id, label: rotuloNo(tipo, label, !!imagem) },
        estiloNo(tipo, info.status, imagem), posicaoFixa, fixacao,
    ));
    if (tipo === 'artifact') ordemArtifacts.push(id);
    if (info.status === 'iniciado' || info.status === 'retentando') processando.add(id);
    resolverArestasPendentes();
    esconderAvisoVazio();
    aplicarSoftCap();
    return info;
}

function atualizarStatus(info, novoStatus) {
    if (!novoStatus || novoStatus === info.status) return;
    const posExplosao = novoStatus === 'falhou' ? getScreenPos(info.id) : null;
    info.status = novoStatus;
    nodesDS.update(Object.assign({ id: info.id }, estiloNo(info.tipo, novoStatus, imagemDoNo(info.tipo, info.meta))));
    if (novoStatus === 'iniciado' || novoStatus === 'retentando') processando.add(info.id);
    else processando.delete(info.id);
    if (posExplosao) explodir(posExplosao);
}

function atualizarLabel(info, label) {
    if (!label) return;
    info.label = label;
    nodesDS.update({ id: info.id, label: rotuloNo(info.tipo, label, !!imagemDoNo(info.tipo, info.meta)) });
}

// Favicon chegando depois da criação do nó (caminho ao vivo — ver
// hidratarFaviconAoVivo): troca o dot+emoji pela imagem sem esperar reload.
function atualizarImagemNo(info, imagemUrl) {
    if (!imagemUrl || info.meta.favicon === imagemUrl) return;
    info.meta.favicon = imagemUrl;
    nodesDS.update(Object.assign(
        { id: info.id, label: rotuloNo(info.tipo, info.label, true) },
        estiloNo(info.tipo, info.status, imagemUrl),
    ));
}

// Highlight vindo da timeline (mapa_vivo_timeline.js, ao clicar um item):
// centraliza a câmera no nó, seleciona (halo padrão do vis-network) e dá um
// "pop" de tamanho — feedback visual sem reaproveitar a explosão de falha.
function destacarNo(id) {
    const info = infoPorId.get(id);
    if (!info || !network || !nodesDS.get(id)) return;
    network.selectNodes([id]);
    network.focus(id, { scale: 1.5, animation: { duration: 600, easingFunction: 'easeInOutQuad' } });
    // focus() move a câmera por código, não pelo usuário — não dispara
    // dragEnd nem zoom (os únicos eventos que persistem a view hoje), então
    // sem isto o clique na timeline não sobrevive a um reload.
    network.once('animationFinished', salvarViewRede);
    const base = tamanhoNo(info.tipo, imagemDoNo(info.tipo, info.meta));
    nodesDS.update({ id, size: base + 14 });
    setTimeout(() => { if (nodesDS.get(id)) nodesDS.update({ id, size: base }); }, 450);
}

// O evento `captura.registrada` não carrega o favicon (payload de evento tem
// teto de 8 KB — não é lugar para uma imagem). O nó nasce sem imagem e busca
// aqui logo em seguida; o domínio só aceita se ainda não tiver uma (a regra de
// "primeira imagem vence" também vale no caminho ao vivo).
function hidratarFaviconAoVivo(artifactId, dominioId) {
    fetch(`/artifacts/${artifactId}/favicon/`)
        .then(r => r.ok ? r.json() : null)
        .then(data => {
            const favicon = data && data.favicon_data_uri;
            if (!favicon) return;
            const infoArtifact = infoPorId.get(artifactId);
            if (infoArtifact) atualizarImagemNo(infoArtifact, favicon);
            const infoDominio = infoPorId.get(dominioId);
            if (infoDominio && !infoDominio.meta.favicon) atualizarImagemNo(infoDominio, favicon);
        })
        .catch(() => { /* sem favicon é só isso — nunca deve virar erro visível */ });
}

// ─── Arestas ────────────────────────────────────────────────────────────────
function desenharAresta(origemId, destinoId, tipo) {
    const chave = origemId + '->' + destinoId;
    if (edgesDS.get(chave)) return;
    if (!nodesDS.get(origemId) || !nodesDS.get(destinoId)) {
        edgesPendentes.push({ origem: origemId, destino: destinoId, tipo });
        return;
    }
    // Arestas de proveniência (em que máquina algo aconteceu) ficam numa cor
    // à parte — são uma camada ortogonal à hierarquia navegador→domínio→
    // captura, não outro nível dela, e precisam ser reconhecíveis à primeira
    // vista em vez de se confundir com o resto do grafo.
    const proveniencia = tipo === 'captura_em' || tipo === 'execucao_em';
    edgesDS.add({
        id: chave, from: origemId, to: destinoId,
        dashes: tipo === 'comparacao' || tipo === 'referencia',
        color: proveniencia
            ? { color: '#8957e5', highlight: '#a371f7', hover: '#a371f7' }
            : { color: '#30363d', highlight: '#58a6ff', hover: '#58a6ff' },
        width: proveniencia ? 1 : 1.5,
    });
}

function resolverArestasPendentes() {
    for (let i = edgesPendentes.length - 1; i >= 0; i--) {
        const { origem, destino, tipo } = edgesPendentes[i];
        if (nodesDS.get(origem) && nodesDS.get(destino)) {
            edgesPendentes.splice(i, 1);
            desenharAresta(origem, destino, tipo);
        }
    }
}

// ─── Explosão (WAAPI, DOM sobre o canvas — vis-network não desenha isso) ───
function getScreenPos(id) {
    if (!network) return null;
    const pos = network.getPositions([id])[id];
    if (!pos) return null;
    const dom = network.canvasToDOM(pos);
    const rect = container.getBoundingClientRect();
    return { x: rect.left + dom.x, y: rect.top + dom.y };
}

function explodir(pos) {
    const N = 10;
    for (let i = 0; i < N; i++) {
        const ang = (Math.PI * 2 * i) / N;
        const p = document.createElement('div');
        p.className = 'particula-boom';
        p.style.left = pos.x + 'px';
        p.style.top = pos.y + 'px';
        document.body.appendChild(p);
        const dist = 38 + Math.random() * 20;
        const anim = p.animate([
            { transform: 'translate(-50%, -50%) translate(0, 0)', opacity: 1 },
            { transform: `translate(-50%, -50%) translate(${Math.cos(ang) * dist}px, ${Math.sin(ang) * dist}px)`, opacity: 0 },
        ], { duration: 520 + Math.random() * 220, easing: 'cubic-bezier(.2,.7,.3,1)' });
        anim.finished.then(() => p.remove()).catch(() => p.remove());
    }
}

// ─── Pulso dos nós em processamento (canvas não tem CSS animation) ─────────
setInterval(() => {
    if (!processando.size) return;
    const grande = Date.now() % 1300 < 650;
    const updates = [];
    processando.forEach(id => {
        const info = infoPorId.get(id);
        if (!info) return;
        const base = tamanhoNo(info.tipo, imagemDoNo(info.tipo, info.meta));
        updates.push({ id, size: grande ? base + 5 : base });
    });
    if (updates.length) nodesDS.update(updates);
}, 320);

// ─── Volume: soft cap no cliente ────────────────────────────────────────────
const SOFT_CAP = 400;
function aplicarSoftCap() {
    if (nodesDS.length <= SOFT_CAP) return;
    const excedente = ordemArtifacts.length - Math.floor(SOFT_CAP / 4);
    if (excedente <= 0) return;
    const idsOcultar = ordemArtifacts.splice(0, excedente);
    const alvo = new Set(idsOcultar);
    const remover = [];
    infoPorId.forEach((info, id) => { if (alvo.has(info.meta.artifact_id)) remover.push(id); });
    if (remover.length) {
        nodesDS.remove(remover);
        remover.forEach(id => infoPorId.delete(id));
    }
}

function esconderAvisoVazio() {
    const aviso = document.getElementById('mapa-vazio');
    if (aviso) aviso.style.display = 'none';
}

// ─── Regras: stage do evento → mutação de nó ────────────────────────────────
function nodoEstruturacao(ev, status) {
    const id = ev.payload && ev.payload.estruturacao_id;
    if (!id) return null;
    const existente = infoPorId.get(id);
    const docTextId = existente && existente.meta.document_text_id;
    const label = (ev.payload.provider && ev.payload.model_name)
        ? `${ev.payload.provider}:${ev.payload.model_name}` : undefined;
    return {
        id, tipo: 'estruturacao_llm', status, label,
        meta: { artifact_id: ev.subject_id, estruturacao_id: id,
                provider: ev.payload.provider, model_name: ev.payload.model_name },
        parentId: docTextId || ev.subject_id, arestaTipo: 'estruturacao',
    };
}

function nodoComparacao(ev, status) {
    const id = ev.payload && ev.payload.comparacao_id;
    if (!id) return null;
    return {
        id, tipo: 'comparacao', status,
        meta: { artifact_id: ev.subject_id, comparacao_id: id },
        parentId: ev.subject_id, arestaTipo: 'comparacao',
    };
}

const REGRAS = {
    'captura.registrada': ev => {
        const url = ev.payload && ev.payload.url;
        const dominio = dominioDe(url);
        const dominioId = 'dominio:' + dominio;
        const label = (ev.payload && (ev.payload.titulo || url)) || ev.subject_id;
        hidratarFaviconAoVivo(ev.subject_id, dominioId);
        notificarNovaCaptura({ id: ev.subject_id, meta: { url, dominio }, label, timestamp: ev.occurred_at });
        return [
            { id: NAVEGADOR_ID, tipo: 'navegador', status: 'ok', label: 'Navegador' },
            {
                id: dominioId, tipo: 'dominio', status: 'ok', label: dominio,
                meta: { dominio }, parentId: NAVEGADOR_ID, arestaTipo: 'agrupamento',
            },
            {
                id: ev.subject_id, tipo: 'artifact', status: 'iniciado',
                label,
                meta: { artifact_id: ev.subject_id, url, dominio },
                parentId: dominioId, arestaTipo: 'captura',
            },
        ];
    },
    // As subetapas de extração (dom2parser, trafilatura, extruct, deterministico,
    // minio, mhtml...) não têm um "extracao.falhou" único —
    // cada uma carrega seu próprio status (ok/iniciado/retentando/falhou/
    // ignorado) na etapa em que a falha de fato ocorreu (ex.: "extracao.minio"
    // com status "falhou" quando o objeto não existe no MinIO). O nó do
    // artefato reflete literalmente o status do evento — ver processarEvento.
    'extracao.concluida': ev => {
        const docId = ev.payload && ev.payload.document_text_id;
        const resultados = [{ id: ev.subject_id, tipo: 'artifact', status: 'ok' }];
        if (docId) {
            resultados.push({
                id: docId, tipo: 'document_text', status: 'ok', label: 'Texto extraído',
                meta: { artifact_id: ev.subject_id, document_text_id: docId },
                parentId: ev.subject_id, arestaTipo: 'extracao',
            });
        }
        return resultados;
    },
    'fragmentacao.concluida': ev => {
        const total = (ev.payload && ev.payload.n) || 0;
        const fragId = 'frag:' + ev.subject_id;
        return {
            id: fragId, tipo: 'fragmentos', status: total > 0 ? 'iniciado' : 'ok',
            meta: { artifact_id: (infoPorId.get(ev.subject_id) || {}).meta?.artifact_id,
                    document_text_id: ev.subject_id, total },
            parentId: ev.subject_id, arestaTipo: 'fragmentacao',
            labelFn: no => `Fragmentos (${(no.meta.indexados ? no.meta.indexados.size : 0)}/${no.meta.total || total})`,
        };
    },
    'embedding.concluido': ev => {
        const docTextId = ev.payload && ev.payload.document_text_id;
        if (!docTextId) return null;
        const fragId = 'frag:' + docTextId;
        return {
            id: fragId, tipo: 'fragmentos', status: 'ok',
            meta: { artifact_id: (infoPorId.get(docTextId) || {}).meta?.artifact_id, document_text_id: docTextId },
            parentId: docTextId, arestaTipo: 'fragmentacao',
            fragmentoIndexadoId: ev.subject_id,
            labelFn: no => `Fragmentos (${no.meta.indexados ? no.meta.indexados.size : 0}${no.meta.total ? '/' + no.meta.total : ''})`,
        };
    },
    'estruturacao_llm.solicitada': ev => nodoEstruturacao(ev, 'iniciado'),
    'estruturacao_llm.iniciada': ev => nodoEstruturacao(ev, 'iniciado'),
    'estruturacao_llm.concluida': ev => nodoEstruturacao(ev, 'ok'),
    'estruturacao_llm.vazio': ev => nodoEstruturacao(ev, 'vazio'),
    'estruturacao_llm.falhou': ev => nodoEstruturacao(ev, 'falhou'),
    'estruturacao_llm.bloqueada': ev => nodoEstruturacao(ev, 'ignorado'),
    'estruturacao_llm.cancelada': ev => nodoEstruturacao(ev, 'ignorado'),
    'comparacao.solicitada': ev => nodoComparacao(ev, 'iniciado'),
    'comparacao.iniciada': ev => nodoComparacao(ev, 'iniciado'),
    'comparacao.concluida': ev => nodoComparacao(ev, 'ok'),
    'comparacao.falhou': ev => nodoComparacao(ev, 'falhou'),
    'comparacao.bloqueada': ev => nodoComparacao(ev, 'ignorado'),
    'comparacao.cancelada': ev => nodoComparacao(ev, 'ignorado'),
};

function aplicarResultado(resultado) {
    if (!resultado || !resultado.id) return;
    let info = infoPorId.get(resultado.id);
    if (!info) {
        info = garantirNo(resultado.id, resultado.tipo, resultado.meta || {}, resultado.label || resultado.tipo, resultado.status || 'iniciado');
    } else if (resultado.meta) {
        Object.assign(info.meta, resultado.meta);
    }
    if (resultado.fragmentoIndexadoId != null) {
        info.meta.indexados = info.meta.indexados || new Set();
        info.meta.indexados.add(resultado.fragmentoIndexadoId);
    }
    if (resultado.labelFn) atualizarLabel(info, resultado.labelFn(info));
    else if (resultado.label) atualizarLabel(info, resultado.label);
    atualizarStatus(info, resultado.status);
    if (resultado.parentId) desenharAresta(resultado.parentId, resultado.id, resultado.arestaTipo || 'rel');
}

function processarEvento(ev) {
    ev.payload = ev.payload || {};

    if (ev.stage.startsWith('extracao.') && ev.stage !== 'extracao.concluida' && ev.subject_type === 'artifact') {
        aplicarResultado({ id: ev.subject_id, tipo: 'artifact', status: ev.status });
        return;
    }

    const regra = REGRAS[ev.stage];
    if (!regra) return;
    const saida = regra(ev);
    if (!saida) return;
    (Array.isArray(saida) ? saida : [saida]).forEach(aplicarResultado);
}

// ─── Carga inicial (grafo já materializado no servidor) ────────────────────
function pintarGrafoInicial(grafo) {
    (grafo.nodes || []).forEach(n => {
        if (infoPorId.has(n.id)) return;
        garantirNo(n.id, n.tipo, n.meta || {}, n.label, n.status);
        if (n.tipo === 'artifact') {
            notificarNovaCaptura({ id: n.id, meta: n.meta || {}, label: n.label, timestamp: n.created_at });
        }
    });
    (grafo.edges || []).forEach(a => desenharAresta(a.origem, a.destino, a.tipo));
}

async function fullResync() {
    try {
        const r = await fetch('/artifacts/mapa/api/v1/grafo/');
        if (r.ok) pintarGrafoInicial(await r.json());
    } catch (err) { /* próxima tentativa cobre */ }
}

// ─── WebSocket + fallback de polling (mesmo padrão de painel.html) ─────────
const vistos = new Set();
let socket = null;
let pollTimer = null;

function marcarConexao(estado, texto) {
    const el = document.getElementById('conexao');
    if (!el) return;
    el.className = 'conexao ' + estado;
    const t = document.getElementById('conexao-texto');
    if (t) t.textContent = texto;
}

function conectar() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    try {
        socket = new WebSocket(`${proto}://${location.host}/ws/eventos/`);
    } catch (err) {
        return iniciarPolling();
    }
    socket.onopen = () => { marcarConexao('ligado', 'ao vivo'); pararPolling(); };
    socket.onmessage = ev => {
        const dado = JSON.parse(ev.data);
        if (dado.tipo === 'evento') receber(dado.evento);
    };
    socket.onclose = () => {
        marcarConexao('caiu', 'reconectando…');
        iniciarPolling();
        setTimeout(conectar, 4000);
    };
    socket.onerror = () => { try { socket.close(); } catch (e) {} };
}

let ultimaSequence = 0;
function iniciarPolling() {
    if (pollTimer) return;
    marcarConexao('polling', 'atualizando a cada 3s');
    pollTimer = setInterval(buscarNovos, 3000);
    buscarNovos();
}
function pararPolling() {
    if (!pollTimer) return;
    clearInterval(pollTimer);
    pollTimer = null;
}
async function buscarNovos() {
    try {
        const r = await fetch(`/eventos/api/v1/eventos/?desde=${ultimaSequence}`);
        if (!r.ok) return;
        const dados = await r.json();
        dados.eventos.forEach(receber);
    } catch (err) { /* próximo ciclo tenta de novo */ }
}

function receber(ev) {
    if (vistos.has(ev.id)) return;
    vistos.add(ev.id);
    if (ev.sequence > ultimaSequence) ultimaSequence = ev.sequence;
    processarEvento(ev);
}

// ─── API pública p/ mapa_vivo_overlay.js (criação otimista, sem WS) ────────
window.MapaVivo = {
    criarOuAtualizarNo: aplicarResultado,
    getNo: id => infoPorId.get(id),
    contarCapturas: () => ordemArtifacts.length,
    contarCapturasDominio: dominio => {
        let n = 0;
        infoPorId.forEach(info => { if (info.tipo === 'artifact' && info.meta.dominio === dominio) n++; });
        return n;
    },
    destacarNo,
    aoNovaCaptura: fn => ouvintesNovaCaptura.push(fn),
    // Chamado após redimensionar a timeline (ver mapa_vivo_timeline.js) — o
    // canvas do vis-network não redesenha sozinho quando o container muda de
    // tamanho por CSS puro.
    redesenharRede: () => { if (network) { network.setSize('100%', '100%'); network.redraw(); } },
};

// ─── Posição/zoom do grafo, lembrados entre recarregamentos ────────────────
const CHAVE_VIEW_REDE = 'mapaVivo.networkView';

function salvarViewRede() {
    if (!network) return;
    try {
        localStorage.setItem(CHAVE_VIEW_REDE, JSON.stringify({
            position: network.getViewPosition(), scale: network.getScale(),
        }));
    } catch (err) { /* localStorage indisponível (modo privado etc.) — não é crítico */ }
}

function lerViewRedeSalva() {
    try {
        const bruto = localStorage.getItem(CHAVE_VIEW_REDE);
        return bruto ? JSON.parse(bruto) : null;
    } catch (err) { return null; }
}

// ─── Início ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    container = document.getElementById('mapa-network');
    nodesDS = new VisNetworkLib.DataSet([]);
    edgesDS = new VisNetworkLib.DataSet([]);

    const viewSalva = lerViewRedeSalva();

    network = new VisNetworkLib.Network(container, { nodes: nodesDS, edges: edgesDS }, {
        autoResize: true,
        interaction: { hover: true, dragView: true, zoomView: true, tooltipDelay: 200 },
        physics: {
            solver: 'forceAtlas2Based',
            forceAtlas2Based: { gravitationalConstant: -60, springLength: 110, springConstant: 0.09, avoidOverlap: .6 },
            // fit:true (padrão) enquadraria tudo automaticamente ao estabilizar,
            // o que apagaria a posição/zoom restaurados abaixo.
            stabilization: { iterations: 200, fit: !viewSalva },
            maxVelocity: 40,
            timestep: 0.4,
        },
        edges: { smooth: { type: 'continuous', roundness: 0.4 } },
    });

    if (viewSalva) {
        // .once: só na primeira estabilização (carga inicial) — as seguintes
        // disparam a cada nó novo chegando ao vivo e não podem reimpor a
        // posição antiga por cima de onde o usuário navegou depois.
        // (stabilizationIterationsDone não dispara com stabilization.fit:false
        // nesta versão — stabilized é o evento que realmente dispara aqui.)
        network.once('stabilized', () => {
            network.moveTo({ position: viewSalva.position, scale: viewSalva.scale, animation: false });
        });
    }

    network.on('dragEnd', salvarViewRede);
    network.on('zoom', salvarViewRede);

    // Posições: salva de novo a cada estabilização (a física roda de novo a
    // cada nó novo chegando ao vivo, então o arranjo evolui) e a cada arrasto
    // manual de nó — sem isso o usuário reposicionar algo na mão não sobrevive
    // a um reload.
    network.on('stabilized', salvarPosicoesRede);
    network.on('dragEnd', salvarPosicoesRede);

    network.on('click', params => {
        if (!params.nodes.length) return;
        const info = infoPorId.get(params.nodes[0]);
        if (!info || !window.MapaVivoOverlay) return;
        const pos = getScreenPos(info.id) || { x: window.innerWidth / 2, y: window.innerHeight / 2 };
        window.MapaVivoOverlay.abrir(Object.assign({}, info, {
            rect: () => ({ left: pos.x, top: pos.y, width: 0, height: 0 }),
        }));
    });
    network.on('hoverNode', () => { container.style.cursor = 'pointer'; });
    network.on('blurNode', () => { container.style.cursor = 'default'; });

    const inicialEl = document.getElementById('dados-iniciais');
    const inicial = inicialEl ? JSON.parse(inicialEl.textContent) : { nodes: [], edges: [] };
    pintarGrafoInicial(inicial);
    if (!inicial.nodes || !inicial.nodes.length) {
        const aviso = document.getElementById('mapa-vazio');
        if (aviso) aviso.style.display = '';
    }

    conectar();
    setInterval(fullResync, 5 * 60 * 1000);
});
