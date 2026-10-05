//Mixin composition for components.
//
//A mixin is a function that takes a base class and returns a subclass of it, so everything the
//framework relies on (private fields such as ReactiveComponent's #state, observedAttributes,
//import.meta.url handed to the constructor) keeps working through the plain inheritance chain.
//
//    const Draggable = Base => class extends Base {
//        onLoad() { super.onLoad(); /* ... */ }
//    };
//    class MyCard extends mix(ReactiveComponent).with(Draggable, Selectable) {}
//
//Mixins apply left to right: the last one is the closest to the final class.

//Hooks that every layer is expected to override and chain with super; they never count as conflicts.
export const LIFECYCLE_HOOKS = new Set([
    'constructor', 'onLoad', 'connectedCallback', 'disconnectedCallback', 'adoptedCallback', 'attributeChangedCallback',
]);

const ownMembers = cls => Object.getOwnPropertyNames(cls.prototype).filter(name => !LIFECYCLE_HOOKS.has(name));

export function mix(Base) {
    return {
        with(...mixins) {
            const claimedBy = new Map();        //member name -> name of the mixin that defined it
            return mixins.reduce((cls, mixin) => {
                const label = mixin?.name || '(anonymous mixin)';
                if (typeof mixin !== 'function') throw new TypeError(`mix: a mixin must be a function, got ${typeof mixin}`);

                const result = mixin(cls);
                if (typeof result !== 'function' || !(result.prototype instanceof cls)) {
                    throw new TypeError(`mix: ${label} must return a subclass of the class it receives`);
                }
                for (const name of ownMembers(result)) {
                    if (claimedBy.has(name)) {
                        throw new Error(`mix: ${label} redefines "${name}", already defined by ${claimedBy.get(name)}. Rename one of them, or chain through the shared hook with super`);
                    }
                    claimedBy.set(name, label);
                }
                return result;
            }, Base);
        },
    };
}
