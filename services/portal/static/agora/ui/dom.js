//Tiny DOM builder for the shell's imperative parts (panel trees are recursive, which data-for cannot express).
//  h('button', { class: 'tab', 'aria-selected': 'true', '.value': 'x', onClick: fn }, 'text', childElement)
//Keys: 'class'; 'onEvent' adds a listener; '.prop' sets a property; booleans toggle the attribute; null/false are skipped.
export function h(tag, props = {}, ...children) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(props ?? {})) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') element.className = value;
        else if (key.startsWith('on')) element.addEventListener(key.slice(2).toLowerCase(), value);
        else if (key.startsWith('.')) element[key.slice(1)] = value;
        else element.setAttribute(key, value === true ? '' : value);
    }
    element.append(...children.flat(Infinity).filter(child => child !== null && child !== undefined && child !== false));
    return element;
}
