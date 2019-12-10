"""Support for Nexia / Trane XL thermostats."""
import datetime
import logging

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.climate import ClimateDevice
from homeassistant.components.climate.const import (
    ATTR_FAN_MODE, ATTR_FAN_MODES, ATTR_HVAC_MODE, ATTR_HVAC_MODES,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP, ATTR_CURRENT_HUMIDITY, ATTR_MIN_HUMIDITY,
    ATTR_MAX_HUMIDITY, ATTR_PRESET_MODE,
    ATTR_HUMIDITY,
    ATTR_MIN_TEMP, ATTR_MAX_TEMP, SUPPORT_TARGET_TEMPERATURE,
    SUPPORT_AUX_HEAT, SUPPORT_PRESET_MODE, SUPPORT_FAN_MODE,
    SUPPORT_TARGET_HUMIDITY,
    ATTR_AUX_HEAT, HVAC_MODE_OFF, HVAC_MODE_AUTO, HVAC_MODE_HEAT_COOL,
    HVAC_MODE_COOL, HVAC_MODE_HEAT,
    CURRENT_HVAC_COOL, CURRENT_HVAC_HEAT, CURRENT_HVAC_IDLE)
from homeassistant.const import (TEMP_CELSIUS, TEMP_FAHRENHEIT,
                                 ATTR_ATTRIBUTION, ATTR_TEMPERATURE,
                                 STATE_OFF, ATTR_ENTITY_ID)
from homeassistant.util import Throttle
from . import (ATTR_MODEL, ATTR_FIRMWARE, ATTR_THERMOSTAT_NAME,
               ATTR_SETPOINT_STATUS,
               ATTR_ZONE_STATUS, ATTR_AIRCLEANER_MODE, ATTR_HUMIDIFY_SUPPORTED,
               ATTR_DEHUMIDIFY_SUPPORTED, ATTR_HUMIDIFY_SETPOINT,
               ATTR_DEHUMIDIFY_SETPOINT, DOMAIN,
               ATTR_THERMOSTAT_ID, ATTR_ZONE_ID, ATTRIBUTION, DATA_NEXIA,
               NEXIA_DEVICE,
               NEXIA_SCAN_INTERVAL, is_percent)

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_AIRCLEANER_MODE = 'set_aircleaner_mode'
SERVICE_SET_HUMIDIFY_SETPOINT = 'set_humidify_setpoint'

SET_FAN_MIN_ON_TIME_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    vol.Required(ATTR_AIRCLEANER_MODE): cv.string,
})

SET_HUMIDITY_SCHEMA = vol.Schema({
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    vol.Required(ATTR_HUMIDITY): is_percent,
})


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up climate zones for a Nexia device."""
    thermostat = hass.data[DATA_NEXIA][NEXIA_DEVICE]
    scan_interval = hass.data[DATA_NEXIA][NEXIA_SCAN_INTERVAL]
    zones = []
    for thermostat_id in thermostat.get_thermostat_ids():

        if thermostat.has_humidify_support(thermostat_id):
            # Add humidify service support
            def humidify_set_service(service):
                entity_id = service.data.get(ATTR_ENTITY_ID)
                humidity = service.data.get(ATTR_HUMIDITY)

                if entity_id:
                    target_zones = [zone for zone in zones if
                                    zone.entity_id in entity_id]
                else:
                    target_zones = zones

                for zone in target_zones:
                    zone.set_humidify_setpoint(humidity)

            hass.services.register(
                DOMAIN, SERVICE_SET_HUMIDIFY_SETPOINT, humidify_set_service(),
                schema=SET_HUMIDITY_SCHEMA)


        for zone_id in thermostat.get_zone_ids(thermostat_id):
            zones.append(
                NexiaZone(thermostat, scan_interval, thermostat_id, zone_id))
    add_entities(zones, True)

    def aircleaner_set_service(service):
        entity_id = service.data.get(ATTR_ENTITY_ID)
        aircleaner_mode = service.data.get(ATTR_AIRCLEANER_MODE)

        if entity_id:
            target_zones = [zone for zone in zones if
                            zone.entity_id in entity_id]
        else:
            target_zones = zones

        for zone in target_zones:
            zone.set_aircleaner_mode(aircleaner_mode)

    hass.services.register(
        DOMAIN, SERVICE_SET_AIRCLEANER_MODE, aircleaner_set_service,
        schema=SET_FAN_MIN_ON_TIME_SCHEMA)




class NexiaZone(ClimateDevice):
    """Provides Nexia Climate support."""

    def __init__(self, device, scan_interval, thermostat_id, zone):
        """Initialize the thermostat."""
        self._device = device
        self._thermostat_id = thermostat_id
        self._zone = zone
        self._scan_interval = scan_interval
        self.update = Throttle(scan_interval)(self._update)

    @property
    def supported_features(self):
        """Return the list of supported features."""
        supported = (SUPPORT_TARGET_TEMPERATURE |
                     SUPPORT_FAN_MODE | SUPPORT_PRESET_MODE)

        if self._device.has_relative_humidity(self._thermostat_id):
            supported |= SUPPORT_TARGET_HUMIDITY

        if self._device.has_emergency_heat(self._thermostat_id):
            supported |= SUPPORT_AUX_HEAT

        return supported

    @property
    def is_fan_on(self):
        """Return true if fan is on."""
        return self._device.is_blower_active(self._thermostat_id)

    @property
    def name(self):
        """ Returns the zone name. """
        if self._device.has_zones(self._thermostat_id):
            return self._device.get_zone_name(self._thermostat_id, self._zone)
        else:
            return self._device.get_thermostat_name(self._thermostat_id)

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return TEMP_CELSIUS if self._device.get_unit(
            self._thermostat_id) == 'C' else TEMP_FAHRENHEIT

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._device.get_zone_temperature(self._thermostat_id,
                                                 self._zone)

    @property
    def fan_mode(self):
        """Return the fan setting."""
        return self._device.get_fan_mode(self._thermostat_id)

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        return self._device.FAN_MODES

    def set_fan_mode(self, fan_mode):
        """Set new target fan mode."""
        self._device.set_fan_mode(fan_mode, self._thermostat_id)

    def set_hold_mode(self, hold_mode):
        """Set new target hold mode."""
        if hold_mode.lower() == "none":
            self._device.call_return_to_schedule(self._thermostat_id,
                                                 self._zone)
        else:
            self._device.set_zone_preset(hold_mode, self._thermostat_id,
                                         self._zone)

    @property
    def preset_mode(self):
        """Returns the current preset."""
        return self._device.get_zone_preset(self._thermostat_id, self._zone)

    @property
    def preset_modes(self):
        """Returns all presets."""
        return self._device.get_zone_presets(self._thermostat_id, self._zone)

    def set_humidity(self, humidity):
        """ Sets the dehumidify target """
        self._device.set_dehumidify_setpoint(humidity / 100.0,
                                             self._thermostat_id)

    @property
    def current_humidity(self):
        """Return the current humidity."""
        if self._device.has_relative_humidity(self._thermostat_id):
            return round(self._device.get_relative_humidity(
                self._thermostat_id) * 100.0, 1)
        return "Not supported"

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self._device.get_zone_current_mode(self._thermostat_id,
                                              self._zone) == 'COOL':
            return self._device.get_zone_cooling_setpoint(self._thermostat_id,
                                                          self._zone)
        return self._device.get_zone_heating_setpoint(self._thermostat_id,
                                                      self._zone)



    @property
    def hvac_action(self) -> str:
        """Return current operation ie. heat, cool, idle."""
        system_status = self._device.get_system_status(self._thermostat_id)
        zone_called = self._device.is_zone_calling(self._thermostat_id,
                                                   self._zone)

        if self._device.get_zone_requested_mode(self._thermostat_id,
                                                self._zone) == \
                self._device.OPERATION_MODE_OFF:
            return STATE_OFF
        if not zone_called:
            return CURRENT_HVAC_IDLE
        if system_status == self._device.SYSTEM_STATUS_COOL:
            return CURRENT_HVAC_COOL
        if system_status == self._device.SYSTEM_STATUS_HEAT:
            return CURRENT_HVAC_HEAT
        if system_status == self._device.SYSTEM_STATUS_IDLE:
            return CURRENT_HVAC_IDLE
        return "idle"

    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle."""
        return self.mode

    @property
    def hvac_modes(self):
        """Returns a list of HVAC available modes"""
        return [HVAC_MODE_OFF, HVAC_MODE_AUTO, HVAC_MODE_HEAT_COOL,
                HVAC_MODE_HEAT, HVAC_MODE_COOL]

    @property
    def mode(self):
        """Return current mode, as the user-visible name."""

        mode = self._device.get_zone_requested_mode(self._thermostat_id,
                                                    self._zone)

        hold = self._device.is_zone_in_permanent_hold(self._thermostat_id,
                                                      self._zone)

        if mode == self._device.OPERATION_MODE_OFF:
            return HVAC_MODE_OFF
        if not hold and mode == self._device.OPERATION_MODE_AUTO:
            return HVAC_MODE_AUTO
        if mode == self._device.OPERATION_MODE_AUTO:
            return HVAC_MODE_HEAT_COOL
        if mode == self._device.OPERATION_MODE_HEAT:
            return HVAC_MODE_HEAT
        if mode == self._device.OPERATION_MODE_COOL:
            return  HVAC_MODE_COOL
        raise KeyError(f"Unhandled mode: {mode}")


    def set_temperature(self, **kwargs):
        """Set target temperature."""
        new_heat_temp = kwargs.get(ATTR_TARGET_TEMP_LOW, None)
        new_cool_temp = kwargs.get(ATTR_TARGET_TEMP_HIGH, None)
        set_temp = kwargs.get(ATTR_TEMPERATURE, None)

        deadband = self._device.get_deadband(self._thermostat_id)
        cur_cool_temp = self._device.get_zone_cooling_setpoint(
            self._thermostat_id, self._zone)
        cur_heat_temp = self._device.get_zone_heating_setpoint(
            self._thermostat_id, self._zone)
        (min_temp, max_temp) = self._device.get_setpoint_limits(
            self._thermostat_id)

        # Check that we're not going to hit any minimum or maximum values
        if new_heat_temp and new_heat_temp + deadband > max_temp:
            new_heat_temp = max_temp - deadband
        if new_cool_temp and new_cool_temp - deadband < min_temp:
            new_cool_temp = min_temp + deadband

        # Check that we're within the deadband range, fix it if we're not
        if new_heat_temp and new_heat_temp != cur_heat_temp:
            if new_cool_temp - new_heat_temp < deadband:
                new_cool_temp = new_heat_temp + deadband
        if new_cool_temp and new_cool_temp != cur_cool_temp:
            if new_cool_temp - new_heat_temp < deadband:
                new_heat_temp = new_cool_temp - deadband

        self._device.set_zone_heat_cool_temp(heat_temperature=new_heat_temp,
                                             cool_temperature=new_cool_temp,
                                             set_temperature=set_temp,
                                             thermostat_id=self._thermostat_id,
                                             zone_id=self._zone)

    @property
    def is_aux_heat(self):
        return "on" if self._device.is_emergency_heat_active(
            self._thermostat_id) else "off"

    @property
    def device_state_attributes(self):
        """Return the device specific state attributes."""

        (min_temp, max_temp) = self._device.get_setpoint_limits(
            self._thermostat_id)
        data = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            ATTR_FAN_MODE: self._device.get_fan_mode(self._thermostat_id),
            ATTR_HVAC_MODE: self.mode,
            ATTR_TARGET_TEMP_HIGH: self._device.get_zone_cooling_setpoint(
                self._thermostat_id,
                self._zone),
            ATTR_TARGET_TEMP_LOW: self._device.get_zone_heating_setpoint(
                self._thermostat_id,
                self._zone),
            ATTR_TARGET_TEMP_STEP: 1.0 if self._device.get_unit(
                self._thermostat_id) == self._device.UNIT_FAHRENHEIT else 0.5,
            ATTR_MIN_TEMP: min_temp,
            ATTR_MAX_TEMP: max_temp,
            ATTR_FAN_MODES: self._device.FAN_MODES,
            ATTR_HVAC_MODES: self.hvac_modes,
            ATTR_PRESET_MODE: self._device.get_zone_preset(self._thermostat_id,
                                                           self._zone),

            # ATTR_PRESET_MODE: self._device.get_zone_setpoint_status(
            #     self._thermostat_id, self._zone),
            # TODO - Enable HOLD_MODES once the presets can be parsed reliably
            # ATTR_HOLD_MODES: self._device.get_zone_presets(
            # self._thermostat_id, self._zone),
            ATTR_MODEL: self._device.get_thermostat_model(self._thermostat_id),
            ATTR_FIRMWARE: self._device.get_thermostat_firmware(
                self._thermostat_id),
            ATTR_THERMOSTAT_NAME: self._device.get_thermostat_name(
                self._thermostat_id),
            ATTR_SETPOINT_STATUS: self._device.get_zone_setpoint_status(
                self._thermostat_id,
                self._zone),
            ATTR_ZONE_STATUS: self._device.get_zone_status(self._thermostat_id,
                                                           self._zone),
            ATTR_THERMOSTAT_ID: self._thermostat_id,
            ATTR_ZONE_ID: self._zone
        }

        if self._device.has_emergency_heat(self._thermostat_id):
            data.update(
                {ATTR_AUX_HEAT: "on" if self._device.is_emergency_heat_active(
                    self._thermostat_id) else "off"})

        if self._device.has_relative_humidity(self._thermostat_id):
            data.update({
                ATTR_HUMIDIFY_SUPPORTED: self._device.has_humidify_support(
                    self._thermostat_id),
                ATTR_DEHUMIDIFY_SUPPORTED: self._device.has_dehumidify_support(
                    self._thermostat_id),
                ATTR_CURRENT_HUMIDITY: round(
                    self._device.get_relative_humidity(
                        self._thermostat_id) * 100.0, 1),
                ATTR_MIN_HUMIDITY: round(
                    self._device.get_humidity_setpoint_limits(
                        self._thermostat_id)[0] * 100.0, 1),
                ATTR_MAX_HUMIDITY: round(
                    self._device.get_humidity_setpoint_limits(
                        self._thermostat_id)[1] * 100.0, 1),
            })
            if self._device.has_dehumidify_support(self._thermostat_id):
                data.update({
                    ATTR_DEHUMIDIFY_SETPOINT: round(
                        self._device.get_dehumidify_setpoint(
                            self._thermostat_id) * 100.0, 1),
                    ATTR_HUMIDITY: round(
                        self._device.get_dehumidify_setpoint(
                            self._thermostat_id) * 100.0, 1)})
            if self._device.has_humidify_support(self._thermostat_id):
                data.update({
                    ATTR_HUMIDIFY_SETPOINT: round(
                        self._device.get_humidify_setpoint(
                            self._thermostat_id) * 100.0, 1)})
        return data

    def set_preset_mode(self, preset_mode: str):
        """Set the preset mode."""
        self._device.set_zone_preset(preset_mode,
                                     self._thermostat_id,
                                     self._zone)

    def turn_aux_heat_off(self):
        """Turns Aux Heat off"""
        self._device.set_emergency_heat(False, self._thermostat_id)

    def turn_aux_heat_on(self):
        """Turns Aux Heat on"""
        self._device.set_emergency_heat(True, self._thermostat_id)

    def turn_off(self):
        """Turns off the zone"""
        self.set_hvac_mode(self._device.OPERATION_MODE_OFF)

    def turn_on(self):
        """Turns on the zone"""
        self.set_hvac_mode(self._device.OPERATION_MODE_AUTO)

    def set_swing_mode(self, swing_mode):
        """Unsupported - Swing Mode"""
        raise NotImplementedError(
            "set_swing_mode is not supported by this device")

    def set_hvac_mode(self, hvac_mode: str) -> None:
        """Set the system mode (Auto, Heat_Cool, Cool, Heat, etc)."""

        if hvac_mode == HVAC_MODE_AUTO:
            self._device.call_return_to_schedule(thermostat_id=self._thermostat_id,
                                                 zone_id=self._zone)
            self._device.set_zone_mode(mode=self._device.OPERATION_MODE_AUTO,
                                       thermostat_id=self._thermostat_id,
                                       zone_id=self._zone)
        else:
            if hvac_mode == HVAC_MODE_HEAT_COOL:
                hvac_mode = HVAC_MODE_AUTO
            self._device.call_permanent_hold(thermostat_id=self._thermostat_id,
                                             zone_id=self._zone)

            hvac_mode = hvac_mode.upper()

            if hvac_mode in self._device.OPERATION_MODES:

                self._device.set_zone_mode(
                    mode=hvac_mode,
                    thermostat_id=self._thermostat_id,
                    zone_id=self._zone)
            else:
                raise KeyError(
                    f"Operation mode {hvac_mode} not in the supported " +
                    f"operations list {str(self._device.OPERATION_MODES)}")

    def set_aircleaner_mode(self, aircleaner_mode):
        """ Sets the aircleaner mode """
        self._device.set_air_cleaner(aircleaner_mode, self._thermostat_id)

    def set_humidify_setpoint(self, humidify_setpoint):
        """ Sets the humidify setpoint """
        self._device.set_humdify_setpoint(humidify_setpoint / 100.0, self._thermostat_id)

    def _update(self):
        """Update the state."""
        if self._device.last_update is None or \
                datetime.datetime.now() - self._device.last_update > \
                self._scan_interval:
            self._device.update()
