# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
import re
from octoprint.events import Events
from time import sleep
import time
import OPi.GPIO as GPIO
import flask

import orangepi.pc          # Lite / One / PC / PC Plus / Plus 2E (40 GPIO)
import orangepi.pc2         # PC 2 (40 GPIO)
import orangepi.prime       # Prime (40 GPIO)
import orangepi.winplus     # Win Plus (40 GPIO)
import orangepi.pi4         # 4 / 4B (40 GPIO)

import orangepi.oneplus     # Lite 2 / One Plus (26 GPIO)
import orangepi.zeroplus    # R1 / Zero / Zero Plus (26 GPIO)
import orangepi.zeroplus2   # Zero Plus 2 (26 GPIO)
import orangepi.pi3         # 3 (26 GPIO)


class Filament_sensor_simplified_OPiPlugin(octoprint.plugin.StartupPlugin,
									   octoprint.plugin.EventHandlerPlugin,
									   octoprint.plugin.TemplatePlugin,
									   octoprint.plugin.SettingsPlugin,
									   octoprint.plugin.SimpleApiPlugin,
									   octoprint.plugin.BlueprintPlugin,
									   octoprint.plugin.AssetPlugin):

	# last UI poll status
	ui_status = 0
	last_status = None
	loaded = False

	# bounce time for sensing
	bounce_time = 250

	# pin number used as plugin disabled
	pin_num_disabled = 0

	# default gcode
	default_gcode = 'M600 X0 Y0'

	# gpio mode disabled
	gpio_mode_disabled = False

	# printing flag
	printing = False

	# detection active
	detectionOn = False

	# has the GPIO been activated
	gpio_activated = False

	def initialize(self):
		GPIO.setwarnings(True)
		# flag telling that we are expecting M603 response
		self.checking_M600 = False
		# flag defining if printer supports M600
		self.M600_supported = True
		# flag defining that the filament change command has been sent to printer, this does not however mean that
		# filament change sequence has been started
		self.changing_filament_initiated = False
		# flag defining that the filament change sequence has been started and the M600 command has been se to printer
		self.changing_filament_command_sent = False
		# flag defining that the filament change sequence has been started and the printer is waiting for user
		# to put in new filament
		self.paused_for_user = False
		# flag for determining if the gcode starts with M600
		self.M600_gcode = True
		# flag to prevent double detection
		self.changing_filament_started = False

	@property
	def pin(self):
		return int(self._settings.get(["pin"]))

	@property
	def power(self):
		return int(self._settings.get(["power"]))

	@property
	def g_code(self):
		return self._settings.get(["g_code"])

	@property
	def triggered(self):
		return int(self._settings.get(["triggered"]))

	@property
	def orangepimodel(self):
		return int(self._settings.get(["orangepimodel"]))

	# AssetPlugin hook
	def get_assets(self):
		return dict(js=["js/filamentsensorsimplifiedopi.js"], css=["css/filamentsensorsimplifiedopi.css"])

	# Template hooks
	def get_template_configs(self):
		return [dict(type="settings", custom_bindings=True)]

	# Settings hook
	def get_settings_defaults(self):
		return dict(
			orangepimodel=1,
			pin=self.pin_num_disabled,  # Default is -1
			power=0,
			g_code=self.default_gcode,
			triggered=0,
			cmd_action="gcode"
		)

	# simpleApiPlugin
	def get_api_commands(self):
		return dict(testSensor=["pin", "power"],pollStatus=[],)

	@octoprint.plugin.BlueprintPlugin.route("/disable", methods=["GET"])
	def get_disable(self):
		self._logger.debug("getting disabled info")
		if self.printing:
			self._logger.debug("printing")
			gpio_mode_disabled = True
		else:
			self._logger.debug("not printing")
			gpio_mode_disabled = self.gpio_mode_disabled

		return flask.jsonify(gpio_mode_disabled=gpio_mode_disabled, printing=self.printing)

	# test pin value, power pin or if its used by someone else
	def on_api_command(self, command, data):
		if command == "pollStatus":
			if self.loaded == False:
				return "Not started", 404

			# only poll every 60 seconds and if auto detection is not running
			timenow = int(time.time())
			if self.detectionOn == False and (timenow - self.ui_status) >= 60:
				if self.setupGPIO():
					self.no_filament()
			return flask.jsonify({'status' : self.last_status})

		try:
			selected_power = int(data.get("power"))
			selected_pin = int(data.get("pin"))
			triggered = int(data.get("triggered"))
			selected_pimodel = int(data.get("orangepimodel"))

###			self._logger.info("ON_API_COMMAND_start: getmode %s, Orange Pi model %s, selected pin %s", GPIO.getmode(), selected_pimodel, selected_pin)	###
			# if mode set by 3rd party don't set it again
			if not self.gpio_mode_disabled:
###				self._logger.info("ON_API_COMMAND_set_mode: getmode %s, Orange Pi model %s, selected pin %s", GPIO.getmode(), selected_pimodel, selected_pin)	###
				if self.gpio_activated:
					GPIO.cleanup()
###					self._logger.info("ON_API_COMMAND_cleanup: self.gpio_activated getmode %s, Orange Pi model %s, selected pin %s", GPIO.getmode(), selected_pimodel, selected_pin)	###
				if selected_pimodel is 1:
					GPIO.setmode(orangepi.pc.BOARD)
				elif selected_pimodel is 2:
					GPIO.setmode(orangepi.pc2.BOARD)
				elif selected_pimodel is 3:
					GPIO.setmode(orangepi.prime.BOARD)
				elif selected_pimodel is 4:
					GPIO.setmode(orangepi.winplus.BOARD)
				elif selected_pimodel is 5:
					GPIO.setmode(orangepi.pi4.BOARD)
				elif selected_pimodel is 6:
					GPIO.setmode(orangepi.oneplus.BOARD)
				elif selected_pimodel is 7:
					GPIO.setmode(orangepi.zeroplus.BOARD)
				elif selected_pimodel is 8:
					GPIO.setmode(orangepi.zeroplus2.BOARD)
				elif selected_pimodel is 9:
					GPIO.setmode(orangepi.pi3.BOARD)
###				self._logger.info("ON_API_COMMAND_set_mode_end: getmode %s, Orange Pi model %s, selected pin %s", GPIO.getmode(), selected_pimodel, selected_pin)	###
				self.gpio_activated = True

			# before read don't let the pin float
			self._logger.debug("selected power is %s" % selected_power)
			if selected_pin in GPIO._exports:	### check if configured, 2 calls to GPIO.setup returns error
				GPIO.cleanup(selected_pin)		### clean if configured
###				self._logger.info("ON_API_COMMAND_cleanup: selected_pin in GPIO._exports %s, Orange Pi model %s, selected pin %s", GPIO.getmode(), selected_pimodel, selected_pin)	###
###			self._logger.info("ON_API_COMMAND_fijar_pin: selected pin %s", selected_pin)	###
			if selected_power is 0:
				GPIO.setup(selected_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
			else:
				GPIO.setup(selected_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

			pin_value = GPIO.input(selected_pin)
			self._logger.debug("pin value is %s" % pin_value)

			# reset input to pull down after read
			GPIO.cleanup(selected_pin)	### clean, 2 calls to GPIO.setup returns error
###			self._logger.info("ON_API_COMMAND_cleanup: limpiar antes de volver a configurar, getmode %s, Orange Pi model %s, selected pin %s", GPIO.getmode(), selected_pimodel, selected_pin)	###
			GPIO.setup(selected_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

			triggered_bool = (pin_value + selected_power + triggered) % 2 is 0
			self._logger.debug("triggered value %s" % triggered_bool)
			return flask.jsonify(triggered=triggered_bool)
		except ValueError as e:
			self._logger.error(str(e))
			# ValueError occurs when reading from power, ground or out of range pins
			return "", 556

	def on_after_startup(self):
		self._logger.info("Filament sensor simplified Orange Pi started")
		gpio_mode = GPIO.getmode()

		# Fix old -1 settings to 0
		if self.pin is -1:
			self._logger.info("Fixing old settings from -1 to 0")
			self._settings.set(["pin"], 0)
			self.pin = 0
		if gpio_mode is not None:	### previously configured
			self.gpio_mode_disabled = True
###			self._logger.info("ON_AFTER_STARTUP_inicio: Modo ya fijado, se desactiva")	###
		else:
###			self._logger.info("ON_AFTER_STARTUP_inicio: getmode %s, Orange Pi model %s", GPIO.getmode(), self.orangepimodel)	###
			if self.orangepimodel is 1:
				GPIO.setmode(orangepi.pc.BOARD)
			elif self.orangepimodel is 2:
				GPIO.setmode(orangepi.pc2.BOARD)
			elif self.orangepimodel is 3:
				GPIO.setmode(orangepi.prime.BOARD)
			elif self.orangepimodel is 4:
				GPIO.setmode(orangepi.winplus.BOARD)
			elif self.orangepimodel is 5:
				GPIO.setmode(orangepi.pi4.BOARD)
			elif self.orangepimodel is 6:
				GPIO.setmode(orangepi.oneplus.BOARD)
			elif self.orangepimodel is 7:
				GPIO.setmode(orangepi.zeroplus.BOARD)
			elif self.orangepimodel is 8:
				GPIO.setmode(orangepi.zeroplus2.BOARD)
			elif self.orangepimodel is 9:
				GPIO.setmode(orangepi.pi3.BOARD)

			self.gpio_mode_disabled = False
		self._logger.info("ON_AFTER_STARTUP_fin: GPIO-getmode is %s", GPIO.getmode())

		# get current status
		if self.setupGPIO():
			self.no_filament()

		# ready
		self.loaded = True

	def on_settings_save(self, data):
		# Retrieve any settings not changed in order to validate that the combination of new and old settings end up in a bad combination
		pin_to_save = self._settings.get_int(["pin"])
		pin_previous = pin_to_save	### save previous to clean if changed
		pimodel_to_save = self._settings.get_int(["orangepimodel"])

###		self._logger.info("ONSETTIGNSSAVE_antes: pintosave %s, pimodeltosave %s", pin_to_save, pimodel_to_save)	###
		if "pin" in data:
			pin_to_save = int(data.get("pin"))

		if "orangepimodel" in data:
			pimodel_to_save = int(data.get("orangepimodel"))

###		self._logger.info("ONSETTIGNSSAVE_despues: pintosave %s, pimodeltosave %s", pin_to_save, pimodel_to_save)	###
		if pin_to_save is not None:
			# check if pin is not power/ground pin or out of range but allow the disabled value (0)
			if pin_to_save is not self.pin_num_disabled:
				if (pin_to_save > 26 and pimodel_to_save > 5) or pin_to_save > 40:
					self._logger.info("You are trying to save pin %s which is out of range" % (pin_to_save))
					self._plugin_manager.send_plugin_message(self._identifier,
															 dict(type="error", autoClose=False,
																  msg="Filament sensor settings not saved, you are trying to use a pin which is out of range"))
					return
				if pin_to_save not in [3,5,7,11,13,15,19,21,23,27,29,31,33,35,37,8,10,12,16,18,22,24,26,28,32,36,38,40]:
					self._logger.info("You are trying to save pin %s which is ground/power" % (pin_to_save))
					self._plugin_manager.send_plugin_message(self._identifier,
															 dict(type="error", autoClose=False,
																  msg="Filament sensor settings not saved, you are trying to use a pin which is ground/power"))
					return
		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
		# clean previous pin
		if pin_previous != pin_to_save and pin_previous != 0:
			GPIO.cleanup(pin_previous)
###			self._logger.info("ON_SETTIGS_SAVE: limpiado pin %s", pin_previous)	###

		# get current status
		if self.setupGPIO():
			self.no_filament()

	def checkM600Enabled(self):
		sleep(1)
		self.checking_M600 = True
		self._printer.commands("M603")

	# this method is called before the gcode is sent to printer
	def sending_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs):
		if self.changing_filament_initiated and self.M600_supported:
			if self.changing_filament_command_sent and self.changing_filament_started:
				# M113 - host keepalive message, ignore this message
				if not re.search("^M113", cmd):
					self.changing_filament_initiated = False
					self.changing_filament_command_sent = False
					self.changing_filament_started = False
					if self.no_filament():
						self.send_out_of_filament()
			if cmd == self.g_code:
				self.changing_filament_command_sent = True

		# deliberate change
		if self.M600_supported and re.search("^M600", cmd):
			self.changing_filament_initiated = True
			self.changing_filament_command_sent = True

	# this method is called on gcode response
	def gcode_response_received(self, comm, line, *args, **kwargs):
		if self.changing_filament_command_sent:
			if re.search("busy: paused for user", line):
				self._logger.debug("received busy paused for user")
				if not self.paused_for_user:
					self._plugin_manager.send_plugin_message(self._identifier, dict(type="info", autoClose=False,
																					msg="Filament change: printer is waiting for user input."))
					self.paused_for_user = True
					self.changing_filament_started = True
			elif re.search("echo:busy: processing", line):
				self._logger.debug("received busy processing")
				if self.paused_for_user:
					self.paused_for_user = False

		# waiting for M603 command response
		if self.checking_M600:
			if re.search("^ok", line):
				self._logger.debug("Printer supports M600")
				self.M600_supported = True
				self.checking_M600 = False
			elif re.search("^echo:Unknown command: \"M603\"", line):
				self._logger.debug("Printer doesn't support M600")
				self.M600_supported = False
				self.checking_M600 = False
				self._settings.set(["cmd_action"], "opnative")
			else:
				self._logger.debug("M600 check unsuccessful, trying again")
				self.checkM600Enabled()
		return line

	# plugin disabled if pin set to -1
	def sensor_enabled(self):
		return self.pin != self.pin_num_disabled

	# read sensor input value
	def no_filament(self):
		try:
			filaStatus = (GPIO.input(self.pin) + self.power + self.triggered) % 2 is not 0
		except:
			filaStatus = False	### avoid error before configuring pin
		self.ui_status = int(time.time())
		self.last_status = filaStatus;

		self._plugin_manager.send_plugin_message(self._identifier, dict(type="iconStatus", noFilament=filaStatus))
		return filaStatus

	# method invoked on event
	def on_event(self, event, payload):
		# octoprint connects to 3D printer
		if event is Events.CONNECTED:
###			self._logger.info("ON_EVENT: CONNECTED %s", event)	###
			# if the command starts with M600, check if printer supports M600
			if re.search("^M600", self.g_code):
				self.M600_gcode = True
				self.checkM600Enabled()

		# octoprint disconnects from 3D printer, reset M600 enabled variable
		elif event is Events.DISCONNECTED:
###			self._logger.info("ON_EVENT: DISCONNECTED %s", event)	###
			self.M600_supported = True

		# if user has logged in show appropriate popup
		elif event is Events.CLIENT_OPENED:
###			self._logger.info("ON_EVENT: CLIENT_OPENED %s", event)	###
			if self.changing_filament_initiated and not self.changing_filament_command_sent:
				self.show_printer_runout_popup()
			elif self.changing_filament_command_sent and not self.paused_for_user:
				self.show_printer_runout_popup()
			# printer is waiting for user to put in new filament
			elif self.changing_filament_command_sent and self.paused_for_user:
				self._plugin_manager.send_plugin_message(self._identifier, dict(type="info", autoClose=False,
																				msg="Printer ran out of filament! It's waiting for user input"))
			# if the plugin hasn't been initialized
			if not self.sensor_enabled():
				self._plugin_manager.send_plugin_message(self._identifier, dict(type="info", autoClose=True,
																				msg="Don't forget to configure this plugin."))

			# get status for navbar
			if self.setupGPIO():
				self.no_filament()

		if not (self.M600_gcode and not self.M600_supported):
			# Enable sensor
			if event in (
					Events.PRINT_STARTED,
					Events.PRINT_RESUMED
			):
###				self._logger.info("ON_EVENT: PRINT_STARTED/RESUMED %s", event)	###
				self._logger.info("%s: Enabling filament sensor." % (event))
				if self.setupGPIO():
					# 0 = sensor is grounded, react to rising edge pulled up by pull up resistor
					if self.power is 0:
						# triggered when open
						if self.triggered is 0:
							self.turnOffDetection(event)
							self.detectionOn = True
							GPIO.add_event_detect(
								self.pin, GPIO.RISING,
								callback=self.sensor_callback)
						# triggered when closed
						else:
							self.turnOffDetection(event)
							self.detectionOn = True
							GPIO.add_event_detect(
								self.pin, GPIO.FALLING,
								callback=self.sensor_callback)
					# 1 = sensor is powered, react to falling edge pulled down by pull down resistor
					else:
						# triggered when open
						if self.triggered is 0:
							self.turnOffDetection(event)
							self.detectionOn = True
							GPIO.add_event_detect(
								self.pin, GPIO.RISING,
								callback=self.sensor_callback)
						# triggered when closed
						else:
							self.turnOffDetection(event)
							self.detectionOn = True
							GPIO.add_event_detect(
								self.pin, GPIO.FALLING,
								callback=self.sensor_callback)

					# print started with no filament present
					if self.no_filament():
						self._logger.info("Printing aborted: no filament detected!")
						self._printer.cancel_print()
						self._plugin_manager.send_plugin_message(self._identifier,
																 dict(type="error", autoClose=True,
																	  msg="No filament detected! Print cancelled."))
					# print started
					else:
						self.printing = True
					self.changing_filament_initiated = False
					self.changing_filament_command_sent = False
					self.paused_for_user = False

				# print started without plugin configuration
				else:
					self._plugin_manager.send_plugin_message(self._identifier,
															 dict(type="info", autoClose=True,
																  msg="You may have forgotten to configure this plugin."))

			# Disable sensor
			elif event in (
					Events.PRINT_DONE,
					Events.PRINT_FAILED,
					Events.PRINT_CANCELLED,
					Events.ERROR
			):
###				self._logger.info("ON_EVENT: PRINT_DONE/FAILED/CANCELLED %s", event)	###
				self.no_filament()
				self.turnOffDetection(event)
				self.changing_filament_initiated = False
				self.changing_filament_command_sent = False
				self.paused_for_user = False
				self.printing = False

	# move into a function to keep it clean and it can also be used from other places
	def setupGPIO(self):
		if self.sensor_enabled():
###			self._logger.info("SETUPGPIO_inicio: getmode %s, Orange Pi model %s, pin %s", GPIO.getmode(), self.orangepimodel, self.pin)	###
			if self.orangepimodel is 1:
				GPIO.setmode(orangepi.pc.BOARD)
			elif self.orangepimodel is 2:
				GPIO.setmode(orangepi.pc2.BOARD)
			elif self.orangepimodel is 3:
				GPIO.setmode(orangepi.prime.BOARD)
			elif self.orangepimodel is 4:
				GPIO.setmode(orangepi.winplus.BOARD)
			elif self.orangepimodel is 5:
				GPIO.setmode(orangepi.pi4.BOARD)
			elif self.orangepimodel is 6:
				GPIO.setmode(orangepi.oneplus.BOARD)
			elif self.orangepimodel is 7:
				GPIO.setmode(orangepi.zeroplus.BOARD)
			elif self.orangepimodel is 8:
				GPIO.setmode(orangepi.zeroplus2.BOARD)
			elif self.orangepimodel is 9:
				GPIO.setmode(orangepi.pi3.BOARD)

			if self.pin in GPIO._exports:	### check if configured, 2 calls to GPIO.setup returns error
				GPIO.cleanup(self.pin)		### clean if configured
###				self._logger.info("SETUPGPIO_cleanup: comprobar si ya está configurado antes de reconfigurar selected pin %s", self.pin)	###
			# 0 = sensor is grounded, react to rising edge pulled up by pull up resistor
###			self._logger.info("SETUPGPIO_fin: getmode %s, Orange Pi model %s, pin %s", GPIO.getmode(), self.orangepimodel, self.pin)	###
			if self.power is 0:
				GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
			# 1 = sensor is powered, react to falling edge pulled down by pull down resistor
			else:
				GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
			return True
		else:
			return False

	# turn off detection if on
	def turnOffDetection(self,event):
		if self.detectionOn:
			self._logger.info("%s: Disabling filament sensor." % (event))
			self.detectionOn = False
			GPIO.remove_event_detect(self.pin)

	def sensor_callback(self, channel):
		trigger = True
		for x in range(0, 5):	# bouncing control (not implemented in GPIO)
			sleep(0.05)
			if not self.no_filament():
				trigger = False

		if trigger:
			self._logger.info("Sensor on channel %s was triggered", channel)
			if not self.changing_filament_initiated:
				self.send_out_of_filament()
############# mi prueba
	def my_sensor_callback(self, channel):
		self._logger.info("MY_SENSOR_CALLBACK: Detectado en pin %s", channel)	###
###############<---

	def send_out_of_filament(self):
		self.show_printer_runout_popup()
		cmd_action = self._settings.get(["cmd_action"])
		if cmd_action == "gcode":
			self._logger.info("Sending out of filament GCODE: %s" % (self.g_code))
			self._printer.commands(self.g_code)
		else:
			self._logger.info("Pausing print using OctoPrint native pause")
			self._printer.pause_print()
		self.changing_filament_initiated = True

	def show_printer_runout_popup(self):
		self._plugin_manager.send_plugin_message(self._identifier,
												 dict(type="info", autoClose=False, msg="Printer ran out of filament!"))

	def get_update_information(self):
		# Define the configuration for your plugin to use with the Software Update
		# Plugin here. See https://docs.octoprint.org/en/master/bundledplugins/softwareupdate.html
		# for details.
		return dict(
			filamentsensorsimplifiedopi=dict(
				displayName="Filament sensor simplified Orange Pi",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="camochu",
				repo="Filament_sensor_simplified_OPi",
				current=self._plugin_version,

				# update method: pip
				pip="https://github.com/camochu/Filament_sensor_simplified_OPi/archive/{target_version}.zip"
			)
		)


# Starting with OctoPrint 1.4.0 OctoPrint will also support to run under Python 3 in addition to the deprecated
# Python 2. New plugins should make sure to run under both versions for now. Uncomment one of the following
# compatibility flags according to what Python versions your plugin supports!
# __plugin_pythoncompat__ = ">=2.7,<3" # only python 2
# __plugin_pythoncompat__ = ">=3,<4" # only python 3
__plugin_pythoncompat__ = ">=2.7,<4"  # python 2 and 3

__plugin_name__ = "Filament sensor simplified Orange Pi"
__plugin_version__ = "0.1.0"


def __plugin_check__():
	try:
		import OPi.GPIO as GPIO
###		if GPIO.VERSION < "0.6":  # Need at least 0.6 for edge detection ### revisar
###			return False
	except ImportError:
		return False
	return True


def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = Filament_sensor_simplified_OPiPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
		"octoprint.comm.protocol.gcode.received": __plugin_implementation__.gcode_response_received,
		"octoprint.comm.protocol.gcode.sending": __plugin_implementation__.sending_gcode
	}
