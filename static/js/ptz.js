/**
 * PTZ Controller UI — polls /api/ptz/status and renders:
 * - Controller connection indicator
 * - Current camera
 * - Axis sliders
 * - Last action text
 */

(function () {
    const POLL_MS = 100;

    // Only run if the PTZ section exists on the page
    const section = document.getElementById('ptz-section');
    if (!section) return;

    // DOM refs
    const controllerDot = document.getElementById('ptz-controller-dot');
    const controllerLabel = document.getElementById('ptz-controller-label');
    const cameraLabel = document.getElementById('ptz-camera-label');
    const actionText = document.getElementById('ptz-action-text');
    const rawText = document.getElementById('ptz-raw-text');
    const sliderEls = {
        PAN: document.getElementById('ptz-slider-pan'),
        TILT: document.getElementById('ptz-slider-tilt'),
        ZOOM: document.getElementById('ptz-slider-zoom'),
        PAN_LEFT: document.getElementById('ptz-slider-pan-left'),
        PAN_RIGHT: document.getElementById('ptz-slider-pan-right'),
    };

    function updateSlider(id, value) {
        const el = sliderEls[id];
        if (!el) return;
        // value is -1..1, map to 0..100%
        const pct = ((value + 1) / 2) * 100;
        const fill = el.querySelector('.ptz-slider-fill');
        if (fill) fill.style.width = pct + '%';
    }

    async function poll() {
        try {
            const r = await fetch('/api/ptz/status');
            const s = await r.json();

            // Controller status
            if (s.controller_connected) {
                controllerDot.className = 'inline-block w-2 h-2 rounded-full bg-green-400 mr-1';
                controllerLabel.textContent = s.controller_name || 'Connected';
                controllerLabel.className = 'text-green-400';
            } else {
                controllerDot.className = 'inline-block w-2 h-2 rounded-full bg-red-400 mr-1';
                controllerLabel.textContent = 'Disconnected';
                controllerLabel.className = 'text-red-400';
            }

            // Camera
            const camStatus = s.camera_connected ? 'text-green-400' : 'text-red-400';
            cameraLabel.textContent = s.camera_ip || '—';
            cameraLabel.className = camStatus;

            // Action
            actionText.textContent = s.last_action || '—';
            rawText.textContent = s.last_raw_input || '—';

            // Sliders
            if (s.axes) {
                for (const [name, value] of Object.entries(s.axes)) {
                    updateSlider(name, value);
                }
            }
        } catch (err) {
            // Ignore poll errors — will retry on next tick
        }
        setTimeout(poll, POLL_MS);
    }

    poll();
})();