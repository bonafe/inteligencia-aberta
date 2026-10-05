// Service worker do Ultima Agora: o "app shell" funciona sem rede depois do primeiro acesso (offline-first).
// Mesma ideia do nossotreino (pré-cache do app + "rede primeiro, cache como reserva"), com duas diferenças:
//   - a lista de arquivos vem de um precache.json GERADO (tools/gerar_precache.py), nunca escrita à mão; a versão
//     do cache é o hash do conteúdo, então um deploy novo troca o cache sozinho;
//   - serve a hospedeiros diferentes: parametrizado pela query de registro (?precache=URL&shell=URL).
//
// O que ele NÃO faz, de propósito: não intercepta a API (/agora/api/), o WebSocket nem o sync. Dados de domínio
// têm política própria de cache local (nível de classificação), decidida pelo app, não por um service worker.

const parametros = new URL(self.location.href).searchParams;
const PRECACHE = new URL(parametros.get('precache') || 'precache.json', self.location.href);
const SHELL = parametros.get('shell') ? new URL(parametros.get('shell'), self.location.href).href : null;     // página mostrada quando uma navegação falha
const PREFIXO_CACHE = 'agora-shell-';
const ESPERA_REDE_MS = 4000;                                                                                // rede lenta ("lie-fi") não pode travar o app

let nomeDoCache = null;
const cacheAtual = async () => {
    if (!nomeDoCache) {
        const nomes = (await caches.keys()).filter(n => n.startsWith(PREFIXO_CACHE)).sort();
        nomeDoCache = nomes.at(-1) ?? `${PREFIXO_CACHE}vazio`;
    }
    return caches.open(nomeDoCache);
};

self.addEventListener('install', evento => {
    evento.waitUntil((async () => {
        const lista = await (await fetch(PRECACHE, { cache: 'reload' })).json();
        const cache = await caches.open(`${PREFIXO_CACHE}${lista.version}`);
        // um por um: cache.addAll é tudo-ou-nada, e uma falha isolada não deve derrubar o pré-cache inteiro
        await Promise.allSettled([...lista.files, ...(SHELL ? [SHELL] : [])].map(async arquivo => {
            const url = new URL(arquivo, PRECACHE);
            const resposta = await fetch(url, { cache: 'reload' });
            if (!resposta.ok) throw new Error(`${resposta.status} ${url}`);
            await cache.put(url, resposta);
        }));
        nomeDoCache = `${PREFIXO_CACHE}${lista.version}`;
    })());
    self.skipWaiting();
});

self.addEventListener('activate', evento => {
    evento.waitUntil((async () => {
        const lista = await (await fetch(PRECACHE).catch(() => null))?.json().catch(() => null);
        const atual = lista ? `${PREFIXO_CACHE}${lista.version}` : null;
        if (atual) {
            nomeDoCache = atual;
            for (const nome of await caches.keys()) if (nome.startsWith(PREFIXO_CACHE) && nome !== atual) await caches.delete(nome);
        }
        await self.clients.claim();
    })());
});

const daRedeComLimite = (requisicao) => new Promise((resolve, reject) => {
    const temporizador = setTimeout(() => reject(new Error('rede lenta')), ESPERA_REDE_MS);
    //'no-cache': revalidate with the server (a cheap 304 when nothing changed). Without it the browser's HTTP cache may answer
    //"network first" with an old copy, and an updated app would keep running the previous version.
    fetch(requisicao, { cache: 'no-cache' }).then(resposta => { clearTimeout(temporizador); resolve(resposta); }, erro => { clearTimeout(temporizador); reject(erro); });
});

self.addEventListener('fetch', evento => {
    const requisicao = evento.request;
    const url = new URL(requisicao.url);
    if (requisicao.method !== 'GET' || url.origin !== self.location.origin) return;
    if (url.pathname.includes('/api/') || url.pathname.includes('/agora-sync/')) return;             // dados: o app decide

    evento.respondWith((async () => {
        const cache = await cacheAtual();
        try {
            const resposta = await daRedeComLimite(requisicao);
            if (resposta.ok && resposta.type === 'basic' && requisicao.mode !== 'navigate') cache.put(requisicao, resposta.clone());
            return resposta;
        } catch (erro) {
            const guardada = await cache.match(requisicao, { ignoreSearch: requisicao.mode === 'navigate' });
            if (guardada) return guardada;
            if (requisicao.mode === 'navigate' && SHELL) return (await cache.match(SHELL)) ?? Response.error();
            return Response.error();
        }
    })());
});
