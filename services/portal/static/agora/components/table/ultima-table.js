import { AgoraComponent } from '../../core/components/agora_component.js';
import { h } from '../../ui/dom.js';

//Parses CSV (comma separated, optional "quoted, fields"; first line is the header) into a Table value.
export function parseCsv(text) {
    const lines = String(text ?? '').split(/\r?\n/).filter(line => line.trim());
    const split = line => {
        const cells = [];
        let cell = '', quoted = false;
        for (let i = 0; i < line.length; i++) {
            const c = line[i];
            if (quoted) { if (c === '"' && line[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') quoted = false; else cell += c; }
            else if (c === '"') quoted = true;
            else if (c === ',') { cells.push(cell.trim()); cell = ''; }
            else cell += c;
        }
        cells.push(cell.trim());
        return cells;
    };
    if (!lines.length) return { columns: [], rows: [] };
    const header = split(lines[0]);
    const columns = header.map(key => ({ key, title: key }));
    const rows = lines.slice(1).map(line => Object.fromEntries(split(line).map((value, i) => [header[i] ?? `col${i + 1}`, value])));
    return { columns, rows };
}

export default class UltimaTable extends AgoraComponent {
    #incoming;                      //Table arriving on the "rows" input; wins over the configured CSV while connected
    #sort = null;                   //{ key, direction }  (view state, local)
    #selected = null;               //index into the displayed rows (view state, local)

    constructor() {
        super({ templateUrl: './ultima-table.html', shadowDom: true }, import.meta.url);
    }

    onLoad() { super.onLoad(); this.#refresh(); }
    onConfig() { this.#refresh(); }
    onInput(port, value) { if (port === 'rows') { this.#incoming = value; this.#refresh(); } }

    get #table() { return this.#incoming ?? parseCsv(this.config.data); }

    #refresh() {
        if (!this.loaded) return;
        this.#selected = null;
        this.#paint();
        this.emit('table', this.#table);
        //Output values are runtime state, not stored in the document: derive "graph" whenever the data
        //changes, so a reopened workspace has it again without anyone running the action
        try { this.emit('graph', this.#buildGraph()); } catch { /* the data has no from/to columns: no graph to offer */ }
    }

    #displayedRows() {
        const rows = [...this.#table.rows];
        if (!this.#sort) return rows;
        const { key, direction } = this.#sort;
        return rows.sort((a, b) => String(a[key] ?? '').localeCompare(String(b[key] ?? ''), 'pt-BR', { numeric: true }) * (direction === 'ascending' ? 1 : -1));
    }

    #paint() {
        const { columns } = this.#table;
        const rows = this.#displayedRows();
        this.rootNode.querySelector('#empty').hidden = columns.length > 0;
        const select = index => { this.#selected = index; this.#paint(); this.emit('selectedRow', rows[index]); };

        this.rootNode.querySelector('#table').replaceChildren(
            h('thead', {}, h('tr', {}, columns.map(column => h('th', {
                tabindex: 0, scope: 'col', 'aria-sort': this.#sort?.key === column.key ? this.#sort.direction : 'none',
                onClick: () => this.#toggleSort(column.key),
                onKeydown: event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.#toggleSort(column.key); } },
            }, column.title)))),
            h('tbody', {}, rows.map((row, index) => h('tr', {
                tabindex: 0, 'aria-selected': index === this.#selected ? 'true' : 'false',
                onClick: () => select(index),
                onKeydown: event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(index); } },
            }, columns.map(column => h('td', {}, String(row[column.key] ?? '')))))),
        );
    }

    #toggleSort(key) {
        const direction = this.#sort?.key === key && this.#sort.direction === 'ascending' ? 'descending' : 'ascending';
        this.#sort = { key, direction };
        this.#selected = null;
        this.#paint();
    }

    //Rows become edges between the values of the from/to columns. Throws a readable error when the
    //configured columns are not in the table.
    #buildGraph() {
        const { fromColumn, toColumn, labelColumn } = this.config;
        const table = this.#table;
        const keys = table.columns.map(c => c.key);
        for (const column of [fromColumn, toColumn]) {
            if (!keys.includes(column)) throw new Error(`A tabela não tem a coluna "${column}". Ajuste as colunas de origem/destino no inspetor.`);
        }
        const ids = new Set();
        const edges = [];
        for (const row of table.rows) {
            const from = String(row[fromColumn] ?? '').trim();
            const to = String(row[toColumn] ?? '').trim();
            if (!from || !to) continue;
            ids.add(from); ids.add(to);
            edges.push({ from, to, ...(row[labelColumn] ? { label: String(row[labelColumn]) } : {}) });
        }
        return { nodes: [...ids].map(id => ({ id, label: id })), edges };
    }

    //Action "showGraph"; its result is also what the "graph" output carries
    onAction(name) { return name === 'showGraph' ? this.#buildGraph() : undefined; }

    describeContext() { return { columns: this.#table.columns.map(c => c.key), rows: this.#table.rows.length }; }
}
