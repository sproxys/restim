"""
Gamepad input handler for 3-phase pulse control.
Uses the 'inputs' library for cross-platform gamepad support.
"""
import logging
import threading

from PySide6 import QtCore
from PySide6.QtCore import QThread, Signal, QTimer

logger = logging.getLogger('restim.gamepad')

# Try to import inputs library
try:
    import inputs
    GAMEPAD_AVAILABLE = True
except ImportError:
    GAMEPAD_AVAILABLE = False
    logger.warning("'inputs' library not installed. Gamepad support unavailable.")


# Mapping from inputs library event codes to our button identifiers
EVENT_TO_BUTTON = {
    # Key events (buttons)
    ('Key', 'BTN_TL'): 'lb',
    ('Key', 'BTN_TR'): 'rb',
    ('Key', 'BTN_SOUTH'): 'a',
    ('Key', 'BTN_EAST'): 'b',
    ('Key', 'BTN_NORTH'): 'y',  # Note: Some controllers swap X/Y
    ('Key', 'BTN_WEST'): 'x',
    ('Key', 'BTN_START'): 'start',
    ('Key', 'BTN_SELECT'): 'select',
    ('Key', 'BTN_THUMBL'): 'l3',
    ('Key', 'BTN_THUMBR'): 'r3',
    # Alternative button names used by some controllers
    ('Key', 'BTN_A'): 'a',
    ('Key', 'BTN_B'): 'b',
    ('Key', 'BTN_X'): 'x',
    ('Key', 'BTN_Y'): 'y',
}

# D-pad handled separately since it's an absolute axis with positive/negative values


class GamepadReaderThread(QThread):
    """
    Background thread that reads gamepad events using the blocking inputs library.
    Emits position_changed signal when joystick position changes.
    """
    position_changed = Signal(float, float)
    connection_changed = Signal(bool)
    # Signals for button/trigger controls (emitted on initial press)
    carrier_frequency_change = Signal(int)  # +1 for increase, -1 for decrease
    volume_change = Signal(int)  # +1 for up, -1 for down
    pulse_frequency_change = Signal(int)  # +1 for increase, -1 for decrease
    pulse_width_change = Signal(int)  # +1 for increase, -1 for decrease
    shock_triggered = Signal()  # emitted when shock button is pressed
    shock_released = Signal()  # emitted when shock button is released
    mute_triggered = Signal()  # emitted when mute button is pressed
    # Signal to notify button state changes for repeat handling
    button_state_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._alpha = 0.0
        self._beta = 0.0
        self._dead_zone = 0.15
        self._connected = False
        self._invert_vertical = False
        self._invert_horizontal = False
        # Trigger threshold for treating as button press
        self._trigger_threshold = 0.5

        # Button mappings: action -> button_id
        self._button_mappings = {
            'carrier_up': 'rb',
            'carrier_down': 'lb',
            'volume_up': 'dpad_up',
            'volume_down': 'dpad_down',
            'pulse_freq_up': 'dpad_right',
            'pulse_freq_down': 'dpad_left',
            'pulse_width_up': 'rt',
            'pulse_width_down': 'lt',
            'shock': 'a',
            'mute': 'b',
        }

        # Build reverse mapping: button_id -> list of actions
        self._button_to_actions = {}
        self._rebuild_button_to_actions()

        # Button hold states (thread-safe with lock)
        # Track ALL possible buttons, not just mapped ones
        self._state_lock = threading.Lock()
        self._held_buttons = {
            'lb': False,
            'rb': False,
            'lt': False,
            'rt': False,
            'a': False,
            'b': False,
            'x': False,
            'y': False,
            'dpad_up': False,
            'dpad_down': False,
            'dpad_left': False,
            'dpad_right': False,
            'start': False,
            'select': False,
            'l3': False,
            'r3': False,
        }

    def _rebuild_button_to_actions(self):
        """Rebuild the reverse mapping from buttons to actions"""
        self._button_to_actions = {}
        for action, button_id in self._button_mappings.items():
            if button_id and button_id != 'none':
                if button_id not in self._button_to_actions:
                    self._button_to_actions[button_id] = []
                self._button_to_actions[button_id].append(action)

    def set_button_mappings(self, mappings: dict):
        """Set button mappings from settings"""
        self._button_mappings.update(mappings)
        self._rebuild_button_to_actions()

    def get_held_actions(self):
        """Get a dict of actions and whether their mapped button is held (thread-safe)"""
        with self._state_lock:
            result = {}
            for action, button_id in self._button_mappings.items():
                if button_id and button_id != 'none':
                    result[action] = self._held_buttons.get(button_id, False)
                else:
                    result[action] = False
            return result

    def set_dead_zone(self, dead_zone: float):
        """Set the joystick dead zone (0.0 to 1.0)"""
        self._dead_zone = max(0.0, min(1.0, dead_zone))

    def set_invert_vertical(self, invert: bool):
        """Set vertical inversion for joystick"""
        self._invert_vertical = invert

    def set_invert_horizontal(self, invert: bool):
        """Set horizontal inversion for joystick"""
        self._invert_horizontal = invert

    def run(self):
        """Main thread loop - reads gamepad events"""
        if not GAMEPAD_AVAILABLE:
            return

        self._running = True

        while self._running:
            try:
                gamepads = inputs.devices.gamepads
                if not gamepads:
                    if self._connected:
                        self._connected = False
                        self.connection_changed.emit(False)
                    self.msleep(500)
                    continue

                if not self._connected:
                    self._connected = True
                    self.connection_changed.emit(True)
                    logger.info(f"Gamepad connected: {gamepads[0]}")

                events = inputs.get_gamepad()

                for event in events:
                    if not self._running:
                        break
                    self._process_event(event)

            except inputs.UnpluggedError:
                if self._connected:
                    self._connected = False
                    self.connection_changed.emit(False)
                    logger.info("Gamepad disconnected")
                # Clear held buttons on disconnect
                with self._state_lock:
                    for key in self._held_buttons:
                        self._held_buttons[key] = False
                self.button_state_changed.emit()
                self.msleep(500)
            except Exception as e:
                logger.debug(f"Gamepad read error: {e}")
                self.msleep(100)

    def _process_event(self, event):
        """Process a single gamepad event"""
        if event.ev_type == 'Absolute':
            if event.code in ('ABS_X', 'ABS_RX'):
                raw_value = event.state / 32768.0
                if self._invert_horizontal:
                    raw_value = -raw_value
                self._beta = self._apply_dead_zone(raw_value)
                self._emit_position()
            elif event.code in ('ABS_Y', 'ABS_RY'):
                raw_value = -event.state / 32768.0
                if self._invert_vertical:
                    raw_value = -raw_value
                self._alpha = self._apply_dead_zone(raw_value)
                self._emit_position()
            # D-pad (HAT)
            elif event.code == 'ABS_HAT0Y':
                # -1 = up, 1 = down, 0 = released
                self._handle_button_event('dpad_up', event.state == -1)
                self._handle_button_event('dpad_down', event.state == 1)
            elif event.code == 'ABS_HAT0X':
                # -1 = left, 1 = right, 0 = released
                self._handle_button_event('dpad_left', event.state == -1)
                self._handle_button_event('dpad_right', event.state == 1)
            # Triggers as buttons (LT/RT)
            elif event.code == 'ABS_Z':  # Left trigger (LT)
                pressed = (event.state / 255.0) > self._trigger_threshold
                self._handle_button_event('lt', pressed)
            elif event.code == 'ABS_RZ':  # Right trigger (RT)
                pressed = (event.state / 255.0) > self._trigger_threshold
                self._handle_button_event('rt', pressed)
        elif event.ev_type == 'Key':
            # Map event to button ID
            button_id = EVENT_TO_BUTTON.get((event.ev_type, event.code))
            if button_id:
                self._handle_button_event(button_id, event.state == 1)

    def _handle_button_event(self, button_id: str, pressed: bool):
        """Handle a button press/release event"""
        with self._state_lock:
            was_pressed = self._held_buttons.get(button_id, False)
            self._held_buttons[button_id] = pressed

        # If button just pressed, emit signals for mapped actions
        if pressed and not was_pressed:
            actions = self._button_to_actions.get(button_id, [])
            for action in actions:
                self._emit_action(action)
        # If button just released, check for shock release
        elif not pressed and was_pressed:
            actions = self._button_to_actions.get(button_id, [])
            if 'shock' in actions:
                self.shock_released.emit()

        self.button_state_changed.emit()

    def _emit_action(self, action: str):
        """Emit the signal for a specific action"""
        if action == 'carrier_up':
            self.carrier_frequency_change.emit(1)
        elif action == 'carrier_down':
            self.carrier_frequency_change.emit(-1)
        elif action == 'volume_up':
            self.volume_change.emit(1)
        elif action == 'volume_down':
            self.volume_change.emit(-1)
        elif action == 'pulse_freq_up':
            self.pulse_frequency_change.emit(1)
        elif action == 'pulse_freq_down':
            self.pulse_frequency_change.emit(-1)
        elif action == 'pulse_width_up':
            self.pulse_width_change.emit(1)
        elif action == 'pulse_width_down':
            self.pulse_width_change.emit(-1)
        elif action == 'shock':
            self.shock_triggered.emit()
        elif action == 'mute':
            self.mute_triggered.emit()

    def _apply_dead_zone(self, value: float) -> float:
        """Apply dead zone to joystick value"""
        if abs(value) < self._dead_zone:
            return 0.0
        sign = 1 if value > 0 else -1
        return sign * (abs(value) - self._dead_zone) / (1.0 - self._dead_zone)

    def _emit_position(self):
        """Emit current position"""
        self.position_changed.emit(self._alpha, self._beta)

    def stop(self):
        """Stop the thread"""
        self._running = False


class GamepadHandler(QtCore.QObject):
    """
    Qt-based gamepad handler that manages the reader thread
    and provides signals for integration with the main application.
    """
    position_changed = Signal(float, float)
    connection_changed = Signal(bool)
    # Signals for button/trigger controls
    carrier_frequency_change = Signal(int)
    volume_change = Signal(int)
    pulse_frequency_change = Signal(int)
    pulse_width_change = Signal(int)
    shock_triggered = Signal()
    shock_released = Signal()
    mute_triggered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reader_thread = None
        self._enabled = False
        self._dead_zone = 0.15
        self._invert_vertical = False
        self._invert_horizontal = False
        self._repeat_rate = 100  # ms
        self._button_mappings = {}

        # Timer for button repeat
        self._repeat_timer = QTimer(self)
        self._repeat_timer.timeout.connect(self._on_repeat_timer)

    def set_enabled(self, enabled: bool):
        """Enable or disable gamepad input"""
        if enabled == self._enabled:
            return

        self._enabled = enabled

        if enabled:
            self._start_reader()
        else:
            self._stop_reader()

    def set_dead_zone(self, dead_zone: float):
        """Set dead zone (0.0 to 1.0)"""
        self._dead_zone = dead_zone
        if self._reader_thread:
            self._reader_thread.set_dead_zone(dead_zone)

    def set_invert_vertical(self, invert: bool):
        """Set vertical inversion"""
        self._invert_vertical = invert
        if self._reader_thread:
            self._reader_thread.set_invert_vertical(invert)

    def set_invert_horizontal(self, invert: bool):
        """Set horizontal inversion"""
        self._invert_horizontal = invert
        if self._reader_thread:
            self._reader_thread.set_invert_horizontal(invert)

    def set_repeat_rate(self, rate_ms: int):
        """Set button repeat rate in milliseconds"""
        self._repeat_rate = max(20, rate_ms)
        if self._repeat_timer.isActive():
            self._repeat_timer.setInterval(self._repeat_rate)

    def set_button_mappings(self, mappings: dict):
        """Set button mappings"""
        self._button_mappings = mappings
        if self._reader_thread:
            self._reader_thread.set_button_mappings(mappings)

    def _start_reader(self):
        """Start the gamepad reader thread"""
        if not GAMEPAD_AVAILABLE:
            logger.warning("Cannot start gamepad: 'inputs' library not available")
            return

        if self._reader_thread is not None:
            return

        self._reader_thread = GamepadReaderThread(self)
        self._reader_thread.set_dead_zone(self._dead_zone)
        self._reader_thread.set_invert_vertical(self._invert_vertical)
        self._reader_thread.set_invert_horizontal(self._invert_horizontal)
        self._reader_thread.set_button_mappings(self._button_mappings)
        self._reader_thread.position_changed.connect(self.position_changed)
        self._reader_thread.connection_changed.connect(self.connection_changed)
        self._reader_thread.carrier_frequency_change.connect(self.carrier_frequency_change)
        self._reader_thread.volume_change.connect(self.volume_change)
        self._reader_thread.pulse_frequency_change.connect(self.pulse_frequency_change)
        self._reader_thread.pulse_width_change.connect(self.pulse_width_change)
        self._reader_thread.shock_triggered.connect(self.shock_triggered)
        self._reader_thread.shock_released.connect(self.shock_released)
        self._reader_thread.mute_triggered.connect(self.mute_triggered)
        self._reader_thread.button_state_changed.connect(self._on_button_state_changed)
        self._reader_thread.start()
        logger.info("Gamepad reader started")

    def _stop_reader(self):
        """Stop the gamepad reader thread"""
        self._repeat_timer.stop()

        if self._reader_thread is None:
            return

        self._reader_thread.stop()
        self._reader_thread.wait(2000)
        self._reader_thread = None
        logger.info("Gamepad reader stopped")

    def _on_button_state_changed(self):
        """Called when button states change - start/stop repeat timer"""
        if self._reader_thread is None:
            return

        held_actions = self._reader_thread.get_held_actions()
        any_held = any(held_actions.values())

        if any_held and not self._repeat_timer.isActive():
            self._repeat_timer.start(self._repeat_rate)
        elif not any_held and self._repeat_timer.isActive():
            self._repeat_timer.stop()

    def _on_repeat_timer(self):
        """Called periodically to emit signals for held buttons"""
        if self._reader_thread is None:
            self._repeat_timer.stop()
            return

        held_actions = self._reader_thread.get_held_actions()

        # Emit signals for held actions
        if held_actions.get('carrier_up'):
            self.carrier_frequency_change.emit(1)
        if held_actions.get('carrier_down'):
            self.carrier_frequency_change.emit(-1)
        if held_actions.get('volume_up'):
            self.volume_change.emit(1)
        if held_actions.get('volume_down'):
            self.volume_change.emit(-1)
        if held_actions.get('pulse_freq_up'):
            self.pulse_frequency_change.emit(1)
        if held_actions.get('pulse_freq_down'):
            self.pulse_frequency_change.emit(-1)
        if held_actions.get('pulse_width_up'):
            self.pulse_width_change.emit(1)
        if held_actions.get('pulse_width_down'):
            self.pulse_width_change.emit(-1)

    def is_available(self) -> bool:
        """Check if gamepad support is available"""
        return GAMEPAD_AVAILABLE

    def refreshSettings(self):
        """Reload settings"""
        from qt_ui import settings
        enabled = settings.gamepad_enabled.get()
        dead_zone = settings.gamepad_dead_zone.get()
        invert_vertical = settings.gamepad_invert_vertical.get()
        invert_horizontal = settings.gamepad_invert_horizontal.get()
        repeat_rate = settings.gamepad_repeat_rate.get()

        # Load button mappings
        button_mappings = {
            'carrier_up': settings.gamepad_btn_carrier_up.get(),
            'carrier_down': settings.gamepad_btn_carrier_down.get(),
            'volume_up': settings.gamepad_btn_volume_up.get(),
            'volume_down': settings.gamepad_btn_volume_down.get(),
            'pulse_freq_up': settings.gamepad_btn_pulse_freq_up.get(),
            'pulse_freq_down': settings.gamepad_btn_pulse_freq_down.get(),
            'pulse_width_up': settings.gamepad_btn_pulse_width_up.get(),
            'pulse_width_down': settings.gamepad_btn_pulse_width_down.get(),
            'shock': settings.gamepad_btn_shock.get(),
            'mute': settings.gamepad_btn_mute.get(),
        }

        self.set_dead_zone(dead_zone)
        self.set_invert_vertical(invert_vertical)
        self.set_invert_horizontal(invert_horizontal)
        self.set_repeat_rate(repeat_rate)
        self.set_button_mappings(button_mappings)
        self.set_enabled(enabled)
