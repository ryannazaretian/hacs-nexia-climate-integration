""" Nexia Climate Device Access """

import datetime
import json
import logging
import math
import pprint
from threading import Lock

import requests

GLOBAL_LOGIN_ATTEMPTS = 4
GLOBAL_LOGIN_ATTEMPTS_LEFT = GLOBAL_LOGIN_ATTEMPTS
TIMEOUT = 20


_LOGGER = logging.getLogger(__name__)


def is_number(string):
    """String is a number."""
    try:
        float(string)
        return True
    except ValueError:
        return False


class NexiaThermostat:
    """ Nexia Climate Device Access Class """

    ROOT_URL = "https://www.mynexia.com"
    MOBILE_URL = f"{ROOT_URL}/mobile"
    AUTH_FAILED_STRING = "https://www.mynexia.com/login"
    AUTH_FORGOTTEN_PASSWORD_STRING = (
        "https://www.mynexia.com/account/" "forgotten_credentials"
    )
    DEFAULT_UPDATE_RATE = 120  # 2 minutes
    DISABLE_AUTO_UPDATE = "Disable"
    PUT_UPDATE_DELAY = 0.5

    HOLD_PERMANENT = "permanent_hold"
    HOLD_RESUME_SCHEDULE = "run_schedule"

    FAN_MODE_AUTO = "auto"
    FAN_MODE_ON = "on"
    FAN_MODE_CIRCULATE = "circulate"
    FAN_MODES = [FAN_MODE_AUTO, FAN_MODE_ON, FAN_MODE_CIRCULATE]

    OPERATION_MODE_AUTO = "AUTO"
    OPERATION_MODE_COOL = "COOL"
    OPERATION_MODE_HEAT = "HEAT"
    OPERATION_MODE_OFF = "OFF"
    OPERATION_MODES = [
        OPERATION_MODE_AUTO,
        OPERATION_MODE_COOL,
        OPERATION_MODE_HEAT,
        OPERATION_MODE_OFF,
    ]

    # The order of these is important as it maps to preset#
    PRESET_MODE_HOME = "Home"
    PRESET_MODE_AWAY = "Away"
    PRESET_MODE_SLEEP = "Sleep"
    PRESET_MODE_NONE = "None"

    SYSTEM_STATUS_COOL = "Cooling"
    SYSTEM_STATUS_HEAT = "Heating"
    SYSTEM_STATUS_WAIT = "Waiting..."
    SYSTEM_STATUS_IDLE = "System Idle"

    AIR_CLEANER_MODE_AUTO = "auto"
    AIR_CLEANER_MODE_QUICK = "quick"
    AIR_CLEANER_MODE_ALLERGY = "allergy"
    AIR_CLEANER_MODES = [
        AIR_CLEANER_MODE_AUTO,
        AIR_CLEANER_MODE_QUICK,
        AIR_CLEANER_MODE_ALLERGY,
    ]

    HUMIDITY_MIN = 0.35
    HUMIDITY_MAX = 0.65

    UNIT_CELSIUS = "C"
    UNIT_FAHRENHEIT = "F"

    ALL_IDS = "all"

    def __init__(
        self,
        house_id=None,
        username=None,
        password=None,
        auto_login=True,
        update_rate=None,
        offline_json=None,
    ):
        """
        Connects to and provides the ability to get and set parameters of your
        Nexia connected thermostat.

        :param house_id: int - Your house_id. You can get this from logging in
        and looking at the url once you're looking at your climate device.
        https://www.mynexia.com/houses/<house_id>/climate
        :param username: str - Your login email address
        :param password: str - Your login password
        :param auto_login: bool - Default is True, Login now (True), or login
        manually later (False)
        :param update_rate: int - How many seconds between requesting a new
        JSON update. Default is 300s.
        """

        self.username = username
        self.password = password
        self.house_id = house_id
        self.mobile_id = None
        self.api_key = None
        self.thermostat_json = None
        self.last_update = None
        self.mutex = Lock()

        self.offline_json = offline_json

        # Control the update rate
        if update_rate is None:
            self.update_rate = datetime.timedelta(seconds=self.DEFAULT_UPDATE_RATE)
        elif update_rate == self.DISABLE_AUTO_UPDATE:
            self.update_rate = self.DISABLE_AUTO_UPDATE
        else:
            self.update_rate = datetime.timedelta(seconds=update_rate)

        if not self.offline_json:
            # Create a session
            self.session = requests.session()
            self.session.max_redirects = 3

            # Login if requested
            if auto_login:
                self.login()
                self.update()

    def _api_key_headers(self):
        return {"X-MobileId": str(self.mobile_id), "X-ApiKey": str(self.api_key)}

    def _post_url(self, url: str, payload: dict):
        """
        Posts data to the session from the url and payload
        :param url: str
        :param payload: dict
        :return: response
        """

        if self.offline_json:
            print(f"PUT:\n" f"  URL: {url}\n" f"  Data: {pprint.pformat(payload)}")
            return None

        request_url = f"{self.MOBILE_URL}{url}"
        _LOGGER.debug("POST: Calling url %s with payload: %s", request_url, payload)

        request = self.session.post(
            request_url, payload, timeout=TIMEOUT, headers=self._api_key_headers()
        )

        if request.status_code == 302:
            # assuming its redirecting to login
            self.login()
            return self._post_url(url, payload)

        _LOGGER.debug("POST: Response from url %s: %s", request_url, request.content)
        # no need to sleep anymore as we consume the response and update the thermostat's JSON

        self._check_response("Failed to POST url", request)
        return request

    def _get_url(self, url):
        """
        Returns the full session.get from the URL (ROOT_URL + url)
        :param url: str
        :return: response
        """
        request_url = f"{self.MOBILE_URL}{url}"

        _LOGGER.debug("GET: Calling url %s", request_url)
        request = self.session.get(
            request_url,
            allow_redirects=False,
            timeout=TIMEOUT,
            headers=self._api_key_headers(),
        )
        # _LOGGER.debug(f"GET: RESPONSE {request_url}: request.content {request.content}")

        if request.status_code == 302:
            # assuming its redirecting to login
            self.login()
            return self._get_url(url)

        self._check_response("Failed to GET url", request)
        return request

    @staticmethod
    def _check_response(error_text, request):
        """
        Checks the request response, throws exception with the description text
        :param error_text: str
        :param request: response
        :return: None
        """
        if request is None or request.status_code != 200:
            if request is not None:
                response = ""
                for key in request.__attrs__:
                    response += f"  {key}: {getattr(request, key)}\n"
                raise Exception(f"{error_text}\n{response}")
            raise Exception(f"No response from session. {error_text}")

    def _needs_update(self):
        """
        Returns True if an update is needed
        :return: bool
        """
        if self.update_rate == self.DISABLE_AUTO_UPDATE:
            return False
        if self.last_update is None:
            return True
        return datetime.datetime.now() - self.last_update > self.update_rate

    def _post_and_update_thermostat_json(self, thermostat_id, url, data):
        response = self._post_url(url, data)
        self._update_thermostat_json(thermostat_id, response.json()["result"])

    def _update_thermostat_json(self, thermostat_id, data):
        if self.thermostat_json is None:
            return

        for thermostat in self.thermostat_json:
            if thermostat["id"] == thermostat_id:
                _LOGGER.debug(
                    f"Updated thermostat_id:{thermostat_id} with new data from post"
                )
                thermostat.update(data)

    def _post_and_update_zone_json(self, thermostat_id, zone, url, data):
        response = self._post_url(url, data)
        self._update_zone_json(thermostat_id, zone, response.json()["result"])

    def _update_zone_json(self, thermostat_id, zone_id, data):
        if self.thermostat_json is None:
            return

        for thermostat in self.thermostat_json:
            if thermostat["id"] == thermostat_id:
                for zone in thermostat["zones"]:
                    if zone["id"] == zone_id:
                        _LOGGER.debug(
                            f"Updated thermostat_id:{thermostat_id} zone_id:{zone_id} with new data from post"
                        )
                        zone.update(data)

    def _find_house_id(self):
        """
        Finds the house id if none is provided
        """
        request = self._post_url("/session", {})
        if request and request.status_code == 200:
            ts_json = request.json()
            if ts_json:
                self.house_id = ts_json["result"]["_links"]["child"][0]["data"]["id"]
            else:
                raise Exception("Nothing in the JSON")
        else:
            self._check_response(
                "Failed to get house id JSON, session probably timed" " out", request,
            )

    def _get_thermostat_json(self, thermostat_id=None, force_update=False):
        """
        Returns the thermostat's JSON data. It's either cached, or returned
        directly from the internet
        :param force_update: bool - Forces an update
        :return: dict(thermostat_jason)
        """
        if not self.mobile_id:
            # not yet authenticated
            return None

        if self.thermostat_json is None or self._needs_update() or force_update is True:
            with self.mutex:
                # Now that we have the mutex we check again
                # to make an update did not happen elsewhere
                if (
                    self.thermostat_json is None
                    or self._needs_update()
                    or force_update is True
                ):
                    request = self._get_url("/houses/" + str(self.house_id))
                    if request and request.status_code == 200:
                        ts_json = request.json()
                        if ts_json:
                            self.thermostat_json = ts_json["result"]["_links"]["child"][
                                0
                            ]["data"]["items"]
                            self.last_update = datetime.datetime.now()
                        else:
                            raise Exception("Nothing in the JSON")
                    else:
                        self._check_response(
                            "Failed to get thermostat JSON, session probably timed"
                            " out",
                            request,
                        )

        # _LOGGER.debug(f"self.thermostat_json: {self.thermostat_json}")
        if thermostat_id == self.ALL_IDS:
            return self.thermostat_json
        if thermostat_id is not None:
            thermostat_ids = []
            for thermostat in self.thermostat_json:
                thermostat_ids.append(thermostat["id"])
                if thermostat["id"] == thermostat_id:
                    return thermostat
            raise KeyError(
                f"Thermostat ID {thermostat_id} does not exist. Available IDs:"
                f" {thermostat_ids}"
            )
        if len(self.thermostat_json) == 1:
            return self.thermostat_json[0]

        raise IndexError(
            "More than one thermostat detected. You must provide a " "thermostat_id"
        )

    def _get_thermostat_deep_key(self, area, area_primary_key, key, thermostat_id=None):
        """
        Returns the thermostat value from deep inside the thermostat's
        JSON.
        :param area: The area of the json to look i.e. "settings", "features", etc
        :param area_primary_key: The name of the primary key such as "name" or "key"
        :param thermostat_id: int - the ID of the thermostat to use
        :param key: str
        :return: value
        """
        data = find_dict_with_keyvalue_in_json(
            self._get_thermostat_json(thermostat_id)[area], area_primary_key, key
        )

        if not data:
            raise KeyError(f'Key "{key}" not in the thermostat JSON!')
        return data

    def _get_thermostat_features_key_or_none(self, key, thermostat_id=None):
        """
        Returns the thermostat value from the provided key in the thermostat's
        JSON.
        :param thermostat_id: int - the ID of the thermostat to use
        :param key: str
        :return: value
        """
        try:
            return self._get_thermostat_features_key(key, thermostat_id)
        except KeyError:
            return None

    def _get_thermostat_features_key(self, key, thermostat_id=None):
        """
        Returns the thermostat value from the provided key in the thermostat's
        JSON.
        :param thermostat_id: int - the ID of the thermostat to use
        :param key: str
        :return: value
        """
        return self._get_thermostat_deep_key("features", "name", key, thermostat_id)

    def _get_thermostat_key_or_none(self, key, thermostat_id=None):
        """
        Returns the thermostat value from the provided key in the thermostat's
        JSON.
        :param thermostat_id: int - the ID of the thermostat to use
        :param key: str
        :return: value
        """
        try:
            return self._get_thermostat_key(key, thermostat_id)
        except KeyError:
            return None

    def _get_thermostat_key(self, key, thermostat_id=None):
        """
        Returns the thermostat value from the provided key in the thermostat's
        JSON.
        :param thermostat_id: int - the ID of the thermostat to use
        :param key: str
        :return: value
        """
        thermostat = self._get_thermostat_json(thermostat_id)
        if key in thermostat:
            return thermostat[key]
        raise KeyError(f'Key "{key}" not in the thermostat JSON!')

    def _get_thermostat_settings_key_or_none(self, key, thermostat_id=None):
        """
        Returns the thermostat value from the provided key in the thermostat's
        JSON.
        :param thermostat_id: int - the ID of the thermostat to use
        :param key: str
        :return: value
        """
        try:
            return self._get_thermostat_settings_key(key, thermostat_id)
        except KeyError:
            return None

    def _get_thermostat_settings_key(self, key, thermostat_id=None):
        """
        Returns the thermostat value from the provided key in the thermostat's
        JSON.
        :param thermostat_id: int - the ID of the thermostat to use
        :param key: str
        :return: value
        """
        return self._get_thermostat_deep_key("settings", "type", key, thermostat_id)

    def _get_zone_json(self, thermostat_id=None, zone_id=0):
        """
        Returns the thermostat zone's JSON
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: dict(thermostat_json['zones'][zone_id])
        """
        thermostat = self._get_thermostat_json(thermostat_id)
        if not thermostat:
            return None

        zone = find_dict_with_keyvalue_in_json(thermostat["zones"], "id", zone_id)

        if not zone:
            raise IndexError(
                f"The zone_id ({zone_id}) does not exist in the thermostat zones."
            )
        return zone

    def _get_zone_setting(self, key, thermostat_id=None, zone_id=0):
        """
        Returns the zone value for the key and zone_id provided.
        :param key: str
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: The value of the key/value pair.
        """
        zone = self._get_zone_json(thermostat_id, zone_id)
        subdict = find_dict_with_keyvalue_in_json(zone["settings"], "type", key)
        if not subdict:
            raise KeyError(f'Zone {zone_id} settings key "{key}" invalid.')
        return subdict

    def _get_zone_features(self, key, thermostat_id=None, zone_id=0):
        """
        Returns the zone value for the key and zone_id provided.
        :param key: str
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: The value of the key/value pair.
        """
        zone = self._get_zone_json(thermostat_id, zone_id)
        subdict = find_dict_with_keyvalue_in_json(zone["features"], "name", key)
        if not subdict:
            raise KeyError(f'Zone {zone_id} feature key "{key}" invalid.')
        return subdict

    def _get_zone_key(self, key, thermostat_id=None, zone_id=0):
        """
        Returns the zone value for the key and zone_id provided.
        :param key: str
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: The value of the key/value pair.
        """
        zone = self._get_zone_json(thermostat_id, zone_id)
        if key in zone:
            return zone[key]

        raise KeyError(f'Zone {zone_id} key "{key}" invalid.')

    ########################################################################
    # Session Methods

    def login(self):
        """
        Provides you with a Nexia web session.

        All parameters should be set prior to calling this.
        - username - (str) Your email address
        - password - (str) Your login password
        - house_id - (int) Your house id
        :return: None
        """
        global GLOBAL_LOGIN_ATTEMPTS, GLOBAL_LOGIN_ATTEMPTS_LEFT
        if GLOBAL_LOGIN_ATTEMPTS_LEFT > 0:
            payload = {
                "login": self.username,
                "password": self.password,
            }
            request = self._post_url("/accounts/sign_in", payload)

            if (
                request is None
                or request.status_code != 200
                and request.status_code != 302
            ):
                GLOBAL_LOGIN_ATTEMPTS_LEFT -= 1
            self._check_response("Failed to login", request)

            if request.url == self.AUTH_FORGOTTEN_PASSWORD_STRING:
                raise Exception(
                    f"Failed to login, getting redirected to {request.url}"
                    f". Try to login manually on the website."
                )

            json_dict = request.json()
            if json_dict.get("success") is not True:
                error_text = json_dict.get("error", "Unknown Error")
                raise Exception(f"Failed to login, {error_text}")

            self.mobile_id = json_dict["result"]["mobile_id"]
            self.api_key = json_dict["result"]["api_key"]
        else:
            raise Exception(
                f"Failed to login after {GLOBAL_LOGIN_ATTEMPTS} attempts! Any "
                f"more attempts may lock your account!"
            )

        if not self.house_id:
            self._find_house_id()

    def get_last_update(self):
        """
        Returns a string indicating the ISO formatted time string of the last
        update
        :return: The ISO formatted time string of the last update,
        datetime.datetime.min if never updated
        """
        if self.last_update is None:
            return datetime.datetime.isoformat(datetime.datetime.min)
        return datetime.datetime.isoformat(self.last_update)

    def update(self):
        """
        Forces a status update
        :return: None
        """
        self._get_thermostat_json(thermostat_id=self.ALL_IDS, force_update=True)

    ########################################################################
    # Print Functions

    def print_thermostat_data(self, thermostat_id=None):
        """
        Prints just the thermostat data, no zone data
        :param thermostat_id: int - the ID of the thermostat to use
        :return: None
        """
        thermostat_json = self._get_thermostat_json(thermostat_id).copy()
        thermostat_json.pop("zones")
        pprint.pprint(thermostat_json)

    def print_zone_data(self, thermostat_id=None, zone_id=None):
        """
        Prints the specified zone data
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: None
        """
        thermostat_json = self._get_zone_json(thermostat_id, zone_id)
        pprint.pprint(thermostat_json)

    def print_all_json_data(self):
        """
        Prints all thermostat data
        :return: None
        """
        thermostat_json = self._get_thermostat_json(self.ALL_IDS)
        pprint.pprint(thermostat_json)

    ########################################################################
    # Thermostat Attributes

    def get_thermostat_ids(self):
        """
        Returns the number of thermostats available to Nexia
        :return:
        """
        ids = list()
        for thermostat in self._get_thermostat_json(thermostat_id=self.ALL_IDS):
            ids.append(thermostat["id"])
        return ids

    def _get_thermostat_advanced_info_label(self, thermostat_id, label):
        """
        Lookup advanced_info in the thermostat features and find the value of the
        requested label.
        """
        advanced_info = self._get_thermostat_features_key(
            "advanced_info", thermostat_id
        )
        return find_dict_with_keyvalue_in_json(advanced_info["items"], "label", label)[
            "value"
        ]

    def get_thermostat_model(self, thermostat_id=None):
        """
        Returns the thermostat model
        :param thermostat_id: int - the ID of the thermostat to use
        :return: string
        """
        return self._get_thermostat_advanced_info_label(thermostat_id, "Model")

    def get_thermostat_firmware(self, thermostat_id=None):
        """
        Returns the thermostat firmware version
        :param thermostat_id: int - the ID of the thermostat to use
        :return: string
        """
        return self._get_thermostat_advanced_info_label(
            thermostat_id, "Firmware Version"
        )

    def get_thermostat_dev_build_number(self, thermostat_id=None):
        """
        Returns the thermostat development build number.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: string
        """
        return self._get_thermostat_advanced_info_label(
            thermostat_id, "Firmware Build Number"
        )

    def get_thermostat_device_id(self, thermostat_id=None):
        """
        Returns the device id
        :param thermostat_id: int - the ID of the thermostat to use
        :return: string
        """
        return self._get_thermostat_advanced_info_label(thermostat_id, "AUID")

    def get_thermostat_type(self, thermostat_id=None):
        """
        Returns the thermostat type, such as TraneXl1050
        :param thermostat_id: int - the ID of the thermostat to use
        :return: str
        """
        return self.get_thermostat_model(thermostat_id)

    def get_thermostat_name(self, thermostat_id=None):
        """
        Returns the name of the thermostat. This is not the zone name.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: str
        """
        return self._get_thermostat_key("name", thermostat_id)

    ########################################################################
    # Supported Features

    def has_outdoor_temperature(self, thermostat_id=None):
        """
        Capability indication of whether the thermostat has an outdoor
        temperature sensor
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        return self._get_thermostat_key_or_none(
            "has_outdoor_temperature", thermostat_id
        )

    def has_relative_humidity(self, thermostat_id=None):
        """
        Capability indication of whether the thermostat has an relative
        humidity sensor
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        return bool(self._get_thermostat_key_or_none("indoor_humidity", thermostat_id))

    def has_variable_speed_compressor(self, thermostat_id=None):
        """
        Capability indication of whether the thermostat has a variable speed
        compressor
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        # This only shows up if its running on mobile
        return True

    def has_emergency_heat(self, thermostat_id=None):
        """
        Capability indication of whether the thermostat has emergency/aux heat.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        return bool(
            self._get_thermostat_key_or_none("emergency_heat_supported", thermostat_id)
        )

    def has_variable_fan_speed(self, thermostat_id=None):
        """
        Capability indication of whether the thermostat has a variable speed
        blower
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        return bool(
            self._get_thermostat_settings_key_or_none("fan_speed", thermostat_id)
        )

    def has_zones(self, thermostat_id=None):
        """
        Indication of whether zoning is enabled or not on the thermostat.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        return bool(self._get_thermostat_key_or_none("zones", thermostat_id))

    def has_dehumidify_support(self, thermostat_id=None):
        """
        Indiciation of whether dehumidifying support is available.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        return bool(
            self._get_thermostat_settings_key_or_none("dehumidify", thermostat_id)
        )

    def has_humidify_support(self, thermostat_id=None):
        """
        Indiciation of whether humidifying support is available.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        return bool(
            self._get_thermostat_settings_key_or_none("humidify", thermostat_id)
        )

    ########################################################################
    # System Attributes

    def get_deadband(self, thermostat_id=None):
        """
        Returns the deadband of the thermostat. This is the minimum number of
        degrees between the heat and cool setpoints in the number of degrees in
        the temperature unit selected by the
        thermostat.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: int
        """
        return self._get_thermostat_features_key("thermostat", thermostat_id)[
            "setpoint_delta"
        ]

    def get_setpoint_limits(self, thermostat_id=None):
        """
        Returns a tuple of the minimum and maximum temperature that can be set
        on any zone. This is in the temperature unit selected by the
        thermostat.
        :return: (int, int)
        """
        return (
            self._get_thermostat_features_key("thermostat", thermostat_id)[
                "setpoint_heat_min"
            ],
            self._get_thermostat_features_key("thermostat", thermostat_id)[
                "setpoint_cool_max"
            ],
        )

    def get_variable_fan_speed_limits(self, thermostat_id=None):
        """
        Returns the variable fan speed setpoint limits of the thermostat.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: (float, float)
        """
        if self.has_variable_fan_speed(thermostat_id):
            possible_values = self._get_thermostat_settings_key(
                "fan_speed", thermostat_id
            )["values"]
            return (possible_values[0], possible_values[-1])
        raise AttributeError("This thermostat does not support fan speeds")

    def get_unit(self, thermostat_id=None):
        """
        Returns the temperature unit used by this system, either C or F.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: str
        """
        return self._get_thermostat_features_key("thermostat", thermostat_id)[
            "scale"
        ].upper()

    def get_humidity_setpoint_limits(self, thermostat_id=None):
        """
        Returns the humidity setpoint limits of the thermostat.

        This is a hard-set limit in this code that I believe is universal to
        all TraneXl thermostats.
        :param thermostat_id: int - the ID of the thermostat to use (unused,
        but kept for consistency)
        :return: (float, float)
        """
        return self.HUMIDITY_MIN, self.HUMIDITY_MAX

    ########################################################################
    # System Universal Boolean Get Methods

    def is_blower_active(self, thermostat_id=None):
        """
        Returns True if the blower is active
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        system_status = self._get_thermostat_key("system_status", thermostat_id)
        return not system_status in (self.SYSTEM_STATUS_WAIT, self.SYSTEM_STATUS_IDLE)

    def is_emergency_heat_active(self, thermostat_id=None):
        """
        Returns True if the emergency/aux heat is active
        :param thermostat_id: int - the ID of the thermostat to use
        :return: bool
        """
        if self.has_emergency_heat(thermostat_id=thermostat_id):
            return self._get_thermostat_settings_key(
                "emergency_heat_active", thermostat_id
            )
        raise Exception("This system does not support emergency heat")

    ########################################################################
    # System Universal Get Methods

    def get_fan_mode(self, thermostat_id=None):
        """
        Returns the current fan mode. See FAN_MODES for the available options.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: str
        """
        return self._get_thermostat_settings_key("fan_mode", thermostat_id)[
            "current_value"
        ]

    def get_outdoor_temperature(self, thermostat_id=None):
        """
        Returns the outdoor temperature.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: float - the temperature, returns nan if invalid
        """
        if self.has_outdoor_temperature(thermostat_id):
            outdoor_temp = self._get_thermostat_key(
                "outdoor_temperature", thermostat_id
            )
            if is_number(outdoor_temp):
                return float(outdoor_temp)
            return float("Nan")
        raise Exception("This system does not have an outdoor temperature sensor")

    def get_relative_humidity(self, thermostat_id=None):
        """
        Returns the indoor relative humidity as a percent (0-1)
        :param thermostat_id: int - the ID of the thermostat to use
        :return: float
        """
        if self.has_relative_humidity(thermostat_id):
            return (
                float(self._get_thermostat_key("indoor_humidity", thermostat_id)) / 100
            )
        raise Exception("This system does not have a relative humidity sensor.")

    def get_current_compressor_speed(self, thermostat_id=None):
        """
        Returns the variable compressor speed, if supported, as a percent (0-1)
        :param thermostat_id: int - the ID of the thermostat to use
        :return: float
        """
        thermostat_compressor_speed = self._get_thermostat_features_key_or_none(
            "thermostat_compressor_speed", thermostat_id
        )
        if thermostat_compressor_speed is None:
            return 0
        return float(thermostat_compressor_speed["compressor_speed"])

    def get_requested_compressor_speed(self, thermostat_id=None):
        """
        Returns the variable compressor's requested speed, if supported, as a
        percent (0-1)
        :param thermostat_id: int - the ID of the thermostat to use
        :return: float
        """
        # mobile api does not have a requested speed
        return self.get_current_compressor_speed(thermostat_id)

    def get_fan_speed_setpoint(self, thermostat_id=None):
        """
        Returns the current variable fan speed setpoint from 0-1.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: float
        """
        if self.has_variable_fan_speed(thermostat_id):
            return self._get_thermostat_settings_key("fan_speed", thermostat_id)
        raise AttributeError("This system does not have variable fan speed.")

    def get_dehumidify_setpoint(self, thermostat_id=None):
        """
        Returns the dehumidify setpoint from 0-1
        :param thermostat_id: int - the ID of the thermostat to use
        :return: float
        """
        if self.has_dehumidify_support(thermostat_id):
            return self._get_thermostat_settings_key("dehumidify", thermostat_id)[
                "current_value"
            ]
        else:
            raise AttributeError("This system does not support " "dehumidification")

    def get_humidify_setpoint(self, thermostat_id=None):
        """
        Returns the dehumidify setpoint from 0-1
        :param thermostat_id: int - the ID of the thermostat to use
        :return: float
        """
        if self.has_humidify_support(thermostat_id):
            return self._get_thermostat_settings_key("humidify", thermostat_id)[
                "current_value"
            ]
        else:
            raise AttributeError("This system does not support humidification")

    def get_system_status(self, thermostat_id=None):
        """
        Returns the system status such as "System Idle" or "Cooling"
        :param thermostat_id: int - the ID of the thermostat to use
        :return: str
        """
        return self._get_thermostat_key("system_status", thermostat_id)

    def get_air_cleaner_mode(self, thermostat_id=None):
        """
        Returns the system's air cleaner mode
        :param thermostat_id: int - the ID of the thermostat to use
        :return: str
        """
        return self._get_thermostat_settings_key("air_cleaner_mode", thermostat_id)[
            "current_value"
        ]

    ########################################################################
    # System Universal Set Methods

    def set_fan_mode(self, fan_mode: str, thermostat_id=None):
        """
        Sets the fan mode.
        :param fan_mode: string that must be in NexiaThermostat.FAN_MODES
        :param thermostat_id: int - the ID of the thermostat to use
        :return: None
        """
        fan_mode = fan_mode.lower()
        if fan_mode in self.FAN_MODES:
            url = _get_thermostat_post_url("fan_mode", thermostat_id)
            data = {"value": fan_mode}
            self._post_and_update_thermostat_json(thermostat_id, url, data)

        else:
            raise KeyError("Invalid fan mode specified")

    def set_fan_setpoint(self, fan_setpoint: float):
        """
         Sets the fan's setpoint speed as a percent in range. You can see the
         limits by calling Nexia.get_variable_fan_speed_limits()
         :param fan_setpoint: float
         :return: None
         """

        # This call will get the limits, as well as check if this system has
        # a variable speed fan
        min_speed, max_speed = self.get_variable_fan_speed_limits()

        if min_speed <= fan_setpoint <= max_speed:
            url = _get_thermostat_post_url("fan_speed", thermostat_id)
            data = {"value": fan_setpoint}
            self._post_and_update_thermostat_json(thermostat_id, url, data)
        else:
            raise ValueError(
                f"The fan setpoint, {fan_setpoint} is not "
                f"between {min_speed} and {max_speed}."
            )

    def set_air_cleaner(self, air_cleaner_mode: str, thermostat_id):
        """
        Sets the air cleaner mode.
        :param air_cleaner_mode: string that must be in
        NexiaThermostat.AIR_CLEANER_MODES
        :param thermostat_id: int - the ID of the thermostat to use
        :return: None
        """
        air_cleaner_mode = air_cleaner_mode.lower()
        if air_cleaner_mode in self.AIR_CLEANER_MODES:
            if air_cleaner_mode != self.get_air_cleaner_mode(thermostat_id):
                url = _get_thermostat_post_url("air_cleaner_mode", thermostat_id)
                data = {"value": air_cleaner_mode}
                self._post_and_update_thermostat_json(thermostat_id, url, data)
        else:
            raise KeyError("Invalid air cleaner mode specified")

    def set_follow_schedule(self, follow_schedule, thermostat_id):
        """
        Enables or disables scheduled operation
        :param follow_schedule: bool - True for follow schedule, False for hold
        current setpoints
        :param thermostat_id: int - the ID of the thermostat to use
        :return: None
        """
        url = _get_thermostat_post_url("scheduling_enabled", thermostat_id)
        data = {"value": "true" if follow_schedule else "false"}
        self._post_and_update_thermostat_json(thermostat_id, url, data)

    def set_emergency_heat(self, emergency_heat_on, thermostat_id):
        """
        Enables or disables emergency / auxiliary heat.
        :param emergency_heat_on: bool - True for enabled, False for Disabled
        :param thermostat_id: int - the ID of the thermostat to use
        :return: None
        """
        if self.has_emergency_heat(thermostat_id):
            url = _get_thermostat_post_url("emergency_heat", thermostat_id)
            data = {"value": bool(emergency_heat_on)}
            self._post_and_update_thermostat_json(thermostat_id, url, data)
        else:
            raise Exception("This thermostat does not support emergency heat.")

    def set_humidity_setpoints(self, **kwargs):
        """

        :param dehumidify_setpoint: float - The dehumidify_setpoint, 0-1, disable: None
        :param humidify_setpoint: float - The humidify setpoint, 0-1, disable: None
        :param thermostat_id:  int - the ID of the thermostat to use
        :return:
        """

        dehumidify_setpoint = kwargs.get("dehumidify_setpoint", None)
        humidify_setpoint = kwargs.get("humidify_setpoint", None)
        thermostat_id = kwargs.get("thermostat_id", None)

        if dehumidify_setpoint is None and humidify_setpoint is None:
            # Do nothing
            return

        if thermostat_id is None:
            raise TypeError("thermostat_id must be set.")

        if self.has_relative_humidity(thermostat_id):
            (min_humidity, max_humidity) = self.get_humidity_setpoint_limits(
                thermostat_id
            )
            if self.has_humidify_support(thermostat_id):
                humidify_supported = True
                if humidify_setpoint is None:
                    humidify_setpoint = self.get_humidify_setpoint(thermostat_id)
            else:
                if humidify_setpoint is not None:
                    raise SystemError("This thermostat does not support humidifying.")
                humidify_supported = False
                humidify_setpoint = 0

            if self.has_dehumidify_support(thermostat_id):
                dehumidify_supported = True
                if dehumidify_setpoint is None:
                    dehumidify_setpoint = self.get_dehumidify_setpoint(thermostat_id)
            else:
                if dehumidify_setpoint is not None:
                    raise SystemError("This thermostat does not support dehumidifying.")
                dehumidify_supported = False
                dehumidify_setpoint = 0

            # Clean up input
            dehumidify_setpoint = round(0.05 * round(dehumidify_setpoint / 0.05), 2)
            humidify_setpoint = round(0.05 * round(humidify_setpoint / 0.05), 2)

            # Check inputs
            if (dehumidify_supported and humidify_supported) and not (
                min_humidity <= humidify_setpoint <= dehumidify_setpoint <= max_humidity
            ):
                raise ValueError(
                    f"Setpoints must be between ({min_humidity} -"
                    f" {max_humidity}) and humdiify_setpoint must"
                    f" be <= dehumidify_setpoint"
                )
            if (dehumidify_supported) and not (
                min_humidity <= dehumidify_setpoint <= max_humidity
            ):
                raise ValueError(
                    f"dehumidify_setpoint must be between "
                    f"({min_humidity} - {max_humidity})"
                )
            if (humidify_supported) and not (
                min_humidity <= humidify_setpoint <= max_humidity
            ):
                raise ValueError(
                    f"humidify_setpoint must be between "
                    f"({min_humidity} - {max_humidity})"
                )

            url = _get_thermostat_post_url("dehumidify", thermostat_id)
            data = {"value": dehumidify_setpoint}
            self._post_and_update_thermostat_json(thermostat_id, url, data)
        else:
            raise Exception(
                "Setting target humidity is not supported on this thermostat."
            )

    def set_dehumidify_setpoint(self, dehumidify_setpoint, thermostat_id):
        """
        Sets the overall system's dehumidify setpoint as a percent (0-1).

        The system must support
        :param dehumidify_setpoint: float
        :param thermostat_id: int - the ID of the thermostat to use
        :return: None
        """
        self.set_humidity_setpoints(
            dehumidify_setpoint=dehumidify_setpoint, thermostat_id=thermostat_id
        )

    def set_humidify_setpoint(self, humidify_setpoint, thermostat_id):
        """
        Sets the overall system's humidify setpoint as a percent (0-1).

        The system must support
        :param humidify_setpoint: float
        :param thermostat_id: int - the ID of the thermostat to use
        :return: None
        """
        self.set_humidity_setpoints(
            humidify_setpoint=humidify_setpoint, thermostat_id=thermostat_id
        )

    ########################################################################
    # Zone Get Methods

    def get_zone_ids(self, thermostat_id=None):
        """
        Returns a list of available zone IDs with a starting index of 0.
        :param thermostat_id: int - the ID of the thermostat to use
        :return: list(int)
        """
        # The zones are in a list, so there are no keys to pull out. I have to
        # create a new list of IDs.
        thermostat = self._get_thermostat_json(thermostat_id)
        zone_list = []
        for data_group in thermostat["zones"]:
            zone_list.append(data_group["id"])

        return zone_list
        # list(range(len(self._get_thermostat_settings_key("zones", thermostat_id))))

    def get_zone_name(self, thermostat_id=None, zone_id=0):
        """
        Returns the zone name
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: str
        """
        return str(
            self._get_zone_key("name", thermostat_id=thermostat_id, zone_id=zone_id)
        )

    def get_zone_cooling_setpoint(self, thermostat_id=None, zone_id=0):
        """
        Returns the cooling setpoint in the temperature unit of the thermostat
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: int
        """
        return self._get_zone_key(
            "setpoints", thermostat_id=thermostat_id, zone_id=zone_id
        )["cool"]

    def get_zone_heating_setpoint(self, thermostat_id=None, zone_id=0):
        """
        Returns the heating setpoint in the temperature unit of the thermostat
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: int
        """
        return self._get_zone_key(
            "setpoints", thermostat_id=thermostat_id, zone_id=zone_id
        )["heat"]

    def get_zone_current_mode(self, thermostat_id=None, zone_id=0):
        """
        Returns the current mode of the zone. This may not match the requested
        mode
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: str
        """
        return self._get_zone_setting(
            "zone_mode", thermostat_id=thermostat_id, zone_id=zone_id
        )["current_value"].upper()

    def get_zone_requested_mode(self, thermostat_id=None, zone_id=0):
        """
        Returns the requested mode of the zone. This should match the zone's
        mode on the thermostat.
        Available options can be found in NexiaThermostat.OPERATION_MODES
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: str
        """
        return self._get_zone_features(
            "thermostat_mode", thermostat_id=thermostat_id, zone_id=zone_id
        )["value"].upper()

    def get_zone_temperature(self, thermostat_id=None, zone_id=0):
        """
        Returns the temperature of the zone in the temperature unit of the
        thermostat.
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: int
        """
        return self._get_zone_key(
            "temperature", thermostat_id=thermostat_id, zone_id=zone_id
        )

    def get_zone_presets(self, thermostat_id=None, zone_id=0):
        """
        Supposed to return the zone presets. For some reason, most of the time,
        my unit only returns "AWAY", but I can set the other modes. There is
        the capability to add additional zone presets on the main thermostat,
        so this may not work as expected.

        :param thermostat_id: int - Doesn't do anything as of the current
        implementation
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: int - Doesn't do anything as of the current
        implementation
        :return:
        """
        # return self._get_zone_key("presets", zone_id=zone_id)
        # Can't get Nexia to return all of the presets occasionally, but I
        # don't think there would be any other "presets" available anyway...
        options = self._get_zone_setting(
            "preset_selected", thermostat_id=thermostat_id, zone_id=zone_id
        )["options"]
        return [opt["label"] for opt in options]

    def get_zone_preset(self, thermostat_id=None, zone_id=0):
        """
        Returns the zone's currently selected preset. Should be one of the
        strings in NexiaThermostat.get_zone_presets(zone_id).
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: str
        """
        preset_selected = self._get_zone_setting(
            "preset_selected", thermostat_id=thermostat_id, zone_id=zone_id
        )
        return preset_selected["labels"][preset_selected["current_value"]]

    def get_zone_status(self, thermostat_id=None, zone_id=0):
        """
        Returns the zone status.
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: str
        """
        return self._get_zone_key(
            "zone_status", thermostat_id=thermostat_id, zone_id=zone_id
        )

    def get_run_mode(self, thermostat_id=None, zone_id=0):
        """
        Returns the run mode ("permanent_hold", "run_schedule")
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: str
        """
        return self._get_zone_setting(
            "run_mode", thermostat_id=thermostat_id, zone_id=zone_id
        )

    def get_zone_setpoint_status(self, thermostat_id=None, zone_id=0):
        """
        Returns the setpoint status, like "Following Schedule - Home", or
        "Holding Permanently"
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: str
        """
        run_mode = self.get_run_mode(thermostat_id, zone_id)
        run_mode_current_value = run_mode["current_value"]
        run_mode_label = find_dict_with_keyvalue_in_json(
            run_mode["options"], "value", run_mode_current_value
        )["label"]

        if run_mode_current_value == self.HOLD_PERMANENT:
            return run_mode_label

        preset_label = self.get_zone_preset(thermostat_id, zone_id)
        if run_mode_current_value == self.PRESET_MODE_NONE:
            return run_mode_label
        return f"{run_mode_label} - {preset_label}"

    def is_zone_calling(self, thermostat_id=None, zone_id=0):
        """
        Returns True if the zone is calling for heat/cool.
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: bool
        """
        return bool(
            self._get_zone_key(
                "operating_state", thermostat_id=thermostat_id, zone_id=zone_id
            )
        )

    def check_heat_cool_setpoints(
        self, heat_temperature=None, cool_temperature=None, thermostat_id=None
    ):
        """
        Checks the heat and cool setpoints to check if they are within the
        appropriate range and within the deadband limits.

        Will throw exception if not valid.
        :param heat_temperature: int
        :param cool_temperature: int
        :param thermostat_id: int - the ID of the thermostat to use
        :return: None
        """

        deadband = self.get_deadband(thermostat_id)
        (min_temperature, max_temperature) = self.get_setpoint_limits(thermostat_id)

        if heat_temperature is not None:
            heat_temperature = self.round_temp(heat_temperature, thermostat_id)
        if cool_temperature is not None:
            cool_temperature = self.round_temp(cool_temperature, thermostat_id)

        if (
            heat_temperature is not None
            and cool_temperature is not None
            and not heat_temperature < cool_temperature
        ):
            raise AttributeError(
                f"The heat setpoint ({heat_temperature}) must be less than the"
                f" cool setpoint ({cool_temperature})."
            )
        if (
            heat_temperature is not None
            and cool_temperature is not None
            and not cool_temperature - heat_temperature >= deadband
        ):
            raise AttributeError(
                f"The heat and cool setpoints must be at least {deadband} "
                f"degrees different."
            )
        if heat_temperature is not None and not heat_temperature <= max_temperature:
            raise AttributeError(
                f"The heat setpoint ({heat_temperature} must be less than the "
                f"maximum temperature of {max_temperature} degrees."
            )
        if cool_temperature is not None and not cool_temperature >= min_temperature:
            raise AttributeError(
                f"The cool setpoint ({cool_temperature}) must be greater than "
                f"the minimum temperature of {min_temperature} degrees."
            )
        # The heat and cool setpoints appear to be valid.

    def is_zone_in_permanent_hold(self, thermostat_id=None, zone_id=0):
        """
        Returns True if the zone is in a permanent hold.
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: bool
        """
        return (
            self._get_zone_setting(
                "run_mode", thermostat_id=thermostat_id, zone_id=zone_id
            )["current_value"]
            == self.HOLD_PERMANENT
        )

    ########################################################################
    # Zone Set Methods

    def call_return_to_schedule(self, thermostat_id=None, zone_id=0):
        """
        Tells the zone to return to its schedule.
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: None
        """

        # Set the thermostat
        url = _get_zone_post_url("return_to_schedule", zone_id=zone_id)
        data = {}
        self._post_and_update_zone_json(thermostat_id, zone_id, url, data)

    def call_permanent_hold(
        self,
        heat_temperature=None,
        cool_temperature=None,
        thermostat_id=None,
        zone_id=0,
    ):
        """
        Tells the zone to call a permanent hold. Optionally can provide the
        temperatures.
        :param heat_temperature:
        :param cool_temperature:
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return:
        """

        if heat_temperature is None and cool_temperature is None:
            # Just calling permanent hold on the current temperature
            heat_temperature = self.get_zone_heating_setpoint(
                thermostat_id=thermostat_id, zone_id=zone_id
            )
            cool_temperature = self.get_zone_cooling_setpoint(
                thermostat_id=thermostat_id, zone_id=zone_id
            )
        elif heat_temperature is not None and cool_temperature is not None:
            # Both heat and cool setpoints provided, continue
            pass
        else:
            # Not sure how I want to handle only one temperature provided, but
            # this definitely assumes you're using auto mode.
            raise AttributeError(
                "Must either provide both heat and cool setpoints, or don't "
                "provide either"
            )

        self._set_mode_and_setpoints(
            thermostat_id,
            zone_id,
            self.HOLD_PERMANENT,
            cool_temperature,
            heat_temperature,
        )

    def set_zone_heat_cool_temp(
        self,
        heat_temperature=None,
        cool_temperature=None,
        set_temperature=None,
        thermostat_id=None,
        zone_id=0,
    ):
        """
        Sets the heat and cool temperatures of the zone. You must provide
        either heat and cool temperatures, or just the set_temperature. This
        method will add deadband to the heat and cool temperature from the set
        temperature.

        :param heat_temperature: int or None
        :param cool_temperature: int or None
        :param set_temperature: int or None
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: None
        """
        deadband = self.get_deadband(thermostat_id)

        if set_temperature is None:
            if heat_temperature:
                heat_temperature = self.round_temp(heat_temperature, thermostat_id)
            else:
                heat_temperature = min(
                    self.get_zone_heating_setpoint(
                        thermostat_id=thermostat_id, zone_id=zone_id
                    ),
                    self.round_temp(cool_temperature, thermostat_id) - deadband,
                )

            if cool_temperature:
                cool_temperature = self.round_temp(cool_temperature, thermostat_id)
            else:
                cool_temperature = max(
                    self.get_zone_cooling_setpoint(
                        thermostat_id=thermostat_id, zone_id=zone_id
                    ),
                    self.round_temp(heat_temperature, thermostat_id) + deadband,
                )

        else:
            # This will smartly select either the ceiling of the floor temp
            # depending on the current operating mode.
            zone_mode = self.get_zone_current_mode(
                thermostat_id=thermostat_id, zone_id=zone_id
            )
            if zone_mode == self.OPERATION_MODE_COOL:
                cool_temperature = self.round_temp(set_temperature, thermostat_id)
                heat_temperature = min(
                    self.get_zone_heating_setpoint(
                        thermostat_id=thermostat_id, zone_id=zone_id
                    ),
                    self.round_temp(cool_temperature, thermostat_id) - deadband,
                )
            elif zone_mode == self.OPERATION_MODE_HEAT:
                heat_temperature = self.round_temp(set_temperature, thermostat_id)
                cool_temperature = max(
                    self.get_zone_cooling_setpoint(
                        thermostat_id=thermostat_id, zone_id=zone_id
                    ),
                    self.round_temp(heat_temperature, thermostat_id) + deadband,
                )
            else:
                cool_temperature = self.round_temp(
                    set_temperature, thermostat_id
                ) + math.ceil(deadband / 2)
                heat_temperature = self.round_temp(
                    set_temperature, thermostat_id
                ) - math.ceil(deadband / 2)

        self._set_setpoints(thermostat_id, zone_id, cool_temperature, heat_temperature)

    def _set_mode_and_setpoints(
        self, thermostat_id, zone_id, mode, cool_temperature, heat_temperature
    ):
        # Set the thermostat
        if (
            self.get_run_mode(thermostat_id, zone_id)["current_value"]
            != self.HOLD_PERMANENT
        ):
            url = _get_zone_post_url("run_mode", zone_id=zone_id)
            data = {"value": self.HOLD_PERMANENT}
            self._post_and_update_zone_json(thermostat_id, zone_id, url, data)

        self._set_setpoints(thermostat_id, zone_id, cool_temperature, heat_temperature)

    def _set_setpoints(
        self, thermostat_id, zone_id, cool_temperature, heat_temperature
    ):
        # Check that the setpoints are valid
        self.check_heat_cool_setpoints(
            heat_temperature, cool_temperature, thermostat_id=thermostat_id
        )
        zone_cooling_setpoint = self.get_zone_cooling_setpoint(
            thermostat_id=thermostat_id, zone_id=zone_id
        )
        zone_heating_setpoint = self.get_zone_heating_setpoint(
            thermostat_id=thermostat_id, zone_id=zone_id
        )
        if (
            zone_cooling_setpoint != cool_temperature
            or heat_temperature != zone_heating_setpoint
        ):
            url = _get_zone_post_url("setpoints", zone_id=zone_id)
            data = {"heat": heat_temperature, "cool": cool_temperature}
            self._post_and_update_zone_json(thermostat_id, zone_id, url, data)

    def set_zone_preset(self, preset, thermostat_id=None, zone_id=0):
        """
        Sets the preset of the specified zone.
        :param preset: str - The preset, see
        NexiaThermostat.get_zone_presets(zone_id)
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return: None
        """
        if self.get_zone_preset(thermostat_id=thermostat_id, zone_id=zone_id) != preset:
            url = _get_zone_post_url("preset_selected", zone_id=zone_id)

            preset_selected = self._get_zone_setting(
                "preset_selected", thermostat_id=thermostat_id, zone_id=zone_id
            )
            value = 0
            for option in preset_selected["options"]:
                if option["label"] == preset:
                    value = option["value"]
                    break
            data = {"value": value}
            self._post_and_update_zone_json(thermostat_id, zone_id, url, data)

    def set_zone_mode(self, mode, thermostat_id=None, zone_id=0):
        """
        Sets the mode of the zone.
        :param mode: str - The mode, see NexiaThermostat.OPERATION_MODES
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.
        :return:
        """
        # Validate the data
        if mode in self.OPERATION_MODES:
            url = _get_zone_post_url("zone_mode", zone_id=zone_id)

            data = {"value": mode}
            self._post_and_update_zone_json(thermostat_id, zone_id, url, data)
        else:
            raise KeyError(
                f'Invalid mode "{mode}". Select one of the following: '
                f"{self.OPERATION_MODES}"
            )

    def round_temp(self, temperature: float, thermostat_id=None):
        """
        Rounds the temperature to the nearest 1/2 degree for C and neareast 1
        degree for F
        :param temperature: temperature to round
        :param thermostat_id: int - the ID of the thermostat to use
        :return: float rounded temperature
        """
        if self.get_unit(thermostat_id) == self.UNIT_CELSIUS:
            temperature *= 2
            temperature = round(temperature)
            temperature /= 2
        else:
            temperature = round(temperature)
        return temperature


def _get_zone_post_url(text=None, zone_id=0):
    """
        Returns the POST url from the text parameter for a specific zone
        :param text: str
        :param thermostat_id: int - the ID of the thermostat to use
        :param zone_id: The index of the zone, defaults to 0.

        :return: str
        """
    return "/xxl_zones/" + str(zone_id) + ("/" + text if text else "")


def _get_thermostat_post_url(text=None, thermostat_id=None):
    """
        Returns the POST url from the text parameter
        :param thermostat_id: int - the ID of the thermostat to use
        :param text: str
        :return: str
        """
    return f"/xxl_thermostats/" f"{str(thermostat_id)}/{text if text else ''}"


def find_dict_with_keyvalue_in_json(json_dict, key_in_subdict, value_to_find):
    """
    Searches a json_dict for the key key_in_subdict that matches value_to_find
    :param json_dict: dict
    :param key_in_subdict: str - the name of the key in the subdict to find
    :param value_to_find: str - the value of the key in the subdict to find
    :return: The subdict to find
    """
    for data_group in json_dict:
        if data_group[key_in_subdict] == value_to_find:
            return data_group


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--username", type=str, help="Your Nexia username/email address."
    )
    parser.add_argument("--password", type=str, help="Your Nexia password.")
    parser.add_argument("--house_id", type=int, help="Your house id")
    parser.add_argument(
        "--offline_json",
        type=str,
        help="Offline JSON file to load. No NexiaHome communication will be performed.",
    )

    args = parser.parse_args()
    if args.offline_json:
        nt = NexiaThermostat(offline_json=args.offline_json)
    elif args.username and args.password and args.house_id:
        nt = NexiaThermostat(
            username=args.username, password=args.password, house_id=args.house_id
        )
    else:
        parser.print_help()
        exit()

    print("NexiaThermostat instance can be referenced using nt.<command>.")
    print("List of available thermostats and zones:")
    for _thermostat_id in nt.get_thermostat_ids():
        _thermostat_name = nt.get_thermostat_name(_thermostat_id)
        _thermostat_model = nt.get_thermostat_model(_thermostat_id)
        print(f'{_thermostat_id} - "{_thermostat_name}" ({_thermostat_model})')
        print(f"  Zones:")
        for _zone_id in nt.get_zone_ids(_thermostat_id):
            _zone_name = nt.get_zone_name(_thermostat_id, _zone_id)
            print(f'    {_zone_id} - "{_zone_name}"')
    del (
        _thermostat_id,
        _thermostat_model,
        _thermostat_name,
        _zone_name,
        _zone_id,
        args,
        parser,
    )

    import code
    import readline
    import rlcompleter

    variables = globals()
    variables.update(locals())

    readline.set_completer(rlcompleter.Completer(variables).complete)
    readline.parse_and_bind("tab: complete")
    code.InteractiveConsole(variables).interact()
