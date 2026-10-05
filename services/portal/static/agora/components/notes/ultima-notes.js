import { AgoraComponent } from '../../core/components/agora_component.js';
import { renderMarkdown } from '../markdown/markdown.js';

const SAVE_DELAY_MS = 400;      //typing is coalesced so that one undo step is a burst of typing, not a letter

export default class UltimaNotes extends AgoraComponent {
    #editor = null;
    #preview = null;
    #timer = null;
    #stopListening = null;
    #typingTimer = null;

    constructor() {
        super({ templateUrl: './ultima-notes.html', shadowDom: true }, import.meta.url);
    }

    onLoad() {
        super.onLoad();
        const root = this.rootNode;
        this.#editor = root.querySelector('#editor');
        this.#preview = root.querySelector('#preview');
        this.#editor.value = this.shared.getText();
        this.#applyRole();

        this.#editor.addEventListener('input', () => { this.#scheduleSave(); this.#announceTyping(); });
        this.#editor.addEventListener('blur', () => this.#flush());
        root.querySelector('#edit').addEventListener('click', () => this.#setMode('edit'));
        root.querySelector('#view').addEventListener('click', () => this.#setMode('view'));

        //Changes from elsewhere (undo, another participant later). Ignored while this user has unsaved typing.
        this.#stopListening = this.shared.onChange(text => {
            if (this.#timer === null && text !== this.#editor.value) this.#replaceKeepingCaret(text);
            if (!this.#preview.hidden) this.#preview.innerHTML = renderMarkdown(text);
        });
    }

    //A remote edit replaces the text; the caret is moved by the length of what changed before it, so it does
    //not jump while someone else types elsewhere
    #replaceKeepingCaret(next) {
        const editor = this.#editor;
        const before = editor.value;
        const caret = editor.selectionStart;
        let start = 0;
        while (start < before.length && start < next.length && before[start] === next[start]) start++;
        let endBefore = before.length, endNext = next.length;
        while (endBefore > start && endNext > start && before[endBefore - 1] === next[endNext - 1]) { endBefore--; endNext--; }
        const shift = caret >= endBefore ? endNext - endBefore : caret > start ? start - caret : 0;
        editor.value = next;
        const position = Math.max(0, Math.min(next.length, caret + shift));
        if (this.rootNode.activeElement === editor) editor.setSelectionRange(position, position);
    }

    //Others see "editing here" on the tab for a couple of seconds after the last keystroke
    #announceTyping() {
        this.presence.setActivity('typing');
        clearTimeout(this.#typingTimer);
        this.#typingTimer = setTimeout(() => this.presence.setActivity(null), 2500);
    }

    onRole() { this.#applyRole(); }

    #applyRole() {
        if (this.#editor) this.#editor.readOnly = !this.shared.canEdit;
    }

    #scheduleSave() {
        clearTimeout(this.#timer);
        this.#timer = setTimeout(() => this.#flush(), SAVE_DELAY_MS);
    }

    #flush() {
        if (this.#timer === null) return;
        clearTimeout(this.#timer);
        this.#timer = null;
        if (!this.shared.canEdit) { this.#editor.value = this.shared.getText(); return; }
        if (this.#editor.value !== this.shared.getText()) this.shared.setText(this.#editor.value);
    }

    #setMode(mode) {
        this.#flush();
        const viewing = mode === 'view';
        this.#editor.hidden = viewing;
        this.#preview.hidden = !viewing;
        this.rootNode.querySelector('#edit').setAttribute('aria-pressed', String(!viewing));
        this.rootNode.querySelector('#view').setAttribute('aria-pressed', String(viewing));
        if (viewing) this.#preview.innerHTML = renderMarkdown(this.shared.getText());
        else this.#editor.focus();
    }

    onDispose() {
        clearTimeout(this.#typingTimer);
        this.#flush();
        this.#stopListening?.();
    }

    describeContext() { return { characters: this.shared.getText().length }; }
}
