//Validation of component manifests (see docs/especificacao/03-componentes-e-contrato.md, 3.3).

export const CAPABILITIES = ['resizable', 'connectable', 'shared-content', 'presence-aware', 'context-provider', 'domain-data', 'file-store', 'agent-callable'];
export const ACTION_EFFECTS = ['self', 'state', 'emit', 'feed', 'create'];
export const CONFIG_TYPES = ['string', 'text', 'integer', 'number', 'boolean', 'enum', 'color', 'date', 'reference', 'list'];

const NAME_PATTERN = /^[a-z][a-z0-9]*(-[a-z0-9]+)+$/;      //custom element names need a hyphen
const SEMVER_PATTERN = /^\d+\.\d+\.\d+$/;

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);

//Returns the list of problems found; an empty list means the manifest is valid.
export function validateManifest(manifest) {
    const errors = [];
    const fail = (field, message) => errors.push(`${field}: ${message}`);

    if (!isObject(manifest)) return ['manifest: must be an object'];

    for (const field of ['name', 'tag', 'title', 'version', 'module']) {
        if (typeof manifest[field] !== 'string' || !manifest[field]) fail(field, 'required string');
    }
    if (typeof manifest.name === 'string' && !NAME_PATTERN.test(manifest.name)) fail('name', 'must be lowercase and hyphenated, e.g. "ia-graph"');
    if (typeof manifest.tag === 'string' && !NAME_PATTERN.test(manifest.tag)) fail('tag', 'must be a valid custom element name');
    if (typeof manifest.version === 'string' && !SEMVER_PATTERN.test(manifest.version)) fail('version', 'must be MAJOR.MINOR.PATCH');

    for (const section of ['inputs', 'outputs']) {
        if (manifest[section] === undefined) continue;
        if (!isObject(manifest[section])) { fail(section, 'must be an object'); continue; }
        for (const [port, spec] of Object.entries(manifest[section])) {
            if (!isObject(spec) || typeof spec.type !== 'string' || !spec.type) { fail(`${section}.${port}`, 'needs a "type" string'); continue; }
            for (const flag of ['multiple', 'retained', 'required']) {
                if (spec[flag] !== undefined && typeof spec[flag] !== 'boolean') fail(`${section}.${port}.${flag}`, 'must be a boolean');
            }
        }
    }

    if (manifest.actions !== undefined) {
        if (!isObject(manifest.actions)) fail('actions', 'must be an object');
        else for (const [name, spec] of Object.entries(manifest.actions)) {
            if (!isObject(spec)) { fail(`actions.${name}`, 'must be an object'); continue; }
            if (typeof spec.title !== 'string') fail(`actions.${name}.title`, 'required string');
            if (spec.effect !== undefined && !ACTION_EFFECTS.includes(spec.effect)) fail(`actions.${name}.effect`, `must be one of ${ACTION_EFFECTS.join(', ')}`);
            if (spec.effect === 'create' && typeof spec.produces !== 'string') fail(`actions.${name}.produces`, 'required when effect is "create"');
            if (spec.effect === 'create' || spec.effect === 'emit') {
                if (typeof spec.port !== 'string') fail(`actions.${name}.port`, 'required: the output port that carries the result');
                else if (!manifest.outputs?.[spec.port]) fail(`actions.${name}.port`, `"${spec.port}" is not an output port`);
                else if (spec.produces && manifest.outputs[spec.port].type !== spec.produces) fail(`actions.${name}.produces`, `must equal the type of output "${spec.port}"`);
            }
            if (spec.suggests !== undefined && !Array.isArray(spec.suggests)) fail(`actions.${name}.suggests`, 'must be an array of component names');
        }
    }

    if (manifest.config !== undefined) {
        if (!isObject(manifest.config)) fail('config', 'must be an object');
        else for (const [name, spec] of Object.entries(manifest.config)) {
            if (!isObject(spec) || !CONFIG_TYPES.includes(spec.type)) fail(`config.${name}.type`, `must be one of ${CONFIG_TYPES.join(', ')}`);
            else if (spec.type === 'enum' && (!Array.isArray(spec.values) || !spec.values.length)) fail(`config.${name}.values`, 'enum needs a non-empty "values" list');
        }
    }

    if (manifest.capabilities !== undefined) {
        if (!Array.isArray(manifest.capabilities)) fail('capabilities', 'must be an array');
        else for (const capability of manifest.capabilities) {
            if (!CAPABILITIES.includes(capability)) fail('capabilities', `unknown capability "${capability}"`);
        }
    }

    return errors;
}

//Checks a config object against the manifest's config schema; returns the list of problems.
export function validateConfig(manifest, config) {
    const errors = [];
    const schema = manifest.config ?? {};
    for (const [name, value] of Object.entries(config ?? {})) {
        const spec = schema[name];
        if (!spec) { errors.push(`config.${name}: not declared in the manifest`); continue; }
        const bad = message => errors.push(`config.${name}: ${message}`);
        switch (spec.type) {
            case 'string': case 'text': case 'color': case 'date': case 'reference':
                if (typeof value !== 'string') bad('must be a string'); break;
            case 'boolean':
                if (typeof value !== 'boolean') bad('must be a boolean'); break;
            case 'integer': case 'number':
                if (typeof value !== 'number' || Number.isNaN(value) || (spec.type === 'integer' && !Number.isInteger(value))) bad(`must be ${spec.type === 'integer' ? 'an integer' : 'a number'}`);
                else if ((spec.min !== undefined && value < spec.min) || (spec.max !== undefined && value > spec.max)) bad(`out of range [${spec.min ?? '-∞'}, ${spec.max ?? '∞'}]`);
                break;
            case 'enum':
                if (!spec.values.includes(value)) bad(`must be one of ${spec.values.join(', ')}`); break;
            case 'list':
                if (!Array.isArray(value)) bad('must be an array'); break;
        }
    }
    return errors;
}
