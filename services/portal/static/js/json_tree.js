/* Renderizador de árvore JSON colapsável.
 *
 * Extraído da galeria de capturas para ser reaproveitado pelo painel de
 * eventos, que precisa mostrar o payload de cada evento com a mesma leitura.
 * Depende apenas das classes .jt-* definidas em static/css/json-tree.css.
 */
function renderJsonNode(value, key) {
    if (Array.isArray(value)) return renderArray(value, key);
    if (value !== null && typeof value === 'object') return renderObject(value, key);
    return renderScalar(value, key);
}

function renderScalar(value, key) {
    const span = document.createElement('span');
    span.className = 'jt-node';

    if (key !== undefined) {
        const keySpan = document.createElement('span');
        keySpan.className = 'jt-key';
        keySpan.textContent = `"${key}": `;
        span.appendChild(keySpan);
    }

    const valSpan = document.createElement('span');
    if (value === null) {
        valSpan.className = 'jt-null';
        valSpan.textContent = 'null';
    } else if (typeof value === 'boolean') {
        valSpan.className = 'jt-bool';
        valSpan.textContent = String(value);
    } else if (typeof value === 'number') {
        valSpan.className = 'jt-num';
        valSpan.textContent = String(value);
    } else {
        valSpan.className = 'jt-str';
        valSpan.textContent = `"${String(value)}"`;
    }
    span.appendChild(valSpan);
    return span;
}

function renderObject(obj, key) {
    const keys = Object.keys(obj);
    const wrapper = document.createElement('div');
    wrapper.className = 'jt-collapsible jt-node';

    const toggle = document.createElement('span');
    toggle.className = 'jt-toggle';
    toggle.onclick = () => wrapper.classList.toggle('collapsed');

    if (key !== undefined) {
        const keySpan = document.createElement('span');
        keySpan.className = 'jt-key';
        keySpan.textContent = `"${key}": `;
        toggle.appendChild(keySpan);
    }

    const openBracket = document.createElement('span');
    openBracket.className = 'jt-bracket';
    openBracket.textContent = '{';
    toggle.appendChild(openBracket);

    const summary = document.createElement('span');
    summary.className = 'jt-summary';
    summary.textContent = ` ${keys.length} chave${keys.length !== 1 ? 's' : ''} `;
    toggle.appendChild(summary);

    wrapper.appendChild(toggle);

    const children = document.createElement('div');
    children.className = 'jt-children';
    keys.forEach(k => children.appendChild(renderJsonNode(obj[k], k)));
    wrapper.appendChild(children);

    const close = document.createElement('span');
    close.className = 'jt-bracket';
    close.textContent = '}';
    wrapper.appendChild(close);

    return wrapper;
}

function renderArray(arr, key) {
    const wrapper = document.createElement('div');
    wrapper.className = 'jt-collapsible jt-node';

    const toggle = document.createElement('span');
    toggle.className = 'jt-toggle';
    toggle.onclick = () => wrapper.classList.toggle('collapsed');

    if (key !== undefined) {
        const keySpan = document.createElement('span');
        keySpan.className = 'jt-key';
        keySpan.textContent = `"${key}": `;
        toggle.appendChild(keySpan);
    }

    const openBracket = document.createElement('span');
    openBracket.className = 'jt-bracket';
    openBracket.textContent = '[';
    toggle.appendChild(openBracket);

    const summary = document.createElement('span');
    summary.className = 'jt-summary';
    summary.textContent = ` ${arr.length} item${arr.length !== 1 ? 's' : ''} `;
    toggle.appendChild(summary);

    wrapper.appendChild(toggle);

    const children = document.createElement('div');
    children.className = 'jt-children';
    arr.forEach((item, i) => children.appendChild(renderJsonNode(item, i)));
    wrapper.appendChild(children);

    const close = document.createElement('span');
    close.className = 'jt-bracket';
    close.textContent = ']';
    wrapper.appendChild(close);

    return wrapper;
}
