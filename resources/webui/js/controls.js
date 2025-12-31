/**
 * UI Control components for Restim Web UI
 */

class PositionCanvas {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.alpha = 0;
        this.beta = 0;
        this.isDragging = false;
        this.invertHorizontal = false;
        this.invertVertical = false;

        this.setupEvents();
        this.draw();
    }

    setInvert(horizontal, vertical) {
        this.invertHorizontal = horizontal;
        this.invertVertical = vertical;
        this.draw();
    }

    setupEvents() {
        const getPosition = (e) => {
            const rect = this.canvas.getBoundingClientRect();
            let x, y;

            if (e.touches) {
                x = e.touches[0].clientX - rect.left;
                y = e.touches[0].clientY - rect.top;
            } else {
                x = e.clientX - rect.left;
                y = e.clientY - rect.top;
            }

            // Convert to -1 to 1 range
            let alpha = (x / rect.width) * 2 - 1;
            let beta = -((y / rect.height) * 2 - 1); // Invert Y for screen coords

            // Apply user invert settings
            if (this.invertHorizontal) alpha = -alpha;
            if (this.invertVertical) beta = -beta;

            return {
                alpha: Math.max(-1, Math.min(1, alpha)),
                beta: Math.max(-1, Math.min(1, beta))
            };
        };

        const handleMove = (e) => {
            if (!this.isDragging) return;
            e.preventDefault();

            const pos = getPosition(e);
            this.setPosition(pos.alpha, pos.beta);

            // Send to server with throttling
            if (this.onPositionChange) {
                this.onPositionChange(pos.alpha, pos.beta);
            }
        };

        // Mouse events
        this.canvas.addEventListener('mousedown', (e) => {
            this.isDragging = true;
            handleMove(e);
        });

        document.addEventListener('mousemove', handleMove);
        document.addEventListener('mouseup', () => {
            this.isDragging = false;
        });

        // Touch events
        this.canvas.addEventListener('touchstart', (e) => {
            this.isDragging = true;
            handleMove(e);
        });

        this.canvas.addEventListener('touchmove', handleMove);
        this.canvas.addEventListener('touchend', () => {
            this.isDragging = false;
        });
    }

    setPosition(alpha, beta) {
        this.alpha = alpha;
        this.beta = beta;
        this.draw();
    }

    draw() {
        const ctx = this.ctx;
        const w = this.canvas.width;
        const h = this.canvas.height;
        const cx = w / 2;
        const cy = h / 2;

        // Clear
        ctx.fillStyle = '#1f3460';
        ctx.fillRect(0, 0, w, h);

        // Grid
        ctx.strokeStyle = '#333';
        ctx.lineWidth = 1;

        // Vertical center line
        ctx.beginPath();
        ctx.moveTo(cx, 0);
        ctx.lineTo(cx, h);
        ctx.stroke();

        // Horizontal center line
        ctx.beginPath();
        ctx.moveTo(0, cy);
        ctx.lineTo(w, cy);
        ctx.stroke();

        // Circle boundary
        ctx.beginPath();
        ctx.arc(cx, cy, Math.min(cx, cy) - 5, 0, Math.PI * 2);
        ctx.stroke();

        // Position dot - apply invert settings for display
        let displayAlpha = this.invertHorizontal ? -this.alpha : this.alpha;
        let displayBeta = this.invertVertical ? -this.beta : this.beta;
        const x = cx + (displayAlpha * (cx - 10));
        const y = cy - (displayBeta * (cy - 10)); // Invert Y for screen coords

        ctx.fillStyle = '#4a90d9';
        ctx.beginPath();
        ctx.arc(x, y, 8, 0, Math.PI * 2);
        ctx.fill();

        // Outer glow
        ctx.strokeStyle = '#6ab0ff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 10, 0, Math.PI * 2);
        ctx.stroke();
    }
}

class SliderControl {
    constructor(sliderId, valueId, options = {}) {
        this.slider = document.getElementById(sliderId);
        this.valueDisplay = document.getElementById(valueId);
        this.options = {
            format: (v) => v.toString(),
            scale: 1,
            throttle: 50,
            ...options
        };

        this.lastSend = 0;
        this.pendingValue = null;
        this.onChange = null;

        this.setupEvents();
    }

    setupEvents() {
        this.slider.addEventListener('input', () => {
            const value = parseFloat(this.slider.value) * this.options.scale;
            this.updateDisplay(value);

            // Throttle sending
            const now = Date.now();
            if (now - this.lastSend >= this.options.throttle) {
                this.sendValue(value);
            } else {
                this.pendingValue = value;
                setTimeout(() => {
                    if (this.pendingValue !== null) {
                        this.sendValue(this.pendingValue);
                        this.pendingValue = null;
                    }
                }, this.options.throttle);
            }
        });
    }

    sendValue(value) {
        this.lastSend = Date.now();
        if (this.onChange) {
            this.onChange(value);
        }
    }

    updateDisplay(value) {
        if (this.valueDisplay) {
            this.valueDisplay.textContent = this.options.format(value);
        }
    }

    setValue(value, updateSlider = true) {
        const scaledValue = value / this.options.scale;
        if (updateSlider) {
            this.slider.value = scaledValue;
        }
        this.updateDisplay(value);
    }

    getValue() {
        return parseFloat(this.slider.value) * this.options.scale;
    }
}

class CheckboxControl {
    constructor(checkboxId) {
        this.checkbox = document.getElementById(checkboxId);
        this.onChange = null;

        this.checkbox.addEventListener('change', () => {
            if (this.onChange) {
                this.onChange(this.checkbox.checked);
            }
        });
    }

    setChecked(checked) {
        this.checkbox.checked = checked;
    }

    isChecked() {
        return this.checkbox.checked;
    }
}

class SelectControl {
    constructor(selectId) {
        this.select = document.getElementById(selectId);
        this.onChange = null;

        this.select.addEventListener('change', () => {
            if (this.onChange) {
                this.onChange(this.select.value);
            }
        });
    }

    setOptions(options) {
        this.select.innerHTML = '';
        options.forEach(opt => {
            const option = document.createElement('option');
            option.value = opt;
            option.textContent = opt;
            this.select.appendChild(option);
        });
    }

    setValue(value) {
        this.select.value = value;
    }

    getValue() {
        return this.select.value;
    }
}

// Collapsible sections
function setupCollapsibles() {
    document.querySelectorAll('.collapsible').forEach(section => {
        const header = section.querySelector('.collapsible-header');
        if (header) {
            header.addEventListener('click', () => {
                section.classList.toggle('open');
            });
        }
    });
}
