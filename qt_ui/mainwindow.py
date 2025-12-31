import os
import sys
from enum import Enum

from PySide6 import QtGui
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSizePolicy, QFrame, QStyleFactory
)
import logging

from net.media_source.interface import MediaConnectionState
from qt_ui.algorithm_factory import AlgorithmFactory
from qt_ui.audio_write_dialog import AudioWriteDialog
from qt_ui.main_window_ui import Ui_MainWindow
import qt_ui.patterns.threephase_patterns
import qt_ui.patterns.fourphase_patterns
from device.audio.audio_stim_device import AudioStimDevice
import net.websocketserver
import net.tcpudpserver
import qt_ui.funscript_conversion_dialog
import qt_ui.simfile_conversion_dialog
import qt_ui.focstim_flash_dialog
import qt_ui.funscript_decomposition_dialog
import qt_ui.preferences_dialog
import qt_ui.about_dialog
import qt_ui.settings
import net.serialproxy
import net.buttplug_wsdm_client
import net.gamepad
import net.webserver
import net.remote_control
from qt_ui import resources
from qt_ui.models.funscript_kit import FunscriptKitModel
from device.focstim.proto_device import FOCStimProtoDevice
from device.neostim.neostim_device import NeoStim
from qt_ui.widgets.icon_with_connection_status import IconWithConnectionStatus
from stim_math.axis import create_temporal_axis


import sounddevice as sd

from qt_ui.device_wizard.wizard import DeviceSelectionWizard
from qt_ui.device_wizard.enums import DeviceConfiguration, DeviceType, WaveformType

from qt_ui.tcode_command_router import TCodeCommandRouter

logger = logging.getLogger('restim.main')


class PlayState(Enum):
    STOPPED = 0
    PLAYING = 1
    WAITING_ON_LOAD = 2  # the audio is stopped, but is ready to be auto-started once funscripts are loaded.


class Window(QMainWindow, Ui_MainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        self.playstate = PlayState.STOPPED
        self.tab_volume.set_play_state(self.playstate)
        self.refresh_play_button_icon()

        # set the first tab as active tab, in case we forgot to set it in designer
        self.tabWidget.setCurrentIndex(0)

        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap(resources.favicon), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.setWindowIcon(icon)

        # TODO: credit https://glyphs.fyi/ for icons
        spacer = QWidget()
        spacer.sizePolicy()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolBar.insertWidget(self.actionStart, spacer)
        line = QFrame()
        # line->setObjectName(QString::fromUtf8("line"));
        # line->setGeometry(QRect(320, 150, 118, 3));
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        self.toolBar.insertWidget(self.actionStart, line)

        self.doubleSpinBox_volume.setValue(qt_ui.settings.volume_default_level.get())
        self.tab_volume.link_volume_controls(self.doubleSpinBox_volume, self.progressBar_volume)

        # default alpha/beta axis. Used by:
        # pattern generator
        # network stuff (intiface, tcode)
        self.alpha = create_temporal_axis(0.0)
        self.beta = create_temporal_axis(0.0)
        self.gamma = create_temporal_axis(0.0)

        self.tcode_command_router = TCodeCommandRouter(
            self.alpha,
            self.beta,
            self.gamma,
            self.tab_volume.axis_api_volume,
            self.tab_volume.axis_external_volume,

            self.tab_carrier.axis_carrier,  # this gets set to the device-specific axis later

            self.tab_pulse_settings.axis_pulse_frequency,
            self.tab_pulse_settings.axis_pulse_width,
            self.tab_pulse_settings.axis_pulse_interval_random,
            self.tab_pulse_settings.axis_pulse_rise_time,

            self.tab_vibrate.vibration_1.frequency,
            self.tab_vibrate.vibration_1.strength,
            self.tab_vibrate.vibration_1.left_right_bias,
            self.tab_vibrate.vibration_1.high_low_bias,
            self.tab_vibrate.vibration_1.random,

            self.tab_vibrate.vibration_2.frequency,
            self.tab_vibrate.vibration_2.strength,
            self.tab_vibrate.vibration_2.left_right_bias,
            self.tab_vibrate.vibration_2.high_low_bias,
            self.tab_vibrate.vibration_2.random,

            # TODO: neostim
        )

        # threephase view
        self.motion_3 = qt_ui.patterns.threephase_patterns.ThreephaseMotionGenerator(self, self.alpha, self.beta)
        self.graphicsView_threephase.set_transform_params(self.tab_threephase.transform_params)
        self.graphicsView_threephase.mousePositionChanged.connect(self.motion_3.mouse_event)
        self.motion_3.position_updated.connect(self.graphicsView_threephase.set_cursor_position_ab)

        # fourphase view
        self.motion_4 = qt_ui.patterns.fourphase_patterns.FourphaseMotionGenerator(self, self.alpha, self.beta, self.gamma)
        self.graphicsView_fourphase.mousePositionChanged.connect(self.motion_4.mouse_event)
        self.motion_4.position_updated.connect(self.graphicsView_fourphase.set_cursor_position_abc)

        # TODO: implement details for 4-phase
        self.tab_details.set_axis(
            self.alpha,
            self.beta,
            self.tab_threephase.calibrate_params,
            self.tab_threephase.transform_params,
        )
        # self.tab_details.set_config_manager(self.threephase_parameters)

        self.comboBox_patternSelect.currentIndexChanged.connect(self.pattern_selection_changed)
        self.motion_3.set_pattern(self.comboBox_patternSelect.currentText())
        self.doubleSpinBox.valueChanged.connect(self.motion_3.set_velocity)
        self.doubleSpinBox.valueChanged.connect(self.motion_4.set_velocity)
        self.motion_3.set_velocity(self.doubleSpinBox.value())

        self.output_device = None

        self.websocket_server = net.websocketserver.WebSocketServer(self)
        self.websocket_server.new_tcode_command.connect(self.tcode_command_router.route_command)

        self.tcpudp_server = net.tcpudpserver.TcpUdpServer(self)
        self.tcpudp_server.new_tcode_command.connect(self.tcode_command_router.route_command)

        self.serial_proxy = net.serialproxy.SerialProxy(self)
        self.serial_proxy.new_tcode_command.connect(self.tcode_command_router.route_command)

        self.buttplug_wsdm_client = net.buttplug_wsdm_client.ButtplugWsdmClient(self)
        self.buttplug_wsdm_client.new_tcode_command.connect(self.tcode_command_router.route_command)

        self.gamepad_handler = net.gamepad.GamepadHandler(self)
        self.gamepad_handler.position_changed.connect(self.motion_3.mouse_event)
        self.gamepad_handler.position_changed.connect(lambda a, b: self.motion_4.mouse_event(a, b, 0.0))
        self.gamepad_handler.carrier_frequency_change.connect(self.gamepad_carrier_frequency_change)
        self.gamepad_handler.volume_change.connect(self.gamepad_volume_change)
        self.gamepad_handler.pulse_frequency_change.connect(self.gamepad_pulse_frequency_change)
        self.gamepad_handler.pulse_width_change.connect(self.gamepad_pulse_width_change)
        self.gamepad_handler.shock_triggered.connect(self.gamepad_shock_triggered)
        self.gamepad_handler.shock_released.connect(self.gamepad_shock_released)
        self.gamepad_handler.mute_triggered.connect(self.gamepad_mute_triggered)
        self.gamepad_handler.refreshSettings()

        self.webui_server = net.webserver.WebUIServer(self, self)

        # Remote control client for controlling other Restim instances
        self.remote_control = net.remote_control.RemoteControlClient(self)
        self._init_remote_control()

        # Shock state tracking
        self._shock_pre_volume = None
        # Mute state tracking
        self._mute_pre_volume = None
        self._is_muted = False

        self.tab_volume.set_monitor_axis([
            self.alpha,
            self.beta,
            self.gamma,
        ])

        # stop audio when user modifies settings in media tab
        self.page_media.dialogOpened.connect(self.signal_stop)
        self.page_media.funscriptMappingChanged.connect(self.funscript_mapping_changed)
        self.page_media.connectionStatusChanged.connect(self.media_connection_status_changed)
        self.page_media.bake_audio_button.clicked.connect(self.open_write_audio_dialog)

        # trigger updates.... maybe not all needed?
        # self.tab_carrier.settings_changed()
        self.tab_pulse_settings.settings_changed()
        self.tab_threephase.settings_changed()
        self.tab_volume.refresh_master_volume()
        self.tab_vibrate.settings_changed()

        self.wizard = DeviceSelectionWizard(self)
        self.actionDevice_selection_wizard.triggered.connect(self.open_setup_wizard)

        self.dialog = qt_ui.funscript_conversion_dialog.FunscriptConversionDialog()
        self.actionFunscript_conversion.triggered.connect(self.open_funscript_conversion_dialog)

        self.simfile_conversion_dialog = qt_ui.simfile_conversion_dialog.SimfileConversionDialog()
        self.actionSimfile_conversion.triggered.connect(self.open_simfile_conversion_dialog)

        self.focstim_flash_dialog = qt_ui.focstim_flash_dialog.FocStimFlashDialog()
        self.actionFirmware_updater.triggered.connect(self.open_focstim_flash_dialog)

        self.funscript_decomposition_dialog = qt_ui.funscript_decomposition_dialog.FunscriptDecompositionDialog()
        self.actionFunscript_decomposition.triggered.connect(self.open_funscript_decomposition_dialog)

        self.settings_dialog = qt_ui.preferences_dialog.PreferencesDialog()
        self.actionPreferences.triggered.connect(self.open_preferences_dialog)

        self.about_dialog = qt_ui.about_dialog.AboutDialog(self)
        self.actionAbout.triggered.connect(self.open_about_dialog)

        self.iconMedia = IconWithConnectionStatus(self.actionMedia.icon(), self.toolBar.widgetForAction(self.actionMedia))
        self.actionMedia.setIcon(QIcon(self.iconMedia))
        # self.iconDevice = IconWithConnectionStatus(self.actionDevice.icon(), self.toolBar.widgetForAction(self.actionDevice))
        # self.actionDevice.setIcon(QIcon(self.iconDevice))

        self.connect_signals_slots_actionbar()

        self.refresh_device_type()

        config = DeviceConfiguration.from_settings()
        if config.device_type == DeviceType.NONE:
            self.timer = QTimer()
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self.open_setup_wizard)
            self.timer.start(0)

        self.autostart_timer = QTimer()
        self.autostart_timer.setSingleShot(True)
        self.autostart_timer.timeout.connect(self.autostart_timeout)
        self.autostart_timer.setInterval(5000)

    def connect_signals_slots_actionbar(self):
        def uncheck():
            self.actionControl.setChecked(False)
            self.actionMedia.setChecked(False)
            # self.actionDevice.setChecked(False)
            # self.actionLog.setChecked(False)

        def show_control():
            uncheck()
            self.actionControl.setChecked(True)
            self.stackedWidget.setCurrentIndex(self.stackedWidget.indexOf(self.page_control))

        def show_media():
            uncheck()
            self.actionMedia.setChecked(True)
            self.stackedWidget.setCurrentIndex(self.stackedWidget.indexOf(self.page_media))

        # def show_device():
        #     uncheck()
        #     self.actionDevice.setChecked(True)
        #     self.stackedWidget.setCurrentIndex(self.stackedWidget.indexOf(self.page_device))

        # def show_log():
        #     uncheck()
        #     self.actionLog.setChecked(True)
        #     self.stackedWidget.setCurrentIndex(self.stackedWidget.indexOf(self.page_log))

        self.actionControl.triggered.connect(show_control)
        self.actionMedia.triggered.connect(show_media)
        # self.actionDevice.triggered.connect(show_device)
        # self.actionLog.triggered.connect(show_log)
        self.actionStart.triggered.connect(self.signal_start_stop)

    def media_connection_status_changed(self, status: MediaConnectionState):
        """
        Called whenever the media connection status changes.
        """
        if status.is_playing():
            self.iconMedia.set_playing()
        elif status.is_connected():
            self.iconMedia.set_connected()
        else:
            self.iconMedia.set_not_connected()

    def funscript_mapping_changed(self):
        """
        Called whenever the loaded funscripts change
        """
        logger.info('funscript mapping changed, re-linking scripts.')
        if self.page_media.autostart_enabled():
            if self.playstate == PlayState.PLAYING:
                self.signal_stop(PlayState.WAITING_ON_LOAD)
                self.autostart_timer.start()
        else:
            self.signal_stop(PlayState.STOPPED)

        device = DeviceConfiguration.from_settings()
        algorithm_factory = AlgorithmFactory(
            self,
            FunscriptKitModel.load_from_settings(),
            self.page_media.model,
            self.page_media.current_media_sync(),
            self.page_media.current_media_sync(),
            load_funscripts=not self.page_media.is_internal(),
        )

        # 3-phase visualization
        self.motion_3.set_scripts(
            algorithm_factory.get_axis_alpha(),
            algorithm_factory.get_axis_beta(),
        )

        # 4-phase visualization
        self.motion_4.set_scripts(
            algorithm_factory.get_axis_alpha(),
            algorithm_factory.get_axis_beta(),
            algorithm_factory.get_axis_gamma(),
        )

        # volume tab
        self.tab_volume.set_monitor_axis([
            algorithm_factory.get_axis_alpha(),
            algorithm_factory.get_axis_beta(),
        ])
        self.tab_volume.axis_funscript_volume = algorithm_factory.get_axis_volume_api()

        # continuous tab
        self.tab_carrier.carrier_controller.link_axis(algorithm_factory.get_axis_continuous_carrier_frequency())

        # pulse tab
        self.tab_pulse_settings.carrier_controller.link_axis(algorithm_factory.get_axis_pulse_carrier_frequency())
        self.tab_pulse_settings.pulse_frequency_controller.link_axis(algorithm_factory.get_axis_pulse_frequency())
        self.tab_pulse_settings.pulse_width_controller.link_axis(algorithm_factory.get_axis_pulse_width())
        self.tab_pulse_settings.pulse_interval_random_controller.link_axis(algorithm_factory.get_axis_pulse_interval_random())
        self.tab_pulse_settings.pulse_rise_time_controller.link_axis(algorithm_factory.get_axis_pulse_rise_time())

        # vibration tab
        self.tab_vibrate.vib1_enabled_controller.link_axis(algorithm_factory.get_axis_vib1_enabled())
        self.tab_vibrate.vib1_freq_controller.link_axis(algorithm_factory.get_axis_vib1_frequency())
        self.tab_vibrate.vib1_strength_controller.link_axis(algorithm_factory.get_axis_vib1_strength())
        self.tab_vibrate.vib1_left_right_bias_controller.link_axis(algorithm_factory.get_axis_vib1_left_right_bias())
        self.tab_vibrate.vib1_high_low_bias_controller.link_axis(algorithm_factory.get_axis_vib1_high_low_bias())
        self.tab_vibrate.vib1_random_controller.link_axis(algorithm_factory.get_axis_vib1_random())
        self.tab_vibrate.vib2_enabled_controller.link_axis(algorithm_factory.get_axis_vib2_enabled())
        self.tab_vibrate.vib2_freq_controller.link_axis(algorithm_factory.get_axis_vib2_frequency())
        self.tab_vibrate.vib2_strength_controller.link_axis(algorithm_factory.get_axis_vib2_strength())
        self.tab_vibrate.vib2_left_right_bias_controller.link_axis(algorithm_factory.get_axis_vib2_left_right_bias())
        self.tab_vibrate.vib2_high_low_bias_controller.link_axis(algorithm_factory.get_axis_vib2_high_low_bias())
        self.tab_vibrate.vib2_random_controller.link_axis(algorithm_factory.get_axis_vib2_random())

        # neostim tab
        # TODO

        if all((not self.page_media.is_internal(),
                self.page_media.has_media_file_loaded(),
                self.page_media.autostart_enabled(),
                self.playstate == PlayState.WAITING_ON_LOAD)):
            logger.info("autostart audio")
            self.signal_start()

    def refresh_device_type(self):
        def set_visible(widget, state):
            self.tabWidget.setTabVisible(self.tabWidget.indexOf(widget), state)
            self.tabWidget.setTabEnabled(self.tabWidget.indexOf(widget), state)

        all_tabs = {self.tab_threephase,
                    self.tab_fourphase,
                    self.tab_pulse_settings,
                    self.tab_carrier,
                    self.tab_volume,
                    self.tab_vibrate,
                    self.tab_details,
                    self.tab_a_b_testing,
                    self.tab_neostim}

        visible = {self.tab_threephase, self.tab_volume, self.tab_vibrate, self.tab_details}

        config = DeviceConfiguration.from_settings()

        # determine tab visibility
        if config.device_type == DeviceType.AUDIO_THREE_PHASE:
            if config.waveform_type == WaveformType.CONTINUOUS:
                visible |= {self.tab_carrier}
            if config.waveform_type == WaveformType.PULSE_BASED:
                visible |= {self.tab_pulse_settings}
            if config.waveform_type == WaveformType.A_B_TESTING:
                visible |= {self.tab_a_b_testing}
        if config.device_type == DeviceType.FOCSTIM_THREE_PHASE:
            visible |= {self.tab_pulse_settings}
            visible -= {self.tab_vibrate}
        if config.device_type == DeviceType.FOCSTIM_FOUR_PHASE:
            visible |= {self.tab_pulse_settings, self.tab_fourphase}
            visible -= {self.tab_vibrate, self.tab_threephase, self.tab_details}
        if config.device_type == DeviceType.NEOSTIM_THREE_PHASE:
            visible |= {self.tab_neostim}
            visible -= {self.tab_vibrate, self.tab_details}

        for tab in all_tabs:
            set_visible(tab, tab in visible)

        # set safety limits
        self.tab_carrier.set_safety_limits(config.min_frequency, config.max_frequency)
        self.tab_pulse_settings.set_safety_limits(config.min_frequency, config.max_frequency)
        self.tab_a_b_testing.set_safety_limits(config.min_frequency, config.max_frequency)

        # configure tcode router
        if config.waveform_type == WaveformType.CONTINUOUS:
            self.tcode_command_router.set_carrier_axis(self.tab_carrier.axis_carrier)
        if config.waveform_type == WaveformType.PULSE_BASED:
            self.tcode_command_router.set_carrier_axis(self.tab_pulse_settings.axis_carrier_frequency)

        # populate motion generator and patterns combobox
        if config.device_type in (DeviceType.AUDIO_THREE_PHASE, DeviceType.NEOSTIM_THREE_PHASE, DeviceType.FOCSTIM_THREE_PHASE):
            self.motion_3.set_enable(True)
            self.motion_4.set_enable(False)
            self.stackedWidget_visual.setCurrentIndex(
                self.stackedWidget_visual.indexOf(self.page_threephase)
            )

        if config.device_type == DeviceType.FOCSTIM_FOUR_PHASE:
            self.motion_3.set_enable(False)
            self.motion_4.set_enable(True)
            self.stackedWidget_visual.setCurrentIndex(
                self.stackedWidget_visual.indexOf(self.page_fourphase)
            )

        if config.device_type == DeviceType.AUDIO_THREE_PHASE:
            self.graphicsView_threephase.set_background(stereo=True)
            self.tab_threephase.phase_widget_calibration.set_background(stereo=True)
        else:
            self.graphicsView_threephase.set_background(foc=True)
            self.tab_threephase.phase_widget_calibration.set_background(foc=True)

        self.refresh_pattern_combobox()

    def pattern_selection_changed(self, index):
        pattern = self.comboBox_patternSelect.currentData()
        self.motion_3.set_pattern(pattern)
        self.motion_4.set_pattern(pattern)

    def signal_start_stop(self):
        if self.playstate == PlayState.STOPPED:
            self.signal_start()
        else:
            self.signal_stop(PlayState.STOPPED)

    def signal_start(self):
        assert self.output_device is None

        self.autostart_timer.stop()
        device = DeviceConfiguration.from_settings()
        algorithm_factory = AlgorithmFactory(
            self,
            FunscriptKitModel.load_from_settings(),
            self.page_media.model,
            self.page_media.current_media_sync(),
            self.page_media.current_media_sync(),
            load_funscripts=not self.page_media.is_internal(),
        )
        algorithm = algorithm_factory.create_algorithm(device)

        if device.device_type in [
            DeviceType.AUDIO_THREE_PHASE,
        ]: # is audio device
            api_name = qt_ui.settings.audio_api.get() or sd.query_hostapis(sd.default.hostapi)['name']
            output_device_name = qt_ui.settings.audio_output_device.get() or sd.query_devices(sd.default.device[1])['name']
            latency = qt_ui.settings.audio_latency.get() or 'high'
            try:
                latency = float(latency)
            except ValueError:
                pass

            output_device = AudioStimDevice(None)
            mapping_parameters = output_device.auto_detect_channel_mapping_parameters(algorithm)
            output_device.start(api_name, output_device_name, latency, algorithm, mapping_parameters)
            if output_device.is_connected_and_running():
                self.output_device = output_device
                self.playstate = PlayState.PLAYING
                self.tab_volume.set_play_state(self.playstate)
                self.refresh_play_button_icon()
        elif device.device_type in (DeviceType.FOCSTIM_THREE_PHASE, DeviceType.FOCSTIM_FOUR_PHASE):
            output_device = FOCStimProtoDevice()
            use_teleplot = qt_ui.settings.focstim_use_teleplot.get()
            dump_notifications = qt_ui.settings.focstim_dump_notifications_to_file.get()
            comms_wifi = qt_ui.settings.focstim_communication_wifi.get()
            if not comms_wifi:
                serial_port_name = qt_ui.settings.focstim_serial_port.get()
                output_device.start_serial(serial_port_name, use_teleplot, dump_notifications, algorithm)
            else:
                ip = qt_ui.settings.focstim_ip.get()
                output_device.start_tcp(ip, 55533, use_teleplot, dump_notifications, algorithm)
            if output_device.is_connected_and_running():
                self.output_device = output_device
                self.playstate = PlayState.PLAYING
                self.tab_volume.set_play_state(self.playstate)
                self.refresh_play_button_icon()
        elif device.device_type == DeviceType.NEOSTIM_THREE_PHASE:
            output_device = NeoStim()
            serial_port_name = qt_ui.settings.neostim_serial_port.get()
            output_device.start(serial_port_name, algorithm)
            if output_device.is_connected_and_running():
                self.output_device = output_device
                self.playstate = PlayState.PLAYING
                self.tab_volume.set_play_state(self.playstate)
                self.refresh_play_button_icon()
        else:
            raise RuntimeError("Unknown device type")

        # Broadcast play state to web UI clients and remote instances
        if self.playstate == PlayState.PLAYING:
            self.webui_server.broadcast_play_state(self.playstate)
            self.remote_control_send_play()

    def signal_stop(self, new_playstate: PlayState = PlayState.STOPPED):
        if self.output_device is not None:
            self.output_device.stop()
            self.output_device = None
        self.playstate = new_playstate
        self.tab_volume.set_play_state(self.playstate)
        self.refresh_play_button_icon()
        self.webui_server.broadcast_play_state(self.playstate)
        self.remote_control_send_stop()

    def autostart_timeout(self):
        print('autostart timeout')
        if self.playstate == PlayState.WAITING_ON_LOAD:
            logger.info("autostart timeout reached. No longer starting audio on file load")
            self.signal_stop(PlayState.STOPPED)

    def refresh_play_button_icon(self):
        if self.playstate in (PlayState.PLAYING, PlayState.WAITING_ON_LOAD):
            self.actionStart.setIcon(QtGui.QIcon(":/restim/stop-sign_poly.svg"))
            self.actionStart.setText("Stop")
        else:
            self.actionStart.setIcon(QtGui.QIcon(":/restim/play_poly.svg"))
            self.actionStart.setText("Start")

    def open_setup_wizard(self):
        self.signal_stop(PlayState.STOPPED)
        self.wizard.exec()
        self.refresh_device_type()
        self.reload_settings()

    def open_funscript_conversion_dialog(self):
        self.signal_stop(PlayState.STOPPED)
        self.dialog.exec()

    def open_simfile_conversion_dialog(self):
        self.signal_stop(PlayState.STOPPED)
        self.simfile_conversion_dialog.exec()

    def open_focstim_flash_dialog(self):
        self.signal_stop(PlayState.STOPPED)
        self.focstim_flash_dialog.exec()

    def open_funscript_decomposition_dialog(self):
        self.signal_stop(PlayState.STOPPED)
        self.funscript_decomposition_dialog.exec()

    def open_preferences_dialog(self):
        self.signal_stop(PlayState.STOPPED)
        self.settings_dialog.exec()
        self.reload_settings()

    def open_about_dialog(self):
        self.signal_stop(PlayState.STOPPED)
        self.about_dialog.exec()

    def open_write_audio_dialog(self):
        device = DeviceConfiguration.from_settings()
        kit = FunscriptKitModel.load_from_settings()
        filename = self.page_media.loaded_media_path
        dialog = AudioWriteDialog(self, kit, self.page_media.model, device, filename)
        dialog.exec()

    def reload_settings(self):
        """
        Reload everything that is stored in settings and may be changed
        by the preferences dialog
        """
        self.tcode_command_router.reload_kit()
        self.tab_volume.refreshSettings()
        self.buttplug_wsdm_client.refreshSettings()
        self.gamepad_handler.refreshSettings()
        self.funscript_mapping_changed()  # reload funscript axis
        self.tab_a_b_testing.refreshSettings()
        self.motion_3.refreshSettings()
        self.motion_4.refreshSettings()
        self.refresh_pattern_combobox()
        self._refresh_remote_control()

    def refresh_pattern_combobox(self):
        config = DeviceConfiguration.from_settings()
        currently_selected_text = self.comboBox_patternSelect.currentText()

        if config.device_type in (DeviceType.AUDIO_THREE_PHASE, DeviceType.NEOSTIM_THREE_PHASE, DeviceType.FOCSTIM_THREE_PHASE):
            self.comboBox_patternSelect.clear()
            for pattern in self.motion_3.patterns:
                self.comboBox_patternSelect.addItem(pattern.name(), pattern)
        else:
            self.comboBox_patternSelect.clear()
            for pattern in self.motion_4.patterns:
                self.comboBox_patternSelect.addItem(pattern.name(), pattern)

        # try to select pattern with similar name as was previously selected
        index = self.comboBox_patternSelect.findText(currently_selected_text)
        if index == -1:
            index = 0
        self.comboBox_patternSelect.setCurrentIndex(index)


    def save_settings(self):
        """
        Save everything that is stored in settings but isn't immediately saved
        for performance reasons.
        """
        self.tab_threephase.save_settings()
        self.tab_fourphase.save_settings()
        self.tab_carrier.save_settings()
        self.tab_vibrate.save_settings()
        self.tab_pulse_settings.save_settings()
        self.tab_volume.save_settings()

    def gamepad_carrier_frequency_change(self, direction: int):
        """Handle gamepad LB/RB for carrier frequency adjustment"""
        step = qt_ui.settings.gamepad_carrier_step.get()
        # Try pulse settings first (for pulse-based mode)
        if self.tab_pulse_settings.isVisible():
            current = self.tab_pulse_settings.carrier.value()
            new_value = current + (direction * step)
            self.tab_pulse_settings.carrier.setValue(new_value)
        # Otherwise use continuous carrier settings
        elif self.tab_carrier.isVisible():
            current = self.tab_carrier.carrier.value()
            new_value = current + (direction * step)
            self.tab_carrier.carrier.setValue(new_value)

    def gamepad_volume_change(self, direction: int):
        """Handle gamepad D-pad Up/Down for volume adjustment"""
        step = qt_ui.settings.gamepad_volume_step.get()
        current = self.doubleSpinBox_volume.value()
        new_value = max(0.0, current + (direction * step))
        self.doubleSpinBox_volume.setValue(new_value)

    def gamepad_pulse_frequency_change(self, direction: int):
        """Handle gamepad D-pad Left/Right for pulse frequency adjustment"""
        step = qt_ui.settings.gamepad_pulse_frequency_step.get()
        if self.tab_pulse_settings.isVisible():
            current = self.tab_pulse_settings.pulse_freq_slider.value()
            new_value = current + (direction * step)
            self.tab_pulse_settings.pulse_freq_slider.setValue(new_value)

    def gamepad_pulse_width_change(self, direction: int):
        """Handle gamepad button for pulse width adjustment"""
        step = qt_ui.settings.gamepad_pulse_width_step.get()
        if self.tab_pulse_settings.isVisible():
            current = self.tab_pulse_settings.pulse_width_slider.value()
            new_value = current + (direction * step)
            self.tab_pulse_settings.pulse_width_slider.setValue(new_value)

    def gamepad_shock_triggered(self):
        """Handle gamepad shock button press - raise volume while held"""
        # Only store volume if not already in shock
        if self._shock_pre_volume is None:
            self._shock_pre_volume = self.doubleSpinBox_volume.value()

        # Set volume to shock level
        shock_volume = qt_ui.settings.gamepad_shock_volume.get()
        self.doubleSpinBox_volume.setValue(shock_volume)

    def gamepad_shock_released(self):
        """Handle gamepad shock button release - restore volume"""
        if self._shock_pre_volume is not None:
            self.doubleSpinBox_volume.setValue(self._shock_pre_volume)
            self._shock_pre_volume = None

    def gamepad_mute_triggered(self):
        """Handle gamepad mute button - toggle mute"""
        if self._is_muted:
            # Unmute - restore previous volume
            if self._mute_pre_volume is not None:
                self.doubleSpinBox_volume.setValue(self._mute_pre_volume)
                self._mute_pre_volume = None
            self._is_muted = False
        else:
            # Mute - store current volume and set to 0
            self._mute_pre_volume = self.doubleSpinBox_volume.value()
            self.doubleSpinBox_volume.setValue(0.0)
            self._is_muted = True

    def _init_remote_control(self):
        """Initialize the remote control client."""
        self._refresh_remote_control()

        # Timer for sending position updates to remote instances (~30Hz)
        self._remote_position_timer = QTimer(self)
        self._remote_position_timer.timeout.connect(self._remote_broadcast_position)
        self._remote_position_timer.start(33)

        if qt_ui.settings.remote_control_enabled.get():
            self.remote_control.start()

    def _remote_broadcast_position(self):
        """Send current position to remote instances."""
        if qt_ui.settings.remote_control_enabled.get() and qt_ui.settings.remote_control_sync_position.get():
            if self.remote_control.get_connected_count() > 0:
                self.remote_control.send_position(
                    self.alpha.last_value(),
                    self.beta.last_value(),
                    self.gamma.last_value()
                )

    def _refresh_remote_control(self):
        """Refresh remote control settings."""
        import json
        from net.remote_control import RemoteInstance

        # Load instances from settings
        try:
            instances_json = qt_ui.settings.remote_control_instances.get()
            instances_data = json.loads(instances_json)
            instances = []
            for data in instances_data:
                instances.append(RemoteInstance(
                    url=data.get('url', ''),
                    enabled=data.get('enabled', True),
                    username=data.get('username', ''),
                    password=data.get('password', '')
                ))
            self.remote_control.set_instances(instances)
        except Exception as e:
            logger.warning(f"Failed to load remote control instances: {e}")

        # Start or stop based on enabled setting
        if qt_ui.settings.remote_control_enabled.get():
            self.remote_control.start()
        else:
            self.remote_control.stop()

    def remote_control_send_position(self, alpha: float, beta: float):
        """Send position update to remote instances."""
        if qt_ui.settings.remote_control_enabled.get() and qt_ui.settings.remote_control_sync_position.get():
            self.remote_control.send_position(alpha, beta, self.gamma.last_value())

    def remote_control_send_volume(self, value: float):
        """Send volume update to remote instances."""
        if qt_ui.settings.remote_control_enabled.get() and qt_ui.settings.remote_control_sync_volume.get():
            self.remote_control.send_volume(value)

    def remote_control_send_carrier(self, frequency: float):
        """Send carrier frequency update to remote instances."""
        if qt_ui.settings.remote_control_enabled.get() and qt_ui.settings.remote_control_sync_carrier.get():
            self.remote_control.send_carrier(frequency)

    def remote_control_send_play(self):
        """Send play command to remote instances."""
        if qt_ui.settings.remote_control_enabled.get() and qt_ui.settings.remote_control_sync_play_state.get():
            self.remote_control.send_play()

    def remote_control_send_stop(self):
        """Send stop command to remote instances."""
        if qt_ui.settings.remote_control_enabled.get() and qt_ui.settings.remote_control_sync_play_state.get():
            self.remote_control.send_stop()

    def closeEvent(self, event):
        logger.warning('Shutting down')
        if self.output_device is not None:
            self.output_device.stop()
        self.gamepad_handler.set_enabled(False)
        self.webui_server.stop()
        self.remote_control.stop()
        self.save_settings()
        event.accept()


def run():
    log_path = os.getcwd()
    logging.basicConfig(filename=os.path.join(log_path, 'restim.log'), filemode='w')
    logging.getLogger().addHandler(logging.StreamHandler())
    logger = logging.getLogger('restim')
    logger.setLevel(logging.DEBUG)
    logging.getLogger('matplotlib').setLevel(logging.WARN)

    def excepthook(exc_type, exc_value, exc_tb):
        exc_info = (exc_type, exc_value, exc_tb)
        logger.critical('Exception occurred', exc_info=exc_info)
        QApplication.quit()

    sys.excepthook = excepthook

    app = QApplication(sys.argv)
    win = Window()
    win.show()
    sys.exit(app.exec())