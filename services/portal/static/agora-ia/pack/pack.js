//The Inteligência Aberta pack for the Ultima Agora: types, converters and components (spec 10.4).
import { registerIaTypes } from './types.js';

export const IA_MANIFESTS = ['./ia-search/ia-search.manifest.json', './ia-entity/ia-entity.manifest.json', './ia-news/ia-news.manifest.json'];

export async function installIaPack(registry) {
    registerIaTypes(registry.types);
    await Promise.all(IA_MANIFESTS.map(path => registry.registerFromUrl(new URL(path, import.meta.url))));
}
