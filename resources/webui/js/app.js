/**
 * Main application logic for Restim Web UI
 */

// Controls
let positionCanvas;
let volumeSlider;
let carrierSlider;
let velocitySlider;
let patternSelect;
let alphaSlider, betaSlider, gammaSlider;
let pulseFreqSlider, pulseWidthSlider, pulseRiseSlider, pulseRandomSlider;
let vib1Enabled, vib1Freq, vib1Strength;
let vib2Enabled, vib2Freq, vib2Strength;

// Throttling for position updates
let positionThrottle = null;
const POSITION_THROTTLE_MS = 50;

// Initialize application
document.addEventListener('DOMContentLoaded', () => {
    setupCollapsibles();
    initControls();
    setupWebSocket();
});

function initControls() {
    // Position canvas
    positionCanvas = new PositionCanvas('position-canvas');
    positionCanvas.onPositionChange = (alpha, beta) => {
        updatePositionSliders(alpha, beta);
        sendPositionThrottled(alpha, beta, null);
    };

    // Position invert settings
    const invertH = document.getElementById('invert-horizontal');
    const invertV = document.getElementById('invert-vertical');

    // Load saved settings
    invertH.checked = localStorage.getItem('restim-invert-horizontal') === 'true';
    invertV.checked = localStorage.getItem('restim-invert-vertical') === 'true';
    positionCanvas.setInvert(invertH.checked, invertV.checked);

    invertH.addEventListener('change', () => {
        localStorage.setItem('restim-invert-horizontal', invertH.checked);
        positionCanvas.setInvert(invertH.checked, invertV.checked);
    });

    invertV.addEventListener('change', () => {
        localStorage.setItem('restim-invert-vertical', invertV.checked);
        positionCanvas.setInvert(invertH.checked, invertV.checked);
    });

    // Position sliders
    alphaSlider = new SliderControl('alpha', 'alpha-value', {
        scale: 0.01,
        format: v => v.toFixed(2)
    });
    alphaSlider.onChange = (v) => {
        positionCanvas.setPosition(v, betaSlider.getValue());
        sendPositionThrottled(v, null, null);
    };

    betaSlider = new SliderControl('beta', 'beta-value', {
        scale: 0.01,
        format: v => v.toFixed(2)
    });
    betaSlider.onChange = (v) => {
        positionCanvas.setPosition(alphaSlider.getValue(), v);
        sendPositionThrottled(null, v, null);
    };

    gammaSlider = new SliderControl('gamma', 'gamma-value', {
        scale: 0.01,
        format: v => v.toFixed(2)
    });
    gammaSlider.onChange = (v) => {
        sendPositionThrottled(null, null, v);
    };

    // Volume
    volumeSlider = new SliderControl('volume', 'volume-value', {
        format: v => `${Math.round(v)}%`
    });
    volumeSlider.onChange = (v) => {
        restimWS.send('set_volume', { value: v });
    };

    // Carrier
    carrierSlider = new SliderControl('carrier', 'carrier-value', {
        format: v => `${Math.round(v)} Hz`
    });
    carrierSlider.onChange = (v) => {
        restimWS.send('set_carrier', { frequency: v });
    };

    // Pattern
    patternSelect = new SelectControl('pattern-select');
    patternSelect.onChange = (v) => {
        restimWS.send('set_pattern', { name: v });
    };

    velocitySlider = new SliderControl('velocity', 'velocity-value', {
        format: v => v.toFixed(1)
    });
    velocitySlider.onChange = (v) => {
        restimWS.send('set_pattern', { velocity: v });
    };

    // Pulse settings
    pulseFreqSlider = new SliderControl('pulse-freq', 'pulse-freq-value', {
        format: v => `${Math.round(v)} Hz`
    });
    pulseFreqSlider.onChange = (v) => {
        restimWS.send('set_pulse_params', { frequency: v });
    };

    pulseWidthSlider = new SliderControl('pulse-width', 'pulse-width-value', {
        format: v => v.toFixed(1)
    });
    pulseWidthSlider.onChange = (v) => {
        restimWS.send('set_pulse_params', { width: v });
    };

    pulseRiseSlider = new SliderControl('pulse-rise', 'pulse-rise-value', {
        format: v => `${v.toFixed(1)} ms`
    });
    pulseRiseSlider.onChange = (v) => {
        restimWS.send('set_pulse_params', { riseTime: v });
    };

    pulseRandomSlider = new SliderControl('pulse-random', 'pulse-random-value', {
        format: v => `${Math.round(v)}%`
    });
    pulseRandomSlider.onChange = (v) => {
        restimWS.send('set_pulse_params', { intervalRandom: v });
    };

    // Vibration 1
    vib1Enabled = new CheckboxControl('vib1-enabled');
    vib1Enabled.onChange = (checked) => {
        restimWS.send('set_vibration', { channel: 1, enabled: checked });
    };

    vib1Freq = new SliderControl('vib1-freq', 'vib1-freq-value', {
        format: v => `${Math.round(v)} Hz`
    });
    vib1Freq.onChange = (v) => {
        restimWS.send('set_vibration', { channel: 1, frequency: v });
    };

    vib1Strength = new SliderControl('vib1-strength', 'vib1-strength-value', {
        format: v => `${Math.round(v)}%`
    });
    vib1Strength.onChange = (v) => {
        restimWS.send('set_vibration', { channel: 1, strength: v });
    };

    // Vibration 2
    vib2Enabled = new CheckboxControl('vib2-enabled');
    vib2Enabled.onChange = (checked) => {
        restimWS.send('set_vibration', { channel: 2, enabled: checked });
    };

    vib2Freq = new SliderControl('vib2-freq', 'vib2-freq-value', {
        format: v => `${Math.round(v)} Hz`
    });
    vib2Freq.onChange = (v) => {
        restimWS.send('set_vibration', { channel: 2, frequency: v });
    };

    vib2Strength = new SliderControl('vib2-strength', 'vib2-strength-value', {
        format: v => `${Math.round(v)}%`
    });
    vib2Strength.onChange = (v) => {
        restimWS.send('set_vibration', { channel: 2, strength: v });
    };

    // Play/Stop buttons
    document.getElementById('btn-play').addEventListener('click', () => {
        restimWS.send('play');
    });

    document.getElementById('btn-stop').addEventListener('click', () => {
        restimWS.send('stop');
    });

    // Gamepad setup
    initGamepad();
}

function initGamepad() {
    const enabledCheckbox = document.getElementById('gamepad-enabled');
    const deadZoneSlider = document.getElementById('gamepad-deadzone');
    const deadZoneValue = document.getElementById('gamepad-deadzone-value');
    const invertH = document.getElementById('gamepad-invert-h');
    const invertV = document.getElementById('gamepad-invert-v');
    const statusEl = document.getElementById('gamepad-status');

    // Load settings
    enabledCheckbox.checked = gamepadHandler.enabled;
    deadZoneSlider.value = gamepadHandler.settings.deadZone * 100;
    deadZoneValue.textContent = `${Math.round(gamepadHandler.settings.deadZone * 100)}%`;
    invertH.checked = gamepadHandler.settings.invertHorizontal;
    invertV.checked = gamepadHandler.settings.invertVertical;

    // Update connection status
    function updateGamepadStatus() {
        const connected = gamepadHandler.isConnected();
        if (connected) {
            const gp = gamepadHandler.getConnectedGamepad();
            statusEl.textContent = 'Connected';
            statusEl.className = 'gamepad-status connected';
            statusEl.title = gp ? gp.id : '';
        } else {
            statusEl.textContent = 'Not Connected';
            statusEl.className = 'gamepad-status disconnected';
            statusEl.title = '';
        }
    }
    updateGamepadStatus();

    // Gamepad connection callback
    gamepadHandler.onConnectionChange = (connected, id) => {
        updateGamepadStatus();
    };

    // Check periodically for gamepad (some browsers need this)
    setInterval(updateGamepadStatus, 1000);

    // Settings change handlers
    enabledCheckbox.addEventListener('change', () => {
        gamepadHandler.setEnabled(enabledCheckbox.checked);
    });

    deadZoneSlider.addEventListener('input', () => {
        const value = parseInt(deadZoneSlider.value) / 100;
        gamepadHandler.settings.deadZone = value;
        deadZoneValue.textContent = `${deadZoneSlider.value}%`;
        gamepadHandler.saveSettings();
    });

    invertH.addEventListener('change', () => {
        gamepadHandler.settings.invertHorizontal = invertH.checked;
        gamepadHandler.saveSettings();
    });

    invertV.addEventListener('change', () => {
        gamepadHandler.settings.invertVertical = invertV.checked;
        gamepadHandler.saveSettings();
    });

    // Gamepad action callbacks
    gamepadHandler.onPositionChange = (alpha, beta) => {
        positionCanvas.setPosition(alpha, beta);
        updatePositionSliders(alpha, beta);
        sendPositionThrottled(alpha, beta, null);
    };

    gamepadHandler.onCarrierChange = (delta) => {
        const current = carrierSlider.getValue();
        const newValue = Math.max(300, Math.min(2000, current + delta));
        carrierSlider.setValue(newValue);
        restimWS.send('set_carrier', { frequency: newValue });
    };

    gamepadHandler.onVolumeChange = (delta) => {
        const current = volumeSlider.getValue();
        const newValue = Math.max(0, Math.min(100, current + delta));
        volumeSlider.setValue(newValue);
        restimWS.send('set_volume', { value: newValue });
    };

    gamepadHandler.onPulseFreqChange = (delta) => {
        const current = pulseFreqSlider.getValue();
        const newValue = Math.max(1, Math.min(100, current + delta));
        pulseFreqSlider.setValue(newValue);
        restimWS.send('set_pulse_params', { frequency: newValue });
    };

    gamepadHandler.onPulseWidthChange = (delta) => {
        const current = pulseWidthSlider.getValue();
        const newValue = Math.max(1, Math.min(20, current + delta));
        pulseWidthSlider.setValue(newValue);
        restimWS.send('set_pulse_params', { width: newValue });
    };

    // Start polling if enabled and gamepad is connected
    if (gamepadHandler.enabled && gamepadHandler.isConnected()) {
        gamepadHandler.startPolling();
    }
}

function setupWebSocket() {
    // Connection status
    restimWS.on('connecting', () => {
        updateConnectionStatus('connecting');
    });

    restimWS.on('connected', () => {
        updateConnectionStatus('connected');
    });

    restimWS.on('disconnected', () => {
        updateConnectionStatus('disconnected');
    });

    // State updates
    restimWS.on('state_update', (payload) => {
        updateFullState(payload);
    });

    restimWS.on('position_update', (payload) => {
        updatePosition(payload.alpha, payload.beta, payload.gamma);
    });

    restimWS.on('play_state_update', (payload) => {
        updatePlayState(payload.state);
    });

    restimWS.on('error', (payload) => {
        console.error('Server error:', payload.error);
    });

    // Connect
    restimWS.connect();
}

function updateConnectionStatus(status) {
    const el = document.getElementById('connection-status');
    el.className = '';

    switch (status) {
        case 'connected':
            el.textContent = 'Connected';
            el.classList.add('status-connected');
            break;
        case 'disconnected':
            el.textContent = 'Disconnected';
            el.classList.add('status-disconnected');
            break;
        case 'connecting':
            el.textContent = 'Connecting...';
            el.classList.add('status-connecting');
            break;
    }
}

function updateFullState(state) {
    // Play state
    if (state.playState) {
        updatePlayState(state.playState);
    }

    // Position
    if (state.position) {
        updatePosition(state.position.alpha, state.position.beta, state.position.gamma);
    }

    // Volume
    if (state.volume) {
        volumeSlider.setValue(state.volume.master);
    }

    // Carrier
    if (state.carrier !== undefined) {
        carrierSlider.setValue(state.carrier);
    }

    // Pulse
    if (state.pulse) {
        if (state.pulse.carrier) carrierSlider.setValue(state.pulse.carrier);
        if (state.pulse.frequency) pulseFreqSlider.setValue(state.pulse.frequency);
        if (state.pulse.width) pulseWidthSlider.setValue(state.pulse.width);
        if (state.pulse.riseTime) pulseRiseSlider.setValue(state.pulse.riseTime);
        if (state.pulse.intervalRandom) pulseRandomSlider.setValue(state.pulse.intervalRandom);
    }

    // Pattern
    if (state.pattern) {
        if (state.pattern.available) {
            patternSelect.setOptions(state.pattern.available);
        }
        if (state.pattern.name) {
            patternSelect.setValue(state.pattern.name);
        }
        if (state.pattern.velocity) {
            velocitySlider.setValue(state.pattern.velocity);
        }
    }

    // Vibration
    if (state.vibration) {
        if (state.vibration.vibration1) {
            const v1 = state.vibration.vibration1;
            vib1Enabled.setChecked(v1.enabled);
            vib1Freq.setValue(v1.frequency);
            vib1Strength.setValue(v1.strength);
        }
        if (state.vibration.vibration2) {
            const v2 = state.vibration.vibration2;
            vib2Enabled.setChecked(v2.enabled);
            vib2Freq.setValue(v2.frequency);
            vib2Strength.setValue(v2.strength);
        }
    }

    // Device info
    if (state.device) {
        document.getElementById('device-type').textContent = `Type: ${state.device.type}`;
        document.getElementById('waveform-type').textContent = `Waveform: ${state.device.waveformType}`;
    }
}

function updatePosition(alpha, beta, gamma) {
    if (alpha !== undefined) {
        alphaSlider.setValue(alpha, true);
    }
    if (beta !== undefined) {
        betaSlider.setValue(beta, true);
    }
    if (gamma !== undefined) {
        gammaSlider.setValue(gamma, true);
    }

    // Update canvas
    if (alpha !== undefined && beta !== undefined) {
        positionCanvas.setPosition(alpha, beta);
    }
}

function updatePositionSliders(alpha, beta) {
    if (alpha !== undefined) {
        alphaSlider.setValue(alpha, true);
    }
    if (beta !== undefined) {
        betaSlider.setValue(beta, true);
    }
}

function updatePlayState(state) {
    const el = document.getElementById('play-state');
    el.textContent = state;

    const playBtn = document.getElementById('btn-play');
    const stopBtn = document.getElementById('btn-stop');

    if (state === 'PLAYING') {
        playBtn.disabled = true;
        stopBtn.disabled = false;
    } else {
        playBtn.disabled = false;
        stopBtn.disabled = true;
    }
}

function sendPositionThrottled(alpha, beta, gamma) {
    if (positionThrottle) {
        clearTimeout(positionThrottle);
    }

    positionThrottle = setTimeout(() => {
        const payload = { interval: 0.1 };
        if (alpha !== null) payload.alpha = alpha;
        if (beta !== null) payload.beta = beta;
        if (gamma !== null) payload.gamma = gamma;

        restimWS.send('set_position', payload);
        positionThrottle = null;
    }, POSITION_THROTTLE_MS);
}
