/**
 * WebSocket connection lifecycle manager.
 * Handles connect, disconnect, send, and message dispatch via EventBus.
 */
import AppState from '../core/AppState.js';
import eventBus from '../core/EventBus.js';

class WebSocketService {
    constructor() {
        this._ws = null;
        this._reconnectTimer = null;
        this._reconnectAttempts = 0;
        this._maxReconnectAttempts = 10;
        this._baseReconnectDelay = 1000;  // 1s base
        this._maxReconnectDelay = 30000;  // max 30s
        this._lastHost = null;
        this._lastUserId = null;
        this._isManualDisconnect = false; // true when user clicks disconnect
    }

    /**
     * Connect to the voice WebSocket endpoint.
     * @param {string} host - Server host (domain:port)
     * @param {string} userId - User ID
     * @param {boolean} isReconnect - Whether this is a reconnection attempt
     */
    connect(host, userId, isReconnect = false) {
        // Save credentials for reconnection
        this._lastHost = host;
        this._lastUserId = userId;

        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let wsUrl = `${wsProtocol}//${host}/ws/v1/voice/${userId}`;

        // Attach JWT token if available
        const token = AppState.auth?.token;
        if (token) {
            wsUrl += `?token=${encodeURIComponent(token)}`;
        }

        if (!isReconnect) {
            this._reconnectAttempts = 0;
            this._isManualDisconnect = false;
        }

        eventBus.emit('log', {
            msg: isReconnect ? `重连中 (${this._reconnectAttempts}/${this._maxReconnectAttempts}): ${wsUrl}` : `连接中: ${wsUrl}`,
            type: 'info'
        });

        try {
            this._ws = new WebSocket(wsUrl);
            AppState.ws = this._ws;

            this._ws.onopen = () => {
                AppState.isConnected = true;
                this._reconnectAttempts = 0;
                this._clearReconnectTimer();
                eventBus.emit('ws:connected');
                eventBus.emit('log', { msg: isReconnect ? '重连成功' : '已连接', type: 'success' });
            };

            this._ws.onmessage = (e) => {
                if (e.data instanceof Blob) {
                    eventBus.emit('ws:audio', e.data);
                } else {
                    try {
                        const msg = JSON.parse(e.data);
                        eventBus.emit('ws:message', msg);
                    } catch (err) {
                        eventBus.emit('log', { msg: '解析错误: ' + err.message, type: 'error' });
                    }
                }
            };

            this._ws.onerror = (err) => {
                eventBus.emit('log', { msg: '连接错误', type: 'error' });
                eventBus.emit('ws:error');
            };

            this._ws.onclose = (e) => {
                AppState.isConnected = false;
                AppState.ws = null;
                this._ws = null;
                eventBus.emit('ws:disconnected');
                eventBus.emit('log', { msg: '已断开', type: 'warning' });

                // Auto-reconnect if not manually disconnected
                if (!this._isManualDisconnect) {
                    this._scheduleReconnect();
                }
            };
        } catch (e) {
            eventBus.emit('log', { msg: '连接失败: ' + e.message, type: 'error' });
            if (!this._isManualDisconnect) {
                this._scheduleReconnect();
            }
        }
    }

    /**
     * Schedule reconnection with exponential backoff.
     */
    _scheduleReconnect() {
        if (this._reconnectAttempts >= this._maxReconnectAttempts) {
            eventBus.emit('log', { msg: '重连次数已达上限，停止重连', type: 'error' });
            return;
        }

        this._reconnectAttempts++;

        // Exponential backoff: 1s, 2s, 4s, 8s... capped at 30s
        const delay = Math.min(
            this._baseReconnectDelay * Math.pow(2, this._reconnectAttempts - 1),
            this._maxReconnectDelay
        );

        eventBus.emit('log', { msg: `${delay / 1000}秒后自动重连...`, type: 'info' });

        this._clearReconnectTimer();
        this._reconnectTimer = setTimeout(() => {
            if (this._lastHost && this._lastUserId) {
                this.connect(this._lastHost, this._lastUserId, true);
            }
        }, delay);
    }

    /**
     * Clear any pending reconnection timer.
     */
    _clearReconnectTimer() {
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
    }

    /**
     * Disconnect from WebSocket (manual - disables auto-reconnect).
     */
    disconnect() {
        this._isManualDisconnect = true;
        this._clearReconnectTimer();

        if (this._ws) {
            this.sendJSON({ type: 'close' });
            this._ws.close();
            this._ws = null;
            AppState.ws = null;
        }
    }

    /**
     * Send raw data (e.g. audio ArrayBuffer).
     */
    send(data) {
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            this._ws.send(data);
        }
    }

    /**
     * Send a JSON object.
     */
    sendJSON(obj) {
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            this._ws.send(JSON.stringify(obj));
        }
    }

    get isOpen() {
        return this._ws && this._ws.readyState === WebSocket.OPEN;
    }
}

export default new WebSocketService();
