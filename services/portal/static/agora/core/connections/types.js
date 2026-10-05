//Port type system (docs/especificacao/04, 4.2): named types with subtyping, structural validation of
//values, and explicit converters. Types are data, so applications register their own (Entity, Claim…)
//without the core knowing them.

const KINDS = ['string', 'number', 'boolean', 'object', 'any'];
const PROPERTY_CHECKS = {
    string: v => typeof v === 'string',
    number: v => typeof v === 'number' && Number.isFinite(v),
    boolean: v => typeof v === 'boolean',
    array: v => Array.isArray(v),
    object: v => v !== null && typeof v === 'object' && !Array.isArray(v),
    any: () => true,
};
const MAX_CHECKED_ELEMENTS = 500;       //validating huge arrays element by element would stall the UI

const isArrayType = name => name.endsWith('[]');
const elementType = name => name.slice(0, -2);

export class TypeRegistry {
    #types = new Map();
    #converters = [];

    //{ name, extends?, kind?, schema? }   schema: { required?: [], properties?: { key: 'string'|'number'|'boolean'|'array'|'object'|'any' } }
    register({ name, extends: parent = null, kind = null, schema = null, displayHint = null }) {
        if (!/^[A-Z][A-Za-z0-9]*$/.test(name)) throw new Error(`Type name "${name}" must be PascalCase`);
        if (this.#types.has(name)) throw new Error(`Type "${name}" is already registered`);
        if (parent && !this.#types.has(parent)) throw new Error(`Type "${name}" extends unknown type "${parent}"`);
        const inheritedKind = parent ? this.#types.get(parent).kind : 'any';
        if (kind && !KINDS.includes(kind)) throw new Error(`Type "${name}": invalid kind "${kind}"`);
        this.#types.set(name, { name, parent, kind: kind ?? inheritedKind, schema, displayHint });
    }

    has(name) { return this.#types.has(isArrayType(name) ? elementType(name) : name); }
    get(name) { return this.#types.get(name) ?? null; }
    list() { return [...this.#types.keys()]; }

    isSubtype(sub, sup) {
        if (sub === sup || sup === 'Json') return true;
        if (isArrayType(sub) !== isArrayType(sup)) return false;
        if (isArrayType(sub)) return this.isSubtype(elementType(sub), elementType(sup));
        for (let type = this.#types.get(sub); type; type = type.parent ? this.#types.get(type.parent) : null) {
            if (type.name === sup) return true;
        }
        return false;
    }

    //convert(value) -> value of the target type; label is shown to the user ("converte: rótulo")
    registerConverter({ from, to, label, convert }) {
        if (!this.has(from) || !this.has(to)) throw new Error(`Converter ${from} → ${to}: both types must be registered`);
        this.#converters.push({ from, to, label, convert });
    }

    //Can a value of `from` flow into a port of type `to`? A single conversion hop is allowed.
    //Returns { ok, convert, via, reason }; convert is null when no conversion is needed.
    compatibility(from, to) {
        if (this.isSubtype(from, to)) return { ok: true, convert: null, via: null };
        if (isArrayType(from) && isArrayType(to)) {
            const inner = this.compatibility(elementType(from), elementType(to));
            if (inner.ok && inner.convert) return { ok: true, convert: list => list.map(inner.convert), via: inner.via };
        }
        const converter = this.#converters.find(c => this.isSubtype(from, c.from) && this.isSubtype(c.to, to));
        if (converter) return { ok: true, convert: converter.convert, via: converter.label ?? `${converter.from} → ${converter.to}` };
        return { ok: false, convert: null, via: null, reason: `${from} não é aceito por uma porta ${to}` };
    }

    //Returns null when the value is valid for the type, otherwise a message.
    validate(typeName, value) {
        if (isArrayType(typeName)) {
            if (!Array.isArray(value)) return `esperava uma lista de ${elementType(typeName)}`;
            for (const item of value.slice(0, MAX_CHECKED_ELEMENTS)) {
                const problem = this.validate(elementType(typeName), item);
                if (problem) return problem;
            }
            return null;
        }
        const type = this.#types.get(typeName);
        if (!type) return `tipo desconhecido "${typeName}"`;
        if (type.parent) {
            const problem = this.validate(type.parent, value);
            if (problem) return problem;
        }
        if (type.kind !== 'any' && !PROPERTY_CHECKS[type.kind](value)) return `esperava ${typeName} (${type.kind})`;
        const { required = [], properties = {} } = type.schema ?? {};
        for (const key of required) if (!(key in value)) return `${typeName} sem o campo "${key}"`;
        for (const [key, kind] of Object.entries(properties)) {
            if (value[key] !== undefined && !PROPERTY_CHECKS[kind](value[key])) return `${typeName}.${key} deveria ser ${kind}`;
        }
        return null;
    }
}

//Generic types and converters that ship with the core (R-TYPE-1, R-TYPE-2). Domain types are never here.
export function registerCoreTypes(types) {
    types.register({ name: 'Json' });
    types.register({ name: 'String', kind: 'string' });
    types.register({ name: 'Text', extends: 'String' });
    types.register({ name: 'Ref', extends: 'String' });
    types.register({ name: 'Number', kind: 'number' });
    types.register({ name: 'Boolean', kind: 'boolean' });
    types.register({ name: 'Row', kind: 'object' });
    types.register({ name: 'Table', kind: 'object', schema: { required: ['columns', 'rows'], properties: { columns: 'array', rows: 'array' } } });
    types.register({ name: 'Node', kind: 'object', schema: { required: ['id'], properties: { id: 'string', label: 'string' } } });
    types.register({ name: 'Graph', kind: 'object', schema: { required: ['nodes', 'edges'], properties: { nodes: 'array', edges: 'array' } } });
    types.register({ name: 'FilterSpec', kind: 'object' });

    types.registerConverter({ from: 'Node', to: 'String', label: 'rótulo do nó', convert: node => node.label ?? node.id });
    types.registerConverter({ from: 'Row', to: 'String', label: 'valores da linha', convert: row => Object.values(row).join(' · ') });
}
