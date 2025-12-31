/**
 * WebSocket connection manager for Restim Web UI
 */

class RestimWebSocket {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 2000;
        this.listeners = new Map();
        this.connected = false;
        this.wsPort = null;
    }

    /**
     * Connect to the WebSocket server
     * @param {string} host - Server host (defaults to current page host)
     * @param {number} port - WebSocket port
     */
    connect(host = null, port = null) {
        if (host === null) {
            host = window.location.hostname || 'localhost';
        }
        if (port === null) {
            // WebSocket port is HTTP port + 1
            const httpPort = parseInt(window.location.port) || 8080;
            port = httpPort + 1;
        }
        this.wsPort = port;

        const url = `ws://${host}:${port}`;
        console.log(`Connecting to WebSocket: ${url}`);
        this.emit('connecting');

        try {
            this.ws = new WebSocket(url);
        } catch (e) {
            console.error('WebSocket creation failed:', e);
            this.attemptReconnect();
            return;
        }

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.connected = true;
            this.reconnectAttempts = 0;
            this.emit('connected');
        };

        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                this.emit(message.type, message.payload, message.timestamp);
            } catch (e) {
                console.error('Failed to parse message:', e, event.data);
            }
        };

        this.ws.onclose = (event) => {
            console.log('WebSocket closed:', event.code, event.reason);
            this.connected = false;
            this.emit('disconnected');
            this.attemptReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    /**
     * Send a message to the server
     * @param {string} type - Message type
     * @param {object} payload - Message payload
     */
    send(type, payload = {}) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            console.warn('WebSocket not connected, cannot send:', type);
            return false;
        }

        const message = {
            type: type,
            payload: payload,
            timestamp: Date.now() / 1000
        };

        try {
            this.ws.send(JSON.stringify(message));
            return true;
        } catch (e) {
            console.error('Failed to send message:', e);
            return false;
        }
    }

    /**
     * Send authentication message
     * @param {string} username
     * @param {string} password
     */
    authenticate(username, password) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        const authMessage = {
            type: 'auth',
            username: username,
            password: password
        };

        try {
            this.ws.send(JSON.stringify(authMessage));
            return true;
        } catch (e) {
            console.error('Failed to send auth:', e);
            return false;
        }
    }

    /**
     * Request full state from server
     */
    getState() {
        return this.send('get_state');
    }

    /**
     * Register an event listener
     * @param {string} event - Event name
     * @param {function} callback - Callback function
     */
    on(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, []);
        }
        this.listeners.get(event).push(callback);
    }

    /**
     * Remove an event listener
     * @param {string} event - Event name
     * @param {function} callback - Callback function to remove
     */
    off(event, callback) {
        if (!this.listeners.has(event)) return;
        const callbacks = this.listeners.get(event);
        const index = callbacks.indexOf(callback);
        if (index > -1) {
            callbacks.splice(index, 1);
        }
    }

    /**
     * Emit an event to all listeners
     * @param {string} event - Event name
     * @param {...any} args - Event arguments
     */
    emit(event, ...args) {
        const callbacks = this.listeners.get(event) || [];
        callbacks.forEach(cb => {
            try {
                cb(...args);
            } catch (e) {
                console.error(`Error in ${event} listener:`, e);
            }
        });
    }

    /**
     * Attempt to reconnect after connection loss
     */
    attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('Max reconnect attempts reached');
            this.emit('reconnect_failed');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.min(this.reconnectAttempts, 5);
        console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

        setTimeout(() => {
            if (!this.connected) {
                this.connect(null, this.wsPort);
            }
        }, delay);
    }

    /**
     * Close the connection
     */
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.connected = false;
    }

    /**
     * Check if connected
     * @returns {boolean}
     */
    isConnected() {
        return this.connected && this.ws && this.ws.readyState === WebSocket.OPEN;
    }
}

// Global instance
const restimWS = new RestimWebSocket();
