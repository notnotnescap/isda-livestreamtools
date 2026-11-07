/**
 * OBS WebSocket client — a single persistent connection to OBS shared by the
 * whole page for the duration of the session.
 *
 * Previously, obs-status.js opened a fresh OBS WebSocket every 500ms and
 * main.js opened one per command, which spammed OBS's connection log. This
 * module keeps one connection open and reconnects automatically (with
 * exponential backoff) if it drops.
 *
 * Exposed as `window.OBSClient`:
 *   - `call(command, params)`  — run an OBS request on the shared connection
 *   - `isConnected()`          — true when the connection is live
 *   - `isConnecting()`         — true while a connect attempt is in flight
 *   - `on(event, cb)`          — subscribe to 'connecting' | 'connected' | 'disconnected'
 */

(function (global) {
    'use strict';

    const RETRY_BASE_MS = 1000;
    const RETRY_MAX_MS = 15000;

    let socket = null;          // current identified OBSWebSocket instance
    let connectPromise = null;  // in-flight connect attempt (shared by callers)
    let retryTimer = null;
    let retryDelay = RETRY_BASE_MS;

    const listeners = { connecting: [], connected: [], disconnected: [] };

    function getConfig() {
        return global.OBS_CONFIG || { ip: '127.0.0.1', port: 4455, password: '' };
    }

    function on(event, cb) {
        (listeners[event] || (listeners[event] = [])).push(cb);
        return () => {
            listeners[event] = listeners[event].filter(fn => fn !== cb);
        };
    }

    function emit(event, ...args) {
        (listeners[event] || []).forEach(fn => {
            try { fn(...args); } catch (err) { console.error('OBS listener error:', err); }
        });
    }

    function isConnected() {
        return !!(socket && socket.identified);
    }

    function isConnecting() {
        return !!connectPromise;
    }

    async function doConnect() {
        emit('connecting');
        const cfg = getConfig();
        const obs = new OBSWebSocket();
        obs.on('ConnectionClosed', () => {
            // Only react to the close of the socket we currently consider live.
            if (socket === obs) {
                socket = null;
                emit('disconnected');
                scheduleReconnect();
            }
        });
        try {
            await obs.connect(`ws://${cfg.ip}:${cfg.port}`, cfg.password, {
                rpcVersion: 1,
                eventSubscriptions: 0,
            });
            socket = obs;
            retryDelay = RETRY_BASE_MS;
            emit('connected');
            return obs;
        } catch (err) {
            socket = null;
            emit('disconnected');
            scheduleReconnect();
            throw err;
        }
    }

    function ensureConnected() {
        if (isConnected()) return Promise.resolve(socket);
        if (connectPromise) return connectPromise;
        connectPromise = doConnect().finally(() => { connectPromise = null; });
        return connectPromise;
    }

    function scheduleReconnect() {
        clearTimeout(retryTimer);
        retryTimer = setTimeout(() => {
            ensureConnected().catch(() => { /* retry is rescheduled by doConnect */ });
        }, retryDelay);
        retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
    }

    async function call(command, params = {}) {
        const obs = await ensureConnected();
        return obs.call(command, params);
    }

    // Start the initial connection; state changes are surfaced via events.
    ensureConnected().catch(() => { /* handled by UI listeners */ });

    global.OBSClient = { call, isConnected, isConnecting, on };
})(window);
