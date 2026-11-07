/**
 * ISDA Live Stream Tools — Main UI JavaScript
 *
 * OBS commands go through the shared persistent connection in obs-client.js
 * (window.OBSClient); OBS config is injected by the Flask template from the
 * centralized ``config.yaml``.
 */


// ---------------------------------------------------------------------------
// Timestamp class
// ---------------------------------------------------------------------------

class Timestamp {
    constructor(timeInSeconds, eventName) {
        this.timeInSeconds = Math.max(0, timeInSeconds);
        this.eventName = eventName;
        this.id = Date.now() + Math.random();
    }

    formatTime() {
        const h = Math.floor(this.timeInSeconds / 3600).toString().padStart(2, '0');
        const m = Math.floor((this.timeInSeconds % 3600) / 60).toString().padStart(2, '0');
        const s = Math.floor(this.timeInSeconds % 60).toString().padStart(2, '0');
        return h === '00' ? `${m}:${s}` : `${h}:${m}:${s}`;
    }

    toString() {
        return `${this.formatTime()} - ${this.eventName}`;
    }
}


// ---------------------------------------------------------------------------
// Timestamp persistence (localStorage)
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'isda-timestamps';

function saveTimestamps() {
    const data = {
        date: new Date().toISOString().split('T')[0],
        timestamps: state.timestamps.map(ts => ({
            timeInSeconds: ts.timeInSeconds,
            eventName: ts.eventName,
            id: ts.id,
        })),
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

function loadSavedTimestamps() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const data = JSON.parse(raw);
        if (!data.timestamps || !Array.isArray(data.timestamps)) return null;
        return data;
    } catch {
        return null;
    }
}

function clearSavedTimestamps() {
    localStorage.removeItem(STORAGE_KEY);
    state.timestamps = [new Timestamp(0, 'Live Stream Countdown')];
    renderTimestamps();
    saveTimestamps();
}


// ---------------------------------------------------------------------------
// OBS WebSocket helpers
// ---------------------------------------------------------------------------

async function obsCommand(command, params = {}) {
    try {
        await OBSClient.call(command, params);
        console.log(`OBS: ${command} ✓`);
        return true;
    } catch (error) {
        console.error(`OBS: ${command} ✗`, error);
        return false;
    }
}

async function startStream(switchToProjection = true) {
    if (state.streamStarted) return;
    console.log('Starting stream and recording...');

    if (switchToProjection) {
        await obsCommand('SetCurrentProgramScene', { sceneName: 'Projector' });
        await new Promise(resolve => setTimeout(resolve, 1000));
    }

    const streamStarted = await obsCommand('StartStream');
    if (streamStarted) {
        await obsCommand('StartRecord');
        state.streamStarted = true;
        state.autoStartEnabled = false;
        updateTimer();
    }
}


// ---------------------------------------------------------------------------
// Overlay trigger (talks to Flask backend)
// ---------------------------------------------------------------------------

async function triggerOverlay(text, duration = 3) {
    // Support sequences: "Hello | Welcome | Thanks" → 3 sequential overlays
    const parts = text.split(' | ');
    for (let i = 0; i < parts.length; i++) {
        if (i > 0) await new Promise(r => setTimeout(r, (duration + 0.5) * 1000));
        try {
            await fetch('/api/trigger-overlay', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: parts[i].trim(), duration })
            });
        } catch (error) {
            console.error('Failed to trigger overlay:', error);
        }
    }
}


// ---------------------------------------------------------------------------
// DOM references (populated on DOMContentLoaded)
// ---------------------------------------------------------------------------

let autostartInput;
let autostartSummary;
let timestampsContainer;
let eventButtons;
let toggleAutostartButton;
let customTextButton;
let copyTimestampsButton;
let clearTimestampsButton;
let overlayTriggerButtons;

const liveTimestampElement = document.createElement('div');
const liveTimeSpan = document.createElement('span');


// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
    autostartTime: null,
    autoStartEnabled: true,
    timerInterval: null,
    streamStarted: false,
    timestamps: [],
};


// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderTimestamps() {
    timestampsContainer.innerHTML = '';

    state.timestamps.forEach(ts => {
        const entry = document.createElement('div');
        entry.className = 'flex space-x-1 items-center';
        entry.innerHTML = `<button data-id="${ts.id}" class="py-0 px-1">-</button> <span>${ts.toString()}</span>`;
        timestampsContainer.appendChild(entry);
    });

    timestampsContainer.appendChild(liveTimestampElement);
    timestampsContainer.scrollTop = timestampsContainer.scrollHeight;
    updateButtonStyles();
}

function updateButtonStyles() {
    const existingEvents = new Set(state.timestamps.map(ts => ts.eventName));
    eventButtons.forEach(btn => {
        btn.classList.toggle('darker', existingEvents.has(btn.textContent.trim()));
    });
}

function setupLiveTimestamp() {
    liveTimeSpan.id = 'live-time';
    liveTimeSpan.textContent = '00:00';

    liveTimestampElement.id = 'live-timestamp';
    liveTimestampElement.className = 'flex space-x-1 items-center text-gray-400';
    liveTimestampElement.innerHTML = '<button class="py-0 px-1 cursor-not-allowed" disabled>&nbsp;</button>';
    liveTimestampElement.appendChild(liveTimeSpan);
    liveTimestampElement.insertAdjacentHTML('beforeend', '<span>&nbsp;-&nbsp;</span>');
    timestampsContainer.appendChild(liveTimestampElement);
}

function setAutostartTime() {
    const [hours, minutes] = autostartInput.value.split(':');
    const now = new Date();
    state.autostartTime = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hours, minutes, 0);

    if (state.autostartTime < now) {
        state.autoStartEnabled = false;
    }
    updateTimer();
}

function formatTime(totalSeconds) {
    const h = Math.floor(totalSeconds / 3600).toString().padStart(2, '0');
    const m = Math.floor((totalSeconds % 3600) / 60).toString().padStart(2, '0');
    const s = Math.floor(totalSeconds % 60).toString().padStart(2, '0');
    return h === '00' ? `${m}:${s}` : `${h}:${m}:${s}`;
}

function updateTimer() {
    const now = new Date();
    const remainingSeconds = Math.floor((state.autostartTime - now) / 1000);

    if (state.streamStarted) {
        autostartSummary.textContent = 'Auto Start: Started';
        toggleAutostartButton.textContent = 'Stream Started';
        toggleAutostartButton.disabled = true;
        toggleAutostartButton.classList.add('darker');
        const elapsedSeconds = -remainingSeconds;
        liveTimeSpan.textContent = formatTime(elapsedSeconds);
        return;
    }

    if (!state.autostartTime || !state.autoStartEnabled) {
        if (state.autostartTime && state.autostartTime < now) {
            autostartSummary.textContent = 'Auto Start: Start time passed';
            toggleAutostartButton.textContent = 'Start Now';
        } else {
            autostartSummary.textContent = 'Auto Start: Disabled';
            toggleAutostartButton.textContent = 'Enable Auto Start';
        }
        return;
    } else {
        toggleAutostartButton.textContent = 'Disable Auto Start';
    }

    if (remainingSeconds > 0) {
        autostartSummary.textContent = `Auto Start: Starting in ${formatTime(remainingSeconds)}`;
        liveTimeSpan.textContent = '00:00';
        liveTimestampElement.style.display = 'none';
    } else {
        if (state.autoStartEnabled) {
            startStream();
            if (autostartSummary && autostartSummary.parentElement) {
                autostartSummary.parentElement.open = false;
            }
        }
        autostartSummary.textContent = 'Auto Start: Started';
        const elapsedSeconds = -remainingSeconds;
        liveTimeSpan.textContent = formatTime(elapsedSeconds);
        liveTimestampElement.style.display = 'flex';
    }
}

function toggleAutostart() {
    if (toggleAutostartButton.textContent === 'Start Now') {
        const now = new Date();
        state.autostartTime = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), now.getMinutes(), now.getSeconds());
        startStream(false);
    } else {
        state.autoStartEnabled = !state.autoStartEnabled;
    }
    updateTimer();
}

function addTimestamp(eventName, timestampDate = null) {
    if (!state.autostartTime) {
        setAutostartTime();
    }

    const now = timestampDate || new Date();
    const elapsedSeconds = Math.floor((now - state.autostartTime) / 1000);

    const newTimestamp = new Timestamp(elapsedSeconds, eventName);
    state.timestamps.push(newTimestamp);
    state.timestamps.sort((a, b) => a.timeInSeconds - b.timeInSeconds);

    renderTimestamps();
    saveTimestamps();
}


// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    // Grab DOM elements
    autostartInput = document.getElementById('autostart-time');
    autostartSummary = document.querySelector('.auto-start summary');
    timestampsContainer = document.getElementById('timestamps-container');
    eventButtons = document.querySelectorAll('.timestamp-events button');
    toggleAutostartButton = document.querySelector('.auto-start-form button');
    customTextButton = document.getElementById('custom-text-btn');
    copyTimestampsButton = document.getElementById('copy-timestamps-btn');
    clearTimestampsButton = document.getElementById('clear-timestamps-btn');
    overlayTriggerButtons = document.querySelectorAll('.overlay-trigger');

    // ── Timestamp event buttons ──────────────────────────────────────────

    eventButtons.forEach((button, index) => {
        button.addEventListener('click', () => {
            addTimestamp(button.textContent.trim());
            for (let i = index + 1; i < eventButtons.length; i++) {
                if (!eventButtons[i].classList.contains('darker')) {
                    eventButtons[i].scrollIntoView({ behavior: 'smooth', block: 'center' });
                    break;
                }
            }
        });
    });

    customTextButton.addEventListener('click', () => {
        const clickTime = new Date();
        const eventName = prompt('Enter custom timestamp text:');
        if (eventName && eventName.trim() !== '') {
            addTimestamp(eventName.trim(), clickTime);
        }
    });

    copyTimestampsButton.addEventListener('click', () => {
        const timestampString = state.timestamps.map(ts => ts.toString()).join('\n');
        navigator.clipboard.writeText(timestampString).then(() => {
            const originalText = copyTimestampsButton.textContent;
            copyTimestampsButton.textContent = 'Copied!';
            setTimeout(() => { copyTimestampsButton.textContent = originalText; }, 2000);
        }).catch(err => {
            console.error('Failed to copy timestamps:', err);
            alert('Could not copy timestamps to clipboard.');
        });
    });

    timestampsContainer.addEventListener('click', (e) => {
        if (e.target.tagName === 'BUTTON' && e.target.dataset.id) {
            const timestampId = e.target.dataset.id;
            state.timestamps = state.timestamps.filter(ts => ts.id.toString() !== timestampId);
            renderTimestamps();
            saveTimestamps();
        }
    });

    clearTimestampsButton.addEventListener('click', () => {
        if (state.timestamps.length <= 1) return;
        if (confirm(`Clear all ${state.timestamps.length - 1} timestamps?`)) {
            clearSavedTimestamps();
        }
    });

    // ── Autostart ────────────────────────────────────────────────────────

    toggleAutostartButton.addEventListener('click', (e) => {
        e.preventDefault();
        toggleAutostart();
    });

    autostartInput.addEventListener('change', setAutostartTime);

    // ── Overlay triggers ─────────────────────────────────────────────────

    overlayTriggerButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const text = btn.dataset.text || 'Welcome';
            const duration = parseInt(btn.dataset.duration) || 8;
            triggerOverlay(text, duration);
        });
    });

    const overlayCustomBtn = document.getElementById('overlay-custom-btn');
    if (overlayCustomBtn) {
        overlayCustomBtn.addEventListener('click', () => {
            const text = prompt('Overlay text:');
            if (text && text.trim() !== '') {
                triggerOverlay(text.trim());
            }
        });
    }

    // ── Bootstrap ────────────────────────────────────────────────────────

    const saved = loadSavedTimestamps();
    const today = new Date().toISOString().split('T')[0];

    if (saved && saved.timestamps.length > 0) {
        if (saved.date !== today) {
            const shouldClear = confirm(
                `Clear ${saved.timestamps.length} timestamps from ${saved.date}?`
            );
            if (shouldClear) {
                clearSavedTimestamps();
            } else {
                state.timestamps = saved.timestamps.map(
                    ts => new Timestamp(ts.timeInSeconds, ts.eventName)
                );
            }
        } else {
            state.timestamps = saved.timestamps.map(
                ts => new Timestamp(ts.timeInSeconds, ts.eventName)
            );
        }
    } else {
        state.timestamps.push(new Timestamp(0, 'Live Stream Countdown'));
    }

    setupLiveTimestamp();
    renderTimestamps();
    setAutostartTime();
    saveTimestamps();
    state.timerInterval = setInterval(updateTimer, 1000);
});