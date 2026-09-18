/* Painel de detalhe do Mapa Vivo — reaproveita os endpoints que a galeria de
 * capturas já usa (ServeMHTMLView, ArtifactContentView, listas de execuções
 * de estruturação e de comparação), sem endpoint de conteúdo novo. */

(function () {
    const overlay = document.getElementById('overlay-detalhe');
    const conteudo = document.getElementById('overlay-conteudo');
    const elTitulo = document.getElementById('overlay-titulo');
    const elSub = document.getElementById('overlay-sub');
    const elTabs = document.getElementById('overlay-tabs');
    const elPaineis = document.getElementById('overlay-paineis');
    const btnFechar = document.getElementById('overlay-fechar');

    const EM_ANDAMENTO = new Set(['pendente', 'executando']);
    let selecaoAtual = null; // {tipo, id} do nó aberto, p/ evitar corrida entre cliques

    function abrir(no) {
        if (!no) return;
        selecaoAtual = { tipo: no.tipo, id: no.id };

        const rect = no.rect();
        overlay.style.setProperty('--origem-x', (rect.left + rect.width / 2) + 'px');
        overlay.style.setProperty('--origem-y', (rect.top + rect.height / 2) + 'px');

        elTitulo.textContent = no.label || no.tipo;
        elSub.textContent = '';
        elTabs.innerHTML = '';
        elPaineis.innerHTML = '';

        overlay.hidden = false;
        requestAnimationFrame(() => overlay.classList.add('aberto'));

        const montadores = {
            navegador: montarPainelNavegador,
            dominio: montarPainelDominio,
            artifact: montarPainelArtifact,
            document_text: montarPainelDocumentText,
            fragmentos: montarPainelFragmentos,
            estruturacao_llm: montarPainelEstruturacao,
            comparacao: montarPainelComparacao,
            maquina: montarPainelMaquina,
        };
        (montadores[no.tipo] || montarPainelGenerico)(no);
    }

    function fechar() {
        overlay.classList.remove('aberto');
        setTimeout(() => { overlay.hidden = true; }, 380);
        selecaoAtual = null;
    }

    btnFechar.addEventListener('click', fechar);
    overlay.addEventListener('click', ev => { if (ev.target === overlay) fechar(); });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !overlay.hidden) fechar(); });

    function adicionarTab(nome, rotulo, ativa) {
        const btn = document.createElement('button');
        btn.className = 'overlay-tab' + (ativa ? ' active' : '');
        btn.textContent = rotulo;
        btn.onclick = () => {
            elTabs.querySelectorAll('.overlay-tab').forEach(t => t.classList.remove('active'));
            elPaineis.querySelectorAll('.overlay-painel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('painel-' + nome).classList.add('active');
        };
        elTabs.appendChild(btn);

        const painel = document.createElement('div');
        painel.className = 'overlay-painel' + (ativa ? ' active' : '');
        painel.id = 'painel-' + nome;
        elPaineis.appendChild(painel);
        return painel;
    }

    // ─── navegador ──────────────────────────────────────────────────────────
    function montarPainelNavegador(no) {
        elSub.textContent = 'Toda captura MHTML da extensão Chrome entra por aqui';
        const painel = adicionarTab('resumo', 'Resumo', true);
        const total = (window.MapaVivo && window.MapaVivo.contarCapturas) ? window.MapaVivo.contarCapturas() : 0;
        const card = document.createElement('div');
        card.className = 'overlay-card st-ok';
        card.textContent = `${total} captura(s) MHTML recebida(s) da extensão.`;
        painel.appendChild(card);
    }

    // ─── maquina ────────────────────────────────────────────────────────────
    // Detalhe de recursos/modelos fica na tela dedicada (/cluster/) — aqui só
    // o essencial de "que máquina é esta", com um link pra lá.
    function montarPainelMaquina(no) {
        elSub.textContent = 'Máquina do cluster — capturou e/ou processou o que está ligado a ela no mapa';
        const painel = adicionarTab('resumo', 'Resumo', true);
        const card = document.createElement('div');
        card.className = 'overlay-card st-ok';
        card.textContent = `Apelido: ${no.meta.apelido || no.label}`;
        painel.appendChild(card);
        const link = document.createElement('a');
        link.href = '/cluster/';
        link.textContent = 'Ver status e capacidade desta máquina →';
        link.style.cssText = 'display:block;margin-top:.75rem;color:#58a6ff;';
        painel.appendChild(link);
    }

    // ─── dominio ────────────────────────────────────────────────────────────
    function montarPainelDominio(no) {
        const dominio = no.meta.dominio || no.label;
        elSub.textContent = 'Todas as capturas MHTML deste domínio';
        const painel = adicionarTab('resumo', 'Resumo', true);
        const total = (window.MapaVivo && window.MapaVivo.contarCapturasDominio) ? window.MapaVivo.contarCapturasDominio(dominio) : 0;
        const card = document.createElement('div');
        card.className = 'overlay-card st-ok';
        card.textContent = `${total} captura(s) de ${dominio}.`;
        painel.appendChild(card);
    }

    // ─── artifact ───────────────────────────────────────────────────────────
    function montarPainelArtifact(no) {
        elSub.textContent = no.meta.url || '';
        const painelMhtml = adicionarTab('mhtml', 'Página capturada', true);
        const iframe = document.createElement('iframe');
        iframe.className = 'overlay-iframe';
        // sandbox="" (mesma trava do visualizador em gallery.html): a página
        // capturada é HTML de terceiros e não pode navegar a aba, abrir popup
        // nem rodar o próprio JS a partir daqui.
        iframe.setAttribute('sandbox', '');
        iframe.src = `/artifacts/${no.id}/mhtml/`;
        painelMhtml.appendChild(iframe);

        const painelDados = adicionarTab('dados', 'Dados extraídos', false);
        painelDados.textContent = 'Carregando…';
        fetch(`/artifacts/${no.id}/content/`)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (!data || selecaoAtual?.id !== no.id) return;
                painelDados.innerHTML = '';
                painelDados.appendChild(resumoConteudo(data));
            })
            .catch(() => { painelDados.textContent = 'Texto ainda não extraído.'; });
    }

    function resumoConteudo(data) {
        const wrap = document.createElement('div');
        const info = document.createElement('div');
        info.className = 'overlay-sub';
        const partes = [];
        if (data.page_type) partes.push(`tipo: ${data.page_type}`);
        if (data.detection_confidence != null) partes.push(`${Math.round(data.detection_confidence * 100)}% confiança`);
        if (data.word_count) partes.push(`${data.word_count.toLocaleString('pt-BR')} palavras`);
        info.textContent = partes.join(' · ');
        wrap.appendChild(info);
        if (data.structured_data) {
            const tree = document.createElement('div');
            tree.appendChild(renderJsonNode(data.structured_data));
            wrap.appendChild(tree);
        } else if (data.text) {
            const pre = document.createElement('div');
            pre.className = 'overlay-texto';
            pre.textContent = data.text.slice(0, 4000);
            wrap.appendChild(pre);
        }
        return wrap;
    }

    // ─── document_text ──────────────────────────────────────────────────────
    function montarPainelDocumentText(no) {
        const artifactId = no.meta.artifact_id;
        elSub.textContent = 'Texto e dados extraídos, um painel por tipo de extração';
        const painelTexto = adicionarTab('texto', 'Texto', true);
        painelTexto.textContent = 'Carregando…';

        fetch(`/artifacts/${artifactId}/content/`)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (!data || selecaoAtual?.id !== no.id) return;
                no.meta.doc = data;
                painelTexto.innerHTML = '';
                const pre = document.createElement('div');
                pre.className = 'overlay-texto';
                pre.textContent = data.text || '(sem texto)';
                painelTexto.appendChild(pre);

                // Cada estratégia de extração de structured_data fica no seu próprio
                // painel — dom2parser, extruct e o extrator determinístico rodam
                // sempre, sem fallback silencioso entre eles. "Dados Estruturados
                // (final)" é o campo que o resto do app consumiria, mas fica vazio
                // até existir um processo que decida entre as estratégias.
                const extracoes = [
                    { chave: 'final', rotulo: 'Dados Estruturados (final)', dados: data.structured_data },
                    { chave: 'dom2parser', rotulo: 'DOM2Parser', dados: data.dados_estruturados_dom2parser },
                    { chave: 'extruct', rotulo: 'Extruct', dados: data.dados_estruturados_extruct },
                    { chave: 'deterministico', rotulo: `Determinístico · ${data.extractor_version || '?'}`,
                      dados: data.dados_estruturados_deterministico },
                ];
                extracoes.forEach(ex => {
                    if (!ex.dados) return;
                    const painel = adicionarTab('extracao-' + ex.chave, ex.rotulo, false);
                    painel.appendChild(renderJsonNode(ex.dados));
                });
                if (!extracoes.some(ex => ex.dados)) {
                    adicionarTab('extracao-vazia', 'Dados Estruturados', false).textContent = 'Nada estruturado ainda.';
                }
            })
            .catch(() => { painelTexto.textContent = 'Erro ao carregar.'; })
            .finally(() => {
                if (selecaoAtual?.id !== no.id) return;
                montarFormEstruturar(adicionarTab('estruturar', 'Estruturar com LLM', false), artifactId, no);
            });
    }

    function montarFormEstruturar(painel, artifactId, no) {
        const providerSel = document.createElement('select');
        ['ollama', 'anthropic'].forEach(p => {
            const opt = document.createElement('option'); opt.value = p; opt.textContent = p;
            providerSel.appendChild(opt);
        });
        const modelInput = document.createElement('input');
        modelInput.type = 'text';
        modelInput.placeholder = 'modelo (ex.: llama3.1:8b)';
        modelInput.style.cssText = 'margin-left:.5rem;padding:.35rem .5rem;background:var(--bg-sunken);border:1px solid var(--border);color:var(--text-main);border-radius:6px;';
        providerSel.style.cssText = 'padding:.35rem .5rem;background:var(--bg-sunken);border:1px solid var(--border);color:var(--text-main);border-radius:6px;';

        const btn = document.createElement('button');
        btn.className = 'btn';
        btn.textContent = 'Estruturar com LLM';
        btn.style.marginLeft = '.5rem';
        btn.onclick = () => {
            if (!modelInput.value.trim()) { modelInput.focus(); return; }
            btn.disabled = true;
            fetch(`/artifacts/${artifactId}/estruturar-llm/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.MAPA_VIVO_CSRF },
                body: JSON.stringify({ provider: providerSel.value, model_name: modelInput.value.trim() }),
            })
                .then(r => r.json())
                .then(data => {
                    if (data.estruturacao_id && window.MapaVivo) {
                        window.MapaVivo.criarOuAtualizarNo({
                            id: data.estruturacao_id, tipo: 'estruturacao_llm', status: 'iniciado',
                            label: `${providerSel.value}:${modelInput.value.trim()}`,
                            meta: { artifact_id: artifactId, document_text_id: no.meta.document_text_id,
                                    estruturacao_id: data.estruturacao_id,
                                    provider: providerSel.value, model_name: modelInput.value.trim() },
                            parentId: no.id, arestaTipo: 'estruturacao',
                        });
                    }
                })
                .finally(() => { btn.disabled = false; });
        };

        painel.append(providerSel, modelInput, btn);
    }

    // ─── fragmentos ─────────────────────────────────────────────────────────
    function montarPainelFragmentos(no) {
        elSub.textContent = 'Fragmentos indexados no Qdrant';
        const painel = adicionarTab('resumo', 'Resumo', true);
        const card = document.createElement('div');
        card.className = 'overlay-card st-ok';
        card.textContent = `${no.meta.indexados ? no.meta.indexados.size : (no.meta.count || 0)} fragmento(s) indexado(s)${no.meta.total ? ` de ${no.meta.total}` : ''}.`;
        painel.appendChild(card);
    }

    // ─── estruturacao_llm ───────────────────────────────────────────────────
    function montarPainelEstruturacao(no) {
        const artifactId = no.meta.artifact_id;
        elSub.textContent = no.label || '';
        const painel = adicionarTab('resultado', 'Resultado', true);
        painel.textContent = 'Carregando…';

        function carregar() {
            fetch(`/artifacts/${artifactId}/estruturacoes/`)
                .then(r => r.json())
                .then(data => {
                    if (selecaoAtual?.id !== no.id) return;
                    const execucao = (data.execucoes || []).find(e => e.id === no.meta.estruturacao_id);
                    painel.innerHTML = '';
                    if (!execucao) { painel.textContent = 'Execução não encontrada.'; return; }
                    painel.appendChild(cartaoEstruturacao(execucao, artifactId, carregar));
                    if (EM_ANDAMENTO.has(execucao.status)) setTimeout(carregar, 3000);
                })
                .catch(() => { painel.textContent = 'Erro ao carregar.'; });
        }
        carregar();
    }

    function cartaoEstruturacao(e, artifactId, aoAtualizar) {
        const card = document.createElement('div');
        card.className = 'overlay-card st-' + (e.status === 'concluido' ? 'ok' : e.status === 'falhou' ? 'falhou' : (EM_ANDAMENTO.has(e.status) ? 'iniciado' : 'vazio'));
        const header = document.createElement('div');
        header.textContent = `${e.provider}:${e.model_name} — ${e.status}${e.duration_ms ? ` (${Math.round(e.duration_ms / 1000)}s)` : ''}`;
        card.appendChild(header);
        if (EM_ANDAMENTO.has(e.status)) {
            const btn = document.createElement('button');
            btn.className = 'btn'; btn.textContent = 'Parar'; btn.style.marginTop = '.5rem';
            btn.onclick = () => fetch(`/artifacts/${artifactId}/estruturacoes/${e.id}/cancelar/`, {
                method: 'POST', headers: { 'X-CSRFToken': window.MAPA_VIVO_CSRF },
            }).then(aoAtualizar);
            card.appendChild(btn);
        }
        if (e.error_message) {
            const err = document.createElement('div');
            err.style.color = 'var(--falhou)'; err.style.marginTop = '.4rem'; err.style.fontSize = '.82rem';
            err.textContent = e.error_message;
            card.appendChild(err);
        }
        if (e.structured_data) {
            const tree = document.createElement('div');
            tree.style.marginTop = '.5rem';
            tree.appendChild(renderJsonNode(e.structured_data));
            card.appendChild(tree);
        }
        return card;
    }

    // ─── comparacao ─────────────────────────────────────────────────────────
    function montarPainelComparacao(no) {
        const artifactId = no.meta.artifact_id;
        elSub.textContent = no.label || '';
        const painel = adicionarTab('veredito', 'Veredito', true);
        painel.textContent = 'Carregando…';

        function carregar() {
            fetch(`/artifacts/${artifactId}/comparacoes/`)
                .then(r => r.json())
                .then(data => {
                    if (selecaoAtual?.id !== no.id) return;
                    const comparacao = (data.comparacoes || []).find(c => c.id === no.meta.comparacao_id);
                    painel.innerHTML = '';
                    if (!comparacao) { painel.textContent = 'Comparação não encontrada.'; return; }
                    painel.appendChild(cartaoComparacao(comparacao, artifactId, carregar));
                    if (EM_ANDAMENTO.has(comparacao.status)) setTimeout(carregar, 3000);
                })
                .catch(() => { painel.textContent = 'Erro ao carregar.'; });
        }
        carregar();
    }

    function cartaoComparacao(c, artifactId, aoAtualizar) {
        const card = document.createElement('div');
        card.className = 'overlay-card st-' + (c.status === 'concluido' ? 'ok' : c.status === 'falhou' ? 'falhou' : (EM_ANDAMENTO.has(c.status) ? 'iniciado' : 'vazio'));
        const header = document.createElement('div');
        header.textContent = `juiz ${c.modelo_juiz_provider}:${c.modelo_juiz_model_name} — ${c.status}`;
        card.appendChild(header);
        if (EM_ANDAMENTO.has(c.status)) {
            const btn = document.createElement('button');
            btn.className = 'btn'; btn.textContent = 'Parar'; btn.style.marginTop = '.5rem';
            btn.onclick = () => fetch(`/artifacts/${artifactId}/comparacoes/${c.id}/cancelar/`, {
                method: 'POST', headers: { 'X-CSRFToken': window.MAPA_VIVO_CSRF },
            }).then(aoAtualizar);
            card.appendChild(btn);
        }
        const veredito = (c.resultado && c.resultado.veredito) || {};
        if (veredito.resumo) {
            const p = document.createElement('p');
            p.style.marginTop = '.5rem';
            p.textContent = veredito.resumo;
            card.appendChild(p);
        }
        if (veredito.mais_completo) {
            const p = document.createElement('p');
            p.innerHTML = `<strong>Mais completo:</strong> ${veredito.mais_completo}`;
            card.appendChild(p);
        }
        if (veredito.discrepancias && veredito.discrepancias.length) {
            const ul = document.createElement('ul');
            ul.style.marginTop = '.4rem';
            veredito.discrepancias.forEach(d => {
                const li = document.createElement('li');
                li.textContent = `${d.campo}: ${d.observacao}`;
                ul.appendChild(li);
            });
            card.appendChild(ul);
        }
        return card;
    }

    function montarPainelGenerico(no) {
        const painel = adicionarTab('resumo', 'Resumo', true);
        painel.textContent = JSON.stringify(no.meta, null, 2);
    }

    window.MapaVivoOverlay = { abrir, fechar };
})();
