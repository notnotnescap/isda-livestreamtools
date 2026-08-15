/**
 * OBS Status Monitor — polls OBS WebSocket for recording/streaming state
 * and performance stats (CPU, FPS, render time, frames, bitrate).
 *
 * Reads OBS_CONFIG from main.js (global) — no separate credentials.
 */

(function () {
    const POLL_MS = 500;

    const section = document.getElementById('obs-status-section');
    if (!section) return console.warn('obs-status: section not found');

    // --- Status row --------------------------------------------------------
    const obsDot = document.getElementById('obs-dot');
    const obsLabel = document.getElementById('obs-label');
    const recordingDot = document.getElementById('obs-recording-dot');
    const recordingLabel = document.getElementById('obs-recording-label');
    const streamingDot = document.getElementById('obs-streaming-dot');
    const streamingLabel = document.getElementById('obs-streaming-label');
    const sceneLabel = document.getElementById('obs-scene-label');

    // --- Stats row ---------------------------------------------------------
    const statsRow = document.getElementById('obs-stats-row');
    const cpuLabel = document.getElementById('obs-cpu');
    const fpsLabel = document.getElementById('obs-fps');
    const renderLabel = document.getElementById('obs-render');
    const framesLabel = document.getElementById('obs-frames');
    const bitrateLabel = document.getElementById('obs-bitrate');

    // Show "Connecting..." immediately
    obsLabel.textContent = 'Connecting...';
    obsLabel.className = 'text-yellow-400';

    // Bitrate tracking
    let lastBytes = 0;
    let lastBytesTime = 0;

    function formatTimecode(tc) {
        if (!tc) return '';
        const parts = tc.split(':');
        if (parts.length >= 3) {
            return parts.slice(1).join(':').split('.')[0];
        }
        return tc.split('.')[0];
    }

    function formatBitrate(bytesPerSec) {
        const kbps = (bytesPerSec * 8) / 1000;
        if (kbps >= 1000) return (kbps / 1000).toFixed(1) + ' Mb/s';
        return Math.round(kbps) + ' kb/s';
    }

    let lastErrorShown = '';

    function setDisconnected(msg) {
        obsDot.className = 'inline-block w-2 h-2 rounded-full bg-red-400 mr-1';
        obsLabel.textContent = msg || 'Disconnected';
        obsLabel.className = 'text-red-400';
        recordingDot.className = 'inline-block w-2 h-2 rounded-full bg-gray-600 mr-1';
        recordingLabel.textContent = '—';
        recordingLabel.className = 'text-gray-500';
        streamingDot.className = 'inline-block w-2 h-2 rounded-full bg-gray-600 mr-1';
        streamingLabel.textContent = '—';
        streamingLabel.className = 'text-gray-500';
        if (statsRow) statsRow.style.display = 'none';
    }

    async function poll() {
        const cfg = window.OBS_CONFIG || (typeof OBS_CONFIG !== 'undefined' ? OBS_CONFIG : null);
        if (!cfg) {
            setDisconnected('No config');
            setTimeout(poll, POLL_MS);
            return;
        }

        let obs;
        try {
            obs = new OBSWebSocket();
            await obs.connect(`ws://${cfg.ip}:${cfg.port}`, cfg.password, {
                rpcVersion: 1,
                eventSubscriptions: 0,
            });
            lastErrorShown = '';

            obsDot.className = 'inline-block w-2 h-2 rounded-full bg-green-400 mr-1';
            obsLabel.textContent = 'Connected';
            obsLabel.className = 'text-green-400';

            const [streamStatus, recordStatus, scene, stats] = await Promise.all([
                obs.call('GetStreamStatus').catch(() => null),
                obs.call('GetRecordStatus').catch(() => null),
                obs.call('GetCurrentProgramScene').catch(() => null),
                obs.call('GetStats').catch(() => null),
            ]);

            // --- Recording --------------------------------------------------
            if (recordStatus && recordStatus.outputActive) {
                recordingDot.className = 'inline-block w-2 h-2 rounded-full bg-red-500 mr-1 obs-pulse';
                const tc = formatTimecode(recordStatus.outputTimecode);
                recordingLabel.textContent = 'Recording' + (tc ? ' ' + tc : '');
                recordingLabel.className = 'text-red-400';
            } else {
                recordingDot.className = 'inline-block w-2 h-2 rounded-full bg-gray-600 mr-1';
                recordingLabel.textContent = 'Idle';
                recordingLabel.className = 'text-gray-500';
            }

            // --- Streaming --------------------------------------------------
            if (streamStatus && streamStatus.outputActive) {
                streamingDot.className = 'inline-block w-2 h-2 rounded-full bg-red-500 mr-1 obs-pulse';
                const tc = formatTimecode(streamStatus.outputTimecode);
                streamingLabel.textContent = 'Live' + (tc ? ' ' + tc : '');
                streamingLabel.className = 'text-red-400';

                // Bitrate from outputBytes delta
                const now = Date.now();
                if (lastBytesTime > 0 && streamStatus.outputBytes != null) {
                    const deltaBytes = streamStatus.outputBytes - lastBytes;
                    const deltaMs = now - lastBytesTime;
                    if (deltaMs > 0 && deltaBytes >= 0) {
                        lastBytes = streamStatus.outputBytes;
                        lastBytesTime = now;
                        bitrateLabel.textContent = formatBitrate(deltaBytes / (deltaMs / 1000));
                    }
                } else if (streamStatus.outputBytes != null) {
                    lastBytes = streamStatus.outputBytes;
                    lastBytesTime = now;
                    bitrateLabel.textContent = '…';
                }
            } else {
                streamingDot.className = 'inline-block w-2 h-2 rounded-full bg-gray-600 mr-1';
                streamingLabel.textContent = 'Offline';
                streamingLabel.className = 'text-gray-500';
                lastBytes = 0;
                lastBytesTime = 0;
                bitrateLabel.textContent = '—';
            }

            // --- Scene ------------------------------------------------------
            if (scene && scene.sceneName) {
                sceneLabel.textContent = scene.sceneName;
                sceneLabel.className = 'text-gray-300';
            }

            // --- Performance stats ------------------------------------------
            if (stats) {
                if (statsRow) statsRow.style.display = '';

                cpuLabel.textContent = (stats.cpuUsage != null ? stats.cpuUsage.toFixed(1) + '%' : '—');
                fpsLabel.textContent = (stats.activeFps != null ? Math.round(stats.activeFps) : '—');
                renderLabel.textContent = (stats.averageFrameRenderTime != null
                    ? stats.averageFrameRenderTime.toFixed(1) + 'ms' : '—');

                const skipped = (stats.renderSkippedFrames || 0) + (stats.outputSkippedFrames || 0);
                const total = (stats.renderTotalFrames || 0) + (stats.outputTotalFrames || 0);
                framesLabel.textContent = total ? skipped + '/' + total : '—';
                framesLabel.className = skipped > 0 ? 'text-yellow-400' : 'text-gray-500';
            }

        } catch (err) {
            const msg = err.message || String(err);
            if (msg !== lastErrorShown) {
                lastErrorShown = msg;
                console.warn('OBS status error:', msg);
            }
            setDisconnected('Error');
        } finally {
            try { obs?.disconnect(); } catch (_) { /* ignore */ }
            setTimeout(poll, POLL_MS);
        }
    }

    setTimeout(poll, 500);
})();