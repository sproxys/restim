/**
 * Gamepad support for Restim Web UI
 * Uses the browser Gamepad API
 */

class GamepadHandler {
    constructor() {
        this.enabled = false;
        this.gamepad = null;
        this.animationFrameId = null;
        this.lastPosition = { alpha: 0, beta: 0 };
        this.lastButtonStates = {};
        this.buttonRepeatTimers = {};

        // Settings with defaults
        this.settings = {
            deadZone: 0.15,
            invertHorizontal: false,
            invertVertical: false,
            carrierStep: 10,
            volumeStep: 1,
            pulseFreqStep: 1,
            pulseWidthStep: 0.5,
            repeatRate: 100
        };

        // Button mappings (standard gamepad layout)
        this.buttonMap = {
            0: 'a',        // A / Cross
            1: 'b',        // B / Circle
            2: 'x',        // X / Square
            3: 'y',        // Y / Triangle
            4: 'lb',       // Left Bumper
            5: 'rb',       // Right Bumper
            6: 'lt',       // Left Trigger
            7: 'rt',       // Right Trigger
            8: 'back',     // Back / Select
            9: 'start',    // Start
            10: 'ls',      // Left Stick Click
            11: 'rs',      // Right Stick Click
            12: 'dpad_up',
            13: 'dpad_down',
            14: 'dpad_left',
            15: 'dpad_right'
        };

        // Action mappings (button name -> action)
        this.actionMap = {
            'rb': 'carrier_up',
            'lb': 'carrier_down',
            'dpad_up': 'volume_up',
            'dpad_down': 'volume_down',
            'dpad_right': 'pulse_freq_up',
            'dpad_left': 'pulse_freq_down',
            'rt': 'pulse_width_up',
            'lt': 'pulse_width_down'
        };

        // Callbacks
        this.onPositionChange = null;
        this.onCarrierChange = null;
        this.onVolumeChange = null;
        this.onPulseFreqChange = null;
        this.onPulseWidthChange = null;

        // Setup gamepad connection events
        window.addEventListener('gamepadconnected', (e) => this.onGamepadConnected(e));
        window.addEventListener('gamepaddisconnected', (e) => this.onGamepadDisconnected(e));

        // Load settings
        this.loadSettings();
    }

    loadSettings() {
        const saved = localStorage.getItem('restim-gamepad-settings');
        if (saved) {
            try {
                const parsed = JSON.parse(saved);
                this.settings = { ...this.settings, ...parsed };
            } catch (e) {
                console.error('Failed to load gamepad settings:', e);
            }
        }
        this.enabled = localStorage.getItem('restim-gamepad-enabled') === 'true';
    }

    saveSettings() {
        localStorage.setItem('restim-gamepad-settings', JSON.stringify(this.settings));
        localStorage.setItem('restim-gamepad-enabled', this.enabled.toString());
    }

    setEnabled(enabled) {
        this.enabled = enabled;
        this.saveSettings();
        if (enabled) {
            this.startPolling();
        } else {
            this.stopPolling();
        }
    }

    onGamepadConnected(e) {
        console.log('Gamepad connected:', e.gamepad.id);
        this.gamepad = e.gamepad;
        if (this.enabled) {
            this.startPolling();
        }
        if (this.onConnectionChange) {
            this.onConnectionChange(true, e.gamepad.id);
        }
    }

    onGamepadDisconnected(e) {
        console.log('Gamepad disconnected:', e.gamepad.id);
        this.gamepad = null;
        this.stopPolling();
        if (this.onConnectionChange) {
            this.onConnectionChange(false, null);
        }
    }

    startPolling() {
        if (this.animationFrameId) return;
        this.poll();
    }

    stopPolling() {
        if (this.animationFrameId) {
            cancelAnimationFrame(this.animationFrameId);
            this.animationFrameId = null;
        }
        // Clear all repeat timers
        Object.values(this.buttonRepeatTimers).forEach(timer => clearInterval(timer));
        this.buttonRepeatTimers = {};
    }

    poll() {
        if (!this.enabled) {
            this.animationFrameId = null;
            return;
        }

        // Get fresh gamepad state
        const gamepads = navigator.getGamepads();
        let gp = null;
        for (const pad of gamepads) {
            if (pad) {
                gp = pad;
                break;
            }
        }

        if (gp) {
            this.processGamepad(gp);
        }

        this.animationFrameId = requestAnimationFrame(() => this.poll());
    }

    processGamepad(gp) {
        // Process analog sticks for position
        this.processPosition(gp);

        // Process buttons
        this.processButtons(gp);
    }

    processPosition(gp) {
        // Left stick: axes[0] = X, axes[1] = Y
        let x = gp.axes[0] || 0;
        let y = gp.axes[1] || 0;

        // Apply dead zone
        const magnitude = Math.sqrt(x * x + y * y);
        if (magnitude < this.settings.deadZone) {
            x = 0;
            y = 0;
        } else {
            // Rescale to 0-1 range outside dead zone
            const scale = (magnitude - this.settings.deadZone) / (1 - this.settings.deadZone);
            x = (x / magnitude) * scale;
            y = (y / magnitude) * scale;
        }

        // Apply inversions
        if (this.settings.invertHorizontal) x = -x;
        if (this.settings.invertVertical) y = -y;

        // Convert to alpha/beta (x = horizontal = alpha, y = vertical = beta, inverted for screen coords)
        const alpha = x;
        const beta = -y;

        // Only send if changed significantly
        const threshold = 0.01;
        if (Math.abs(alpha - this.lastPosition.alpha) > threshold ||
            Math.abs(beta - this.lastPosition.beta) > threshold) {
            this.lastPosition = { alpha, beta };
            if (this.onPositionChange) {
                this.onPositionChange(alpha, beta);
            }
        }
    }

    processButtons(gp) {
        gp.buttons.forEach((button, index) => {
            const buttonName = this.buttonMap[index];
            if (!buttonName) return;

            const isPressed = button.pressed || button.value > 0.5;
            const wasPressed = this.lastButtonStates[buttonName];

            if (isPressed && !wasPressed) {
                // Button just pressed
                this.onButtonDown(buttonName);
            } else if (!isPressed && wasPressed) {
                // Button just released
                this.onButtonUp(buttonName);
            }

            this.lastButtonStates[buttonName] = isPressed;
        });
    }

    onButtonDown(buttonName) {
        const action = this.actionMap[buttonName];
        if (action) {
            this.executeAction(action);
            // Start repeat timer
            this.buttonRepeatTimers[buttonName] = setInterval(() => {
                this.executeAction(action);
            }, this.settings.repeatRate);
        }
    }

    onButtonUp(buttonName) {
        // Stop repeat timer
        if (this.buttonRepeatTimers[buttonName]) {
            clearInterval(this.buttonRepeatTimers[buttonName]);
            delete this.buttonRepeatTimers[buttonName];
        }
    }

    executeAction(action) {
        switch (action) {
            case 'carrier_up':
                if (this.onCarrierChange) this.onCarrierChange(this.settings.carrierStep);
                break;
            case 'carrier_down':
                if (this.onCarrierChange) this.onCarrierChange(-this.settings.carrierStep);
                break;
            case 'volume_up':
                if (this.onVolumeChange) this.onVolumeChange(this.settings.volumeStep);
                break;
            case 'volume_down':
                if (this.onVolumeChange) this.onVolumeChange(-this.settings.volumeStep);
                break;
            case 'pulse_freq_up':
                if (this.onPulseFreqChange) this.onPulseFreqChange(this.settings.pulseFreqStep);
                break;
            case 'pulse_freq_down':
                if (this.onPulseFreqChange) this.onPulseFreqChange(-this.settings.pulseFreqStep);
                break;
            case 'pulse_width_up':
                if (this.onPulseWidthChange) this.onPulseWidthChange(this.settings.pulseWidthStep);
                break;
            case 'pulse_width_down':
                if (this.onPulseWidthChange) this.onPulseWidthChange(-this.settings.pulseWidthStep);
                break;
        }
    }

    getConnectedGamepad() {
        const gamepads = navigator.getGamepads();
        for (const pad of gamepads) {
            if (pad) return pad;
        }
        return null;
    }

    isConnected() {
        return this.getConnectedGamepad() !== null;
    }
}

// Global instance
const gamepadHandler = new GamepadHandler();
