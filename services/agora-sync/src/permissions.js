//Who may change what in a workspace document (docs/especificacao/09, 9.4). Enforced HERE, not only in the UI:
//a tampered client gets its connection closed (R-PERM-3, acceptance C12).
import * as Y from 'yjs';

export const COMPOSITION_ROOTS = ['meta', 'instances', 'layout', 'connections'];
export const ALLOWED_ROOTS = {
    owner: null,                        //null = anything
    editor: null,
    participant: new Set(['content']),  //authored content only: notes, drawings
    viewer: new Set(),
};

const rootNameOf = (type, doc) => {
    while (type._item) type = type._item.parent;
    for (const [name, root] of doc.share) if (root === type) return name;
    return null;
};

//Applies `update` to a scratch copy of the document and reports which top-level maps it touches.
//Not the cheapest approach (it copies the document per participant write); fine until a shadow copy is kept per room.
export function rootsTouchedBy(doc, update) {
    const scratch = new Y.Doc();
    Y.applyUpdate(scratch, Y.encodeStateAsUpdate(doc));
    const touched = new Set();
    scratch.on('afterTransaction', transaction => {
        for (const type of transaction.changed.keys()) touched.add(rootNameOf(type, scratch));
        for (const type of transaction.changedParentTypes.keys()) touched.add(rootNameOf(type, scratch));
    });
    Y.applyUpdate(scratch, update);
    scratch.destroy();
    return touched;
}

//true when a connection with `role` may apply `update` to `doc`
export function mayWrite(role, doc, update) {
    const allowed = ALLOWED_ROOTS[role];
    if (allowed === null) return true;
    if (!allowed) return false;
    //An update that changes nothing is always fine: the normal sync handshake sends one from every client,
    //viewers included. Only a real change to a root outside the role's allowance is refused.
    const touched = rootsTouchedBy(doc, update);
    return [...touched].every(root => allowed.has(root));
}
