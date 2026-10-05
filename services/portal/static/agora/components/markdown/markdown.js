//Minimal, safe Markdown renderer for notes. Everything is HTML-escaped first and only a fixed set of
//constructs is turned back into tags, so no user input reaches the DOM as markup.
//Supports: # headings, - / * / 1. lists, ``` code blocks, **bold**, *italic*, `code`, [text](http(s) url).

const escapeHtml = text => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

function inline(escaped) {
    return escaped
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

export function renderMarkdown(source) {
    const lines = escapeHtml(String(source ?? '')).split('\n');
    const html = [];
    let list = null;                    //'ul' | 'ol' while inside a list
    let code = null;                    //array of lines while inside a fence
    let paragraph = [];

    const closeParagraph = () => { if (paragraph.length) html.push(`<p>${inline(paragraph.join(' '))}</p>`); paragraph = []; };
    const closeList = () => { if (list) html.push(`</${list}>`); list = null; };

    for (const line of lines) {
        if (code) {
            if (line.trim().startsWith('```')) { html.push(`<pre><code>${code.join('\n')}</code></pre>`); code = null; } else code.push(line);
            continue;
        }
        if (line.trim().startsWith('```')) { closeParagraph(); closeList(); code = []; continue; }

        const heading = /^(#{1,3})\s+(.*)$/.exec(line);
        const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
        const numbered = /^\s*\d+\.\s+(.*)$/.exec(line);

        if (heading) { closeParagraph(); closeList(); html.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); }
        else if (bullet || numbered) {
            closeParagraph();
            const kind = bullet ? 'ul' : 'ol';
            if (list !== kind) { closeList(); html.push(`<${kind}>`); list = kind; }
            html.push(`<li>${inline((bullet ?? numbered)[1])}</li>`);
        }
        else if (!line.trim()) { closeParagraph(); closeList(); }
        else { closeList(); paragraph.push(line.trim()); }
    }
    if (code) html.push(`<pre><code>${code.join('\n')}</code></pre>`);
    closeParagraph(); closeList();
    return html.join('\n');
}
