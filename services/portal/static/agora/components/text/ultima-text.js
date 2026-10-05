import { AgoraComponent } from '../../core/components/agora_component.js';

export default class UltimaText extends AgoraComponent {
    #incoming;              //value arriving on the "text" input; wins over the configured text while connected

    constructor() {
        super({ templateUrl: './ultima-text.html', shadowDom: true }, import.meta.url);
    }

    onLoad() {
        super.onLoad();
        this.#show();
    }

    onConfig() { this.#show(); }

    onInput(port, value) {
        if (port === 'text') { this.#incoming = value; this.#show(); }
    }

    #show() {
        this.state = { text: this.#incoming ?? this.config.text ?? '', size: this.config.size ?? 'normal' };
    }
}
