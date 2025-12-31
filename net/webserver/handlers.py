"""
WebSocket message handlers for the Restim Web UI.

Translates WebSocket commands to MainWindow actions and collects state for responses.
"""

import logging
import time
from typing import Optional, TYPE_CHECKING

from .protocol import Message, MessageType, InvalidMessageException

if TYPE_CHECKING:
    from qt_ui.mainwindow import Window

logger = logging.getLogger('restim.webserver.handlers')


class WebSocketHandler:
    """
    Handles WebSocket messages and translates them to MainWindow actions.
    """

    def __init__(self, main_window: 'Window'):
        self.main_window = main_window

    def handle_message(self, message: Message) -> Optional[Message]:
        """
        Process an incoming message and return an optional response.

        Args:
            message: The parsed WebSocket message

        Returns:
            Optional response message, or None if no response needed
        """
        handlers = {
            MessageType.GET_STATE: self._handle_get_state,
            MessageType.SET_POSITION: self._handle_set_position,
            MessageType.SET_VOLUME: self._handle_set_volume,
            MessageType.SET_CARRIER: self._handle_set_carrier,
            MessageType.SET_PULSE_PARAMS: self._handle_set_pulse_params,
            MessageType.SET_VIBRATION: self._handle_set_vibration,
            MessageType.SET_PATTERN: self._handle_set_pattern,
            MessageType.SET_CALIBRATION: self._handle_set_calibration,
            MessageType.PLAY: self._handle_play,
            MessageType.STOP: self._handle_stop,
        }

        handler = handlers.get(message.type)
        if handler:
            try:
                return handler(message.payload)
            except Exception as e:
                logger.exception(f"Error handling message {message.type}: {e}")
                return Message(MessageType.ERROR, {"error": str(e)})

        return Message(MessageType.ERROR, {"error": f"Unknown message type: {message.type}"})

    def _handle_get_state(self, payload: dict) -> Message:
        """Return complete current state."""
        return Message(MessageType.STATE_UPDATE, self.get_full_state())

    def get_full_state(self) -> dict:
        """Collect the full application state."""
        from qt_ui.mainwindow import PlayState
        from qt_ui.device_wizard.enums import DeviceConfiguration

        mw = self.main_window
        config = DeviceConfiguration.from_settings()

        return {
            "playState": mw.playstate.name,
            "position": {
                "alpha": mw.alpha.last_value(),
                "beta": mw.beta.last_value(),
                "gamma": mw.gamma.last_value(),
            },
            "volume": {
                "master": mw.doubleSpinBox_volume.value(),
                "effective": mw.tab_volume.axis_master_volume.last_value() * 100,
            },
            "carrier": self._get_carrier_value(),
            "pulse": self._get_pulse_params(),
            "vibration": self._get_vibration_params(),
            "pattern": {
                "name": mw.comboBox_patternSelect.currentText(),
                "velocity": mw.doubleSpinBox.value(),
                "available": self._get_available_patterns(),
            },
            "calibration": self._get_calibration_params(),
            "device": {
                "type": config.device_type.name,
                "waveformType": config.waveform_type.name,
            },
        }

    def _get_carrier_value(self) -> float:
        """Get current carrier frequency."""
        return self.main_window.tab_carrier.carrier.value()

    def _get_pulse_params(self) -> dict:
        """Get current pulse parameters."""
        ps = self.main_window.tab_pulse_settings
        return {
            "carrier": ps.carrier.value(),
            "frequency": ps.pulse_freq_slider.value(),
            "width": ps.pulse_width_slider.value(),
            "riseTime": ps.pulse_rise_time.value(),
            "intervalRandom": ps.pulse_interval_random.value(),
        }

    def _get_vibration_params(self) -> dict:
        """Get current vibration parameters."""
        vib = self.main_window.tab_vibrate
        return {
            "vibration1": {
                "enabled": vib.vib1_gb.isChecked(),
                "frequency": vib.vibration_1.frequency.last_value(),
                "strength": vib.vibration_1.strength.last_value() * 100,
                "leftRightBias": vib.vibration_1.left_right_bias.last_value() * 100,
                "highLowBias": vib.vibration_1.high_low_bias.last_value() * 100,
                "random": vib.vibration_1.random.last_value() * 100,
            },
            "vibration2": {
                "enabled": vib.vib2_gb.isChecked(),
                "frequency": vib.vibration_2.frequency.last_value(),
                "strength": vib.vibration_2.strength.last_value() * 100,
                "leftRightBias": vib.vibration_2.left_right_bias.last_value() * 100,
                "highLowBias": vib.vibration_2.high_low_bias.last_value() * 100,
                "random": vib.vibration_2.random.last_value() * 100,
            },
        }

    def _get_available_patterns(self) -> list:
        """Get list of available pattern names."""
        patterns = []
        cb = self.main_window.comboBox_patternSelect
        for i in range(cb.count()):
            patterns.append(cb.itemText(i))
        return patterns

    def _get_calibration_params(self) -> dict:
        """Get current calibration parameters."""
        tp = self.main_window.tab_threephase
        fp = self.main_window.tab_fourphase
        return {
            "threephase": {
                "neutral": tp.calibrate_params.neutral.last_value(),
                "right": tp.calibrate_params.right.last_value(),
                "center": tp.calibrate_params.center.last_value(),
            },
            "fourphase": {
                "a": fp.a_power.value(),
                "b": fp.b_power.value(),
                "c": fp.c_power.value(),
                "d": fp.d_power.value(),
                "center": fp.center_power.value(),
            },
            "transform": {
                "enabled": tp.transform_params.transform_enabled.last_value(),
                "rotation": tp.transform_params.transform_rotation_degrees.last_value(),
                "mirror": tp.transform_params.transform_mirror.last_value(),
            },
        }

    def _handle_set_position(self, payload: dict) -> Optional[Message]:
        """Set position (alpha, beta, gamma)."""
        interval = payload.get("interval", 0.1)
        mw = self.main_window

        if "alpha" in payload:
            value = max(-1.0, min(1.0, float(payload["alpha"])))
            mw.alpha.add(value, interval)

        if "beta" in payload:
            value = max(-1.0, min(1.0, float(payload["beta"])))
            mw.beta.add(value, interval)

        if "gamma" in payload:
            value = max(-1.0, min(1.0, float(payload["gamma"])))
            mw.gamma.add(value, interval)

        return None

    def _handle_set_volume(self, payload: dict) -> Optional[Message]:
        """Set master volume."""
        if "value" in payload:
            value = max(0.0, min(100.0, float(payload["value"])))
            self.main_window.doubleSpinBox_volume.setValue(value)
        return None

    def _handle_set_carrier(self, payload: dict) -> Optional[Message]:
        """Set carrier frequency."""
        if "frequency" in payload:
            freq = float(payload["frequency"])
            self.main_window.tab_carrier.carrier.setValue(freq)
            self.main_window.tab_pulse_settings.carrier.setValue(freq)
        return None

    def _handle_set_pulse_params(self, payload: dict) -> Optional[Message]:
        """Set pulse parameters."""
        ps = self.main_window.tab_pulse_settings

        if "carrier" in payload:
            ps.carrier.setValue(float(payload["carrier"]))

        if "frequency" in payload:
            ps.pulse_freq_slider.setValue(float(payload["frequency"]))

        if "width" in payload:
            ps.pulse_width_slider.setValue(float(payload["width"]))

        if "riseTime" in payload:
            ps.pulse_rise_time.setValue(float(payload["riseTime"]))

        if "intervalRandom" in payload:
            ps.pulse_interval_random.setValue(float(payload["intervalRandom"]))

        return None

    def _handle_set_vibration(self, payload: dict) -> Optional[Message]:
        """Set vibration parameters."""
        channel = payload.get("channel", 1)
        vib = self.main_window.tab_vibrate

        if channel == 1:
            gb = vib.vib1_gb
            freq_slider = vib.vib1_freq_slider
            strength_slider = vib.vib1_strength_slider
            lr_slider = vib.vib1_left_right_slider
            hl_slider = vib.vib1_high_low_slider
            random_slider = vib.vig1_random_slider  # Note: typo in original widget
        elif channel == 2:
            gb = vib.vib2_gb
            freq_slider = vib.vib2_freq_slider
            strength_slider = vib.vib2_strength_slider
            lr_slider = vib.vib2_left_right_slider
            hl_slider = vib.vib2_high_low_slider
            random_slider = vib.vib2_random_slider
        else:
            return Message(MessageType.ERROR, {"error": f"Invalid vibration channel: {channel}"})

        if "enabled" in payload:
            gb.setChecked(bool(payload["enabled"]))

        if "frequency" in payload:
            freq_slider.setValue(float(payload["frequency"]))

        if "strength" in payload:
            strength_slider.setValue(float(payload["strength"]))

        if "leftRightBias" in payload:
            lr_slider.setValue(float(payload["leftRightBias"]))

        if "highLowBias" in payload:
            hl_slider.setValue(float(payload["highLowBias"]))

        if "random" in payload:
            random_slider.setValue(float(payload["random"]))

        return None

    def _handle_set_pattern(self, payload: dict) -> Optional[Message]:
        """Set pattern and/or velocity."""
        mw = self.main_window

        if "name" in payload:
            name = payload["name"]
            index = mw.comboBox_patternSelect.findText(name)
            if index >= 0:
                mw.comboBox_patternSelect.setCurrentIndex(index)
            else:
                return Message(MessageType.ERROR, {"error": f"Unknown pattern: {name}"})

        if "velocity" in payload:
            velocity = max(0.1, float(payload["velocity"]))
            mw.doubleSpinBox.setValue(velocity)

        return None

    def _handle_set_calibration(self, payload: dict) -> Optional[Message]:
        """Set calibration parameters."""
        tp = self.main_window.tab_threephase
        fp = self.main_window.tab_fourphase

        if "threephase" in payload:
            tp_data = payload["threephase"]
            if "neutral" in tp_data:
                tp.neutral.setValue(float(tp_data["neutral"]))
            if "right" in tp_data:
                tp.right.setValue(float(tp_data["right"]))
            if "center" in tp_data:
                tp.center.setValue(float(tp_data["center"]))

        if "fourphase" in payload:
            fp_data = payload["fourphase"]
            if "a" in fp_data:
                fp.a_power.setValue(float(fp_data["a"]))
            if "b" in fp_data:
                fp.b_power.setValue(float(fp_data["b"]))
            if "c" in fp_data:
                fp.c_power.setValue(float(fp_data["c"]))
            if "d" in fp_data:
                fp.d_power.setValue(float(fp_data["d"]))
            if "center" in fp_data:
                fp.center_power.setValue(float(fp_data["center"]))

        return None

    def _handle_play(self, payload: dict) -> Optional[Message]:
        """Start signal output."""
        from qt_ui.mainwindow import PlayState

        if self.main_window.playstate == PlayState.STOPPED:
            self.main_window.signal_start()
        return None

    def _handle_stop(self, payload: dict) -> Optional[Message]:
        """Stop signal output."""
        from qt_ui.mainwindow import PlayState

        if self.main_window.playstate != PlayState.STOPPED:
            self.main_window.signal_stop(PlayState.STOPPED)
        return None

    def get_position_update(self) -> dict:
        """Get current position for broadcast."""
        mw = self.main_window
        return {
            "alpha": mw.alpha.last_value(),
            "beta": mw.beta.last_value(),
            "gamma": mw.gamma.last_value(),
        }

    def get_play_state(self) -> str:
        """Get current play state name."""
        return self.main_window.playstate.name
