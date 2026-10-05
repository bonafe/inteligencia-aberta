//Syncs a Y.Doc and its awareness with a server over any transport (docs/especificacao/05, 5.2/5.9).
//
//Wire format = y-websocket's: one varuint message type, then the payload.
//   0 sync (Yjs sync protocol)   1 awareness   3 control (JSON string, server -> client: role changes, errors)
//
//Transport: { send(Uint8Array), close(code?, reason?), onopen, onmessage(Uint8Array), onclose({ code, reason }) }
//Status: 'connecting' | 'synced' | 'offline' | 'error'. Offline edits stay in the local doc and merge on reconnect.
import { Y, syncProtocol, awarenessProtocol, encoding, decoding } from '../../vendor/yjs/yjs.js';

const MSG_SYNC = 0, MSG_AWARENESS = 1, MSG_CONTROL = 3;
const NO_RETRY_CODES = new Set([4400, 4401, 4403, 4404]);       //bad request, auth failed, forbidden, unknown workspace

export class SyncProvider {
    #ydoc;
    #awareness;
    #openTransport;
    #onStatus;
    #onControl;
    #minDelay;
    #maxDelay;
    #transport = null;
    #status = 'offline';
    #wanted = false;
    #attempts = 0;
    #timer = null;
    #listeners = [];

    constructor({ ydoc, awareness, openTransport, onStatus = () => {}, onControl = () => {}, minDelayMs = 1000, maxDelayMs = 30000 }) {
        this.#ydoc = ydoc;
        this.#awareness = awareness;
        this.#openTransport = openTransport;
        this.#onStatus = onStatus;
        this.#onControl = onControl;
        this.#minDelay = minDelayMs;
        this.#maxDelay = maxDelayMs;

        const onUpdate = (update, origin) => {
            if (origin === this || !this.#open) return;
            const message = encoding.createEncoder();
            encoding.writeVarUint(message, MSG_SYNC);
            syncProtocol.writeUpdate(message, update);
            this.#send(message);
        };
        const onAwareness = ({ added, updated, removed }, origin) => {
            if (origin === this || !this.#open) return;
            const message = encoding.createEncoder();
            encoding.writeVarUint(message, MSG_AWARENESS);
            encoding.writeVarUint8Array(message, awarenessProtocol.encodeAwarenessUpdate(awareness, [...added, ...updated, ...removed]));
            this.#send(message);
        };
        ydoc.on('update', onUpdate);
        awareness.on('update', onAwareness);
        this.#listeners = [() => ydoc.off('update', onUpdate), () => awareness.off('update', onAwareness)];
    }

    get status() { return this.#status; }
    get #open() { return this.#transport !== null; }

    #setStatus(status) {
        if (status === this.#status) return;
        this.#status = status;
        this.#onStatus(status);
    }

    #send(encoder) { try { this.#transport?.send(encoding.toUint8Array(encoder)); } catch (error) { console.error('[sync] send failed:', error); } }

    connect() {
        this.#wanted = true;
        if (this.#transport || this.#timer) return;
        this.#attempt();
    }

    async #attempt() {
        this.#setStatus('connecting');
        let transport;
        try { transport = await this.#openTransport(); }
        catch (error) {
            console.warn('[sync] could not open a connection:', error.message);
            this.#setStatus('offline');
            this.#scheduleRetry();
            return;
        }
        if (!this.#wanted) { transport.close(); return; }
        this.#transport = transport;

        transport.onopen = () => {
            this.#attempts = 0;
            const hello = encoding.createEncoder();
            encoding.writeVarUint(hello, MSG_SYNC);
            syncProtocol.writeSyncStep1(hello, this.#ydoc);
            this.#send(hello);
            if (this.#awareness.getLocalState() !== null) {
                const presence = encoding.createEncoder();
                encoding.writeVarUint(presence, MSG_AWARENESS);
                encoding.writeVarUint8Array(presence, awarenessProtocol.encodeAwarenessUpdate(this.#awareness, [this.#ydoc.clientID]));
                this.#send(presence);
            }
        };
        transport.onmessage = data => this.#receive(data);
        transport.onclose = ({ code = 1006, reason = '' } = {}) => {
            this.#transport = null;
            //Everyone else's presence is unknown while disconnected
            const others = [...this.#awareness.getStates().keys()].filter(id => id !== this.#ydoc.clientID);
            awarenessProtocol.removeAwarenessStates(this.#awareness, others, this);
            if (NO_RETRY_CODES.has(code)) {
                this.#wanted = false;
                this.#setStatus('error');
                this.#onControl({ type: 'closed', code, reason });
                return;
            }
            this.#setStatus('offline');
            if (this.#wanted) this.#scheduleRetry();
        };
    }

    #scheduleRetry() {
        if (!this.#wanted || this.#timer) return;
        const delay = Math.min(this.#maxDelay, this.#minDelay * 2 ** this.#attempts++);
        this.#timer = setTimeout(() => { this.#timer = null; if (this.#wanted && !this.#transport) this.#attempt(); }, delay);
    }

    #receive(data) {
        const decoder = decoding.createDecoder(data instanceof Uint8Array ? data : new Uint8Array(data));
        switch (decoding.readVarUint(decoder)) {
            case MSG_SYNC: {
                const reply = encoding.createEncoder();
                encoding.writeVarUint(reply, MSG_SYNC);
                const type = syncProtocol.readSyncMessage(decoder, reply, this.#ydoc, this);
                if (type === syncProtocol.messageYjsSyncStep2) this.#setStatus('synced');
                if (encoding.length(reply) > 1) this.#send(reply);
                break;
            }
            case MSG_AWARENESS:
                awarenessProtocol.applyAwarenessUpdate(this.#awareness, decoding.readVarUint8Array(decoder), this);
                break;
            case MSG_CONTROL:
                try { this.#onControl(JSON.parse(decoding.readVarString(decoder))); } catch (error) { console.error('[sync] bad control message:', error); }
                break;
        }
    }

    //Forces a reconnect (e.g. with a fresh token after a role change)
    reconnect() { this.#transport?.close(4000, 'reconnect'); }

    disconnect() {
        this.#wanted = false;
        clearTimeout(this.#timer);
        this.#timer = null;
        this.#transport?.close(1000, 'bye');
        this.#setStatus('offline');
    }

    destroy() {
        this.disconnect();
        this.#listeners.forEach(stop => stop());
    }
}

//Wraps a browser WebSocket as a transport. The short-lived token travels in the query string (R-API-3).
export function webSocketTransport(url, token) {
    const socket = new WebSocket(`${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`);
    socket.binaryType = 'arraybuffer';
    const transport = {
        send: bytes => { if (socket.readyState === WebSocket.OPEN) socket.send(bytes); },
        close: (code, reason) => socket.close(code, reason),
        onopen: null, onmessage: null, onclose: null,
    };
    socket.onopen = () => transport.onopen?.();
    socket.onmessage = event => transport.onmessage?.(new Uint8Array(event.data));
    socket.onclose = event => transport.onclose?.({ code: event.code, reason: event.reason });
    return transport;
}
