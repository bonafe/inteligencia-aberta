//Componentes que as extensões instaladas trazem (ADR 023). A lista de manifestos vem na configuração da página
//(e, sem rede, da cópia lembrada pelo boot.js). Uma extensão com defeito não pode derrubar o Agora: o erro
//vai para o Console e as outras seguem.
export async function installExtensionPacks(registry, manifests = []) {
    const failed = [];
    await Promise.all(manifests.map(async url => {
        try {
            await registry.registerFromUrl(new URL(url, location.origin));
        } catch (error) {
            failed.push(url);
            console.warn(`Componente de extensão não registrado (${url}):`, error);
        }
    }));
    return { failed };
}
