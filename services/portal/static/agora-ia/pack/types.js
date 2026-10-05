//Domain types of the Inteligência Aberta (the Agora core knows none of these: "domínio fora do núcleo").
//Values on the bus are small DTOs (id, label, kind, level); details are fetched through `this.domain`
//with the permission of whoever is looking (R-DOC-3, R-IA-6).

export function registerIaTypes(types) {
    types.register({
        name: 'Entity', kind: 'object',
        schema: { required: ['id'], properties: { id: 'string', label: 'string', kind: 'string', classification: 'string' } },
    });

    types.registerConverter({ from: 'Entity', to: 'String', label: 'rótulo', convert: entity => entity.label ?? entity.id });

    //A list of entities is shown by the generic table. Its rows keep the entity fields, so a selected row can
    //become an Entity again (below).
    types.registerConverter({
        from: 'Entity[]', to: 'Table', label: 'tabela de entidades',
        convert: entities => ({
            columns: [{ key: 'label', title: 'Nome' }, { key: 'kind', title: 'Tipo' }, { key: 'classification', title: 'Nível' }, { key: 'id', title: 'Id' }],
            rows: entities.map(({ id, label = '', kind = '', classification = '' }) => ({ label, kind, classification, id })),
        }),
    });
    //A node of a graph (id + label) becomes an Entity: this is what lets "the node I chose in the graph" feed ia-entity and ia-news
    types.registerConverter({
        from: 'Node', to: 'Entity', label: 'nó → entidade',
        convert: node => ({ id: String(node.id), label: String(node.label ?? node.id), kind: String(node.kind ?? ''), classification: String(node.classification ?? '') }),
    });
    //Only meaningful for rows that came from entities (they carry an id); other rows are ignored by ia-entity
    types.registerConverter({
        from: 'Row', to: 'Entity', label: 'linha → entidade',
        convert: row => ({ id: String(row.id ?? ''), label: String(row.label ?? ''), kind: String(row.kind ?? ''), classification: String(row.classification ?? '') }),
    });
}
