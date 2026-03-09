/**
 * Lightweight publish/subscribe event bus for inter-module communication.
 * Avoids direct references between modules to prevent circular dependencies.
 */
class EventBus {
    constructor() {
        this._listeners = {};
    }

    /**
     * Subscribe to an event.
     * @param {string} event - Event name
     * @param {Function} handler - Callback function
     */
    on(event, handler) {
        if (!this._listeners[event]) {
            this._listeners[event] = [];
        }
        this._listeners[event].push(handler);
    }

    /**
     * Unsubscribe from an event.
     * @param {string} event - Event name
     * @param {Function} handler - Callback function to remove
     */
    off(event, handler) {
        const list = this._listeners[event];
        if (!list) return;
        this._listeners[event] = list.filter(h => h !== handler);
    }

    /**
     * Emit an event to all subscribers.
     * @param {string} event - Event name
     * @param {*} payload - Data to pass to handlers
     */
    emit(event, payload) {
        const list = this._listeners[event];
        if (!list) return;
        for (const handler of list) {
            try {
                handler(payload);
            } catch (e) {
                console.error(`EventBus handler error [${event}]:`, e);
            }
        }
    }
}

export default new EventBus();
