//User and application settings (a dialog), built from what the HOST offers (host.settings):
//
//  host.settings = {
//    account: { name, logout?: async ({ clearDevice }) },                    -> "Conta" (and "Sair" when logout exists)
//    device:  { cacheLevels?, cacheLevel?, setCacheLevel?(level),            -> "Dados neste dispositivo"
//               describe(): { workspaces, pending, cacheEntries }, clear() },
//  }
//
//Application settings (theme, network) belong to the app itself and are always there. "Limpar este dispositivo" is a
//two-step action (a plain confirm() would block automation and cannot say what will be lost).
import { ReactiveComponent } from '../../vendor/ultima/reactive_component.js';
import { h } from '../dom.js';

const THEMES = [['auto', 'Automático (do sistema)'], ['light', 'Claro'], ['dark', 'Escuro']];
export const THEME_KEY = 'agora.theme';
export const LEVEL_LABELS = { nenhum: 'Nada (não guardar dados)', publico: 'Público', interno: 'Interno', restrito: 'Restrito', confidencial: 'Confidencial' };
const LEVEL_RANK = { nenhum: -1, publico: 0, interno: 1, restrito: 2, confidencial: 3 };

export const loadTheme = () => { try { return localStorage.getItem(THEME_KEY) || 'auto'; } catch { return 'auto'; } };
const saveTheme = theme => { try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage blocked: applies for this session only */ } };

export class AgoraSettings extends ReactiveComponent {
    #host = null;
    #view = 'main';                 // 'main' | 'confirm-clear'
    #message = {};                  // per-section feedback: { account?, device?, clear? } -> { text, error }
    #summary = null;

    constructor() {
        super({ templateUrl: './agora-settings.html', shadowDom: true }, import.meta.url);
    }

    get #dialog() { return this.rootNode.querySelector('#dialog'); }
    get isOpen() { return this.loaded && this.#dialog.open; }

    onLoad() {
        super.onLoad();
        this.rootNode.querySelector('#close').addEventListener('click', () => this.close());
        this.#dialog.addEventListener('close', () => { this.#view = 'main'; });
    }

    async open({ host }) {
        this.#host = host;
        this.#view = 'main';
        this.#message = {};
        this.#summary = null;
        this.#paint();
        if (!this.#dialog.open) this.#dialog.showModal();
        await this.#loadSummary();
    }

    close() { if (this.loaded && this.#dialog.open) this.#dialog.close(); }

    async #loadSummary() {
        const device = this.#host.settings?.device;
        if (!device?.describe) return;
        try { this.#summary = await device.describe(); } catch { this.#summary = null; }
        if (this.isOpen) this.#paint();
    }

    #paint() {
        const settings = this.#host?.settings ?? {};
        const content = this.rootNode.querySelector('#content');
        if (this.#view === 'confirm-clear') { content.replaceChildren(this.#confirmClear(settings)); this.#focusFirst(); return; }
        content.replaceChildren(...[this.#account(settings), this.#device(settings), this.#application()].filter(Boolean));
    }

    #focusFirst() { queueMicrotask(() => this.rootNode.querySelector('#content button:not([disabled]), #content select')?.focus()); }

    #feedback(key) {
        const m = this.#message[key];
        return m ? h('p', { class: m.error ? 'error' : 'ok', role: m.error ? 'alert' : 'status' }, m.text) : null;
    }

    // ---- Conta ----

    #account(settings) {
        const account = settings.account;
        if (!account) return null;
        const clearBox = h('input', { type: 'checkbox', id: 'logout-clear' });
        return h('section', { 'aria-labelledby': 'h-account' },
            h('h3', { id: 'h-account' }, 'Conta'),
            h('div', { class: 'row' }, h('span', {}, h('strong', {}, account.name), account.detail ? h('span', { class: 'hint' }, ` ${account.detail}`) : null)),
            account.logout ? [
                h('label', { class: 'check' }, clearBox, h('span', {}, 'Também limpar os dados deste dispositivo ao sair')),
                h('div', { class: 'actions' }, h('button', { type: 'button', id: 'logout', class: 'primary', onClick: () => this.#logout(clearBox.checked) }, 'Sair')),
            ] : null,
            this.#feedback('account'));
    }

    async #logout(clearDevice) {
        this.#message.account = { text: 'Saindo…' };
        this.#paint();
        try {
            await this.#host.settings.account.logout({ clearDevice });
        } catch (error) {
            this.#message.account = { text: error.message || 'Não foi possível sair agora.', error: true };
            this.#paint();
        }
    }

    // ---- Dados neste dispositivo ----

    #device(settings) {
        const device = settings.device;
        if (!device) return null;
        const levels = device.cacheLevels ?? null;
        const current = device.cacheLevel;
        const select = levels ? h('select', { id: 'cache-level', 'aria-describedby': 'cache-hint', onChange: event => this.#setLevel(event.target.value) },
            levels.map(level => h('option', { value: level, selected: level === current }, LEVEL_LABELS[level] ?? level))) : null;
        const s = this.#summary;
        return h('section', { 'aria-labelledby': 'h-device' },
            h('h3', { id: 'h-device' }, 'Dados neste dispositivo'),
            levels ? [
                h('div', { class: 'row' }, h('label', { for: 'cache-level' }, 'Nível do cache offline'), select),
                h('p', { class: 'hint', id: 'cache-hint' }, 'Até que nível de classificação os dados que você consulta ficam guardados aqui para uso sem rede. O que está acima do nível é apagado agora e nunca mais guardado.'),
                (LEVEL_RANK[current] ?? 0) >= LEVEL_RANK.restrito ? h('p', { class: 'warn', role: 'note' }, 'Atenção: o que fica guardado neste dispositivo não é cifrado. Se o aparelho for perdido, quem o abrir poderá ler esses dados.') : null,
                this.#feedback('device'),
            ] : null,
            s ? h('dl', {}, h('dt', {}, 'Workspaces'), h('dd', {}, String(s.workspaces)), h('dt', {}, 'Cópias de dados consultados'), h('dd', {}, String(s.cacheEntries ?? 0)),
                s.pending ? [h('dt', {}, 'Pendentes de envio'), h('dd', {}, `${s.pending} (ainda não chegaram ao servidor)`)] : null) : null,
            h('div', { class: 'actions' }, h('button', { type: 'button', id: 'clear', class: 'danger', onClick: () => { this.#view = 'confirm-clear'; this.#paint(); } }, 'Limpar este dispositivo…')));
    }

    async #setLevel(level) {
        try {
            await this.#host.settings.device.setCacheLevel(level);
            this.#message.device = { text: level === 'nenhum' ? 'Nada mais será guardado, e as cópias existentes foram apagadas.' : `Nível definido: ${LEVEL_LABELS[level] ?? level}. O que está acima dele foi apagado.` };
        } catch (error) {
            this.#message.device = { text: error.message || 'Não foi possível mudar o nível.', error: true };
        }
        await this.#loadSummary();
        this.#paint();
    }

    #confirmClear(settings) {
        const s = this.#summary;
        const understood = h('input', { type: 'checkbox', id: 'understood' });
        const go = h('button', { type: 'button', id: 'confirm-clear', class: 'danger', disabled: true, onClick: () => this.#clear() }, 'Apagar tudo deste dispositivo');
        understood.addEventListener('change', () => { go.disabled = !understood.checked; });
        return h('section', { 'aria-labelledby': 'h-clear' },
            h('h3', { id: 'h-clear' }, 'Limpar este dispositivo'),
            h('p', {}, 'Isto apaga deste dispositivo todos os workspaces, notas e dados guardados para uso sem rede.'),
            h('p', { class: 'hint' }, 'O que já foi enviado ao servidor continua lá e volta quando você abrir de novo com conexão.'),
            s?.pending ? h('p', { class: 'warn', role: 'alert' }, `Há ${s.pending} item(ns) que ainda NÃO chegou/chegaram ao servidor. Eles serão perdidos.`) : null,
            h('p', { class: 'warn', role: 'note' }, 'Se você estiver sem rede, o que foi escrito e ainda não sincronizou será perdido.'),
            h('label', { class: 'check' }, understood, h('span', {}, 'Entendi que o que não foi enviado ao servidor será perdido')),
            this.#feedback('clear'),
            h('div', { class: 'actions' }, h('button', { type: 'button', id: 'cancel-clear', onClick: () => { this.#view = 'main'; this.#paint(); } }, 'Cancelar'), go));
    }

    async #clear() {
        this.#message.clear = { text: 'Apagando…' };
        this.#paint();
        try {
            await this.#host.settings.device.clear();
            this.dispatchEvent(new CustomEvent('device-cleared', { bubbles: true, composed: true }));
        } catch (error) {
            this.#message.clear = { text: error.message || 'Não foi possível limpar.', error: true };
            this.#paint();
        }
    }

    // ---- Aplicativo ----

    #application() {
        const theme = loadTheme();
        return h('section', { 'aria-labelledby': 'h-app' },
            h('h3', { id: 'h-app' }, 'Aplicativo'),
            h('div', { class: 'row' }, h('label', { for: 'theme' }, 'Tema'),
                h('select', { id: 'theme', onChange: event => { saveTheme(event.target.value); this.dispatchEvent(new CustomEvent('theme-change', { detail: { theme: event.target.value }, bubbles: true, composed: true })); } },
                    THEMES.map(([value, label]) => h('option', { value, selected: value === theme }, label)))),
            h('div', { class: 'row' }, h('span', {}, 'Rede'), h('span', { id: 'network' }, navigator.onLine ? 'Conectado' : 'Sem conexão (trabalhando offline)')));
    }
}

customElements.define('agora-settings', AgoraSettings);
