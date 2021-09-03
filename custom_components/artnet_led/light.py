from __future__ import annotations
from math import floor
from pprint import pprint
import time
import typing


from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_MIN_MIREDS,
    ATTR_MAX_MIREDS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_WHITE_VALUE,
    ATTR_TRANSITION,
    COLOR_MODE_BRIGHTNESS,
    COLOR_MODE_COLOR_TEMP,
    COLOR_MODE_RGB,
    COLOR_MODE_RGBW,
    COLOR_MODE_RGBWW,
    # COLOR_MODE_WHITE,
    SUPPORT_TRANSITION,
    PLATFORM_SCHEMA,
    LightEntity,
)

from homeassistant.const import CONF_DEVICES
from homeassistant.const import CONF_FRIENDLY_NAME as CONF_DEVICE_FRIENDLY_NAME
from homeassistant.const import CONF_HOST as CONF_NODE_HOST
from homeassistant.const import CONF_NAME as CONF_DEVICE_NAME
from homeassistant.const import CONF_PORT as CONF_NODE_PORT
from homeassistant.const import CONF_TYPE as CONF_DEVICE_TYPE

CONF_DEVICE_TRANSITION = ATTR_TRANSITION

import homeassistant.helpers.config_validation as cv
import homeassistant.util.color as color_util
import voluptuous as vol

import logging

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

REQUIREMENTS = ["pyartnet == 0.8.3"]

log.info(f"PyArtNet: {REQUIREMENTS[0]}")
log.info(f"Version : 2021.07.10")

CONF_NODE_MAX_FPS = "max_fps"
CONF_NODE_REFRESH = "refresh_every"
CONF_NODE_UNIVERSES = "universes"

CONF_DEVICE_CHANNEL = "channel"
CONF_OUTPUT_CORRECTION = "output_correction"
CONF_CHANNEL_SIZE = "channel_size"


# Import with syntax highlighting
import pyartnet

if typing.TYPE_CHECKING:
    import pyartnet

AVAILABLE_CORRECTIONS = {
    "linear": None,
    "quadratic": None,
    "cubic": None,
    "quadruple": None,
}


AVAILABLE_CORRECTIONS["quadratic"] = pyartnet.output_correction.quadratic
AVAILABLE_CORRECTIONS["cubic"] = pyartnet.output_correction.cubic
AVAILABLE_CORRECTIONS["quadruple"] = pyartnet.output_correction.quadruple

CHANNEL_SIZE = {
    "8bit": (1, pyartnet.DmxChannel, 1),
    "16bit": (2, pyartnet.DmxChannel16Bit, 256),
    "24bit": (3, pyartnet.DmxChannel24Bit, 256 * 256),
    "32bit": (4, pyartnet.DmxChannel32Bit, 256 ** 3),
}

ARTNET_NODES = {}


async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):

    import pprint

    for l in pprint.pformat(config).splitlines():
        log.info(l)

    host = config.get(CONF_NODE_HOST)
    port = config.get(CONF_NODE_PORT)

    # setup Node
    __id = f"{host}:{port}"
    if not __id in ARTNET_NODES:
        __node = pyartnet.ArtNetNode(
            host,
            port,
            max_fps=config[CONF_NODE_MAX_FPS],
            refresh_every=config[CONF_NODE_REFRESH],
        )
        await __node.start()
        ARTNET_NODES[id] = __node
    node = ARTNET_NODES[id]
    assert isinstance(node, pyartnet.ArtNetNode), type(node)

    device_list = []
    for universe_nr, universe_cfg in config[CONF_NODE_UNIVERSES].items():
        try:
            universe = node.get_universe(universe_nr)
        except KeyError:
            universe = node.add_universe(universe_nr)
            universe.output_correction = AVAILABLE_CORRECTIONS.get(
                universe_cfg[CONF_OUTPUT_CORRECTION]
            )

        for device in universe_cfg[CONF_DEVICES]:  # type: dict
            device = device.copy()
            cls = __CLASS_TYPE[device[CONF_DEVICE_TYPE]]
            device["unique_id"] = str(host) + str(port) + str(universe_nr)

            # create device
            d = cls(**device)  # type: ArtnetBaseLight
            d.set_channel(
                universe.add_channel(
                    start=device[CONF_DEVICE_CHANNEL],
                    width=d._channel_width,
                    channel_name=d._name,
                    channel_type=d._channel_size[1],
                )
            )
            d._channel.output_correction = AVAILABLE_CORRECTIONS.get(
                device[CONF_OUTPUT_CORRECTION]
            )

            device_list.append(d)

    async_add_devices(device_list)
    return True


class ArtnetBaseLight(LightEntity):
    def __init__(self, name, unique_id, **kwargs):
        self._name = name
        self._channel = kwargs[CONF_DEVICE_CHANNEL]
        self._unique_id = unique_id + str(self._channel)
        self._brightness = 255
        self._fade_time = kwargs[CONF_DEVICE_TRANSITION]
        self._transition = self._fade_time
        self._state = False
        self._channel_size = CHANNEL_SIZE[kwargs[CONF_CHANNEL_SIZE]]
        self._color_mode = kwargs[CONF_DEVICE_TYPE]
        self._vals = 0
        self._features = 0
        self._supported_color_modes = set()
        self._min_mireds = 153  # 6500K as a safe default
        self._max_mireds = 370  # 2700K as a safe default
        # channel & notification callbacks
        self._channel: self._channel_size[1] = None
        self._channel_last_update = 0
        self._scale_factor = 1
        self._channel_width = 0

    def set_channel(self, channel):
        "Set the channel & the callbacks"
        # assert isinstance(channel, self._channel_size[1])
        self._channel = channel
        self._channel.callback_value_changed = self._channel_value_change
        self._channel.callback_fade_finished = self._channel_fade_finish

    @property
    def name(self):
        """Return the display name of this light."""
        return self._name

    @property
    def unique_id(self):
        # TODO add unique ID to device
        """Return unique ID for light."""
        return self._unique_id

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def color_mode(self) -> str | None:
        """Return the color mode of the light."""
        return self._color_mode

    @property
    def supported_features(self):
        """Flag supported features."""
        return self._features

    @property
    def device_state_attributes(self):
        data = {}
        data["dmx_channels"] = [
            k
            for k in range(
                self._channel.start, self._channel.start + self._channel.width, 1
            )
        ]
        data["dmx_values"] = self._channel.get_channel_values()
        data["values"] = self._vals
        data["bright"] = self._brightness
        data["transition"] = self._transition
        self._channel_last_update = time.time()
        return data

    @property
    def is_on(self):
        """Return true if light is on."""
        return self._state

    @property
    def should_poll(self):
        return False

    @property
    def supported_color_modes(self) -> set | None:
        """Flag supported color modes."""
        return self._supported_color_modes

    @property
    def fade_time(self):
        return self._fade_time

    @fade_time.setter
    def fade_time(self, value):
        self._fade_time = value

    def _channel_value_change(self, channel):
        "Shedule update while fade is running"
        if time.time() - self._channel_last_update > 1.1:
            self._channel_last_update = time.time()
            self.async_schedule_update_ha_state()

    def _channel_fade_finish(self, channel):
        "Fade is finished -> shedule update"
        self._channel_last_update = time.time()
        self.async_schedule_update_ha_state()

    def get_target_values(self) -> list:
        "Return the Target DMX Values"
        raise NotImplementedError()

    async def async_create_fade(self, **kwargs):
        "Instruct the light to turn on"
        self._state = True

        self._transition = kwargs.get(ATTR_TRANSITION, self._fade_time)

        self._channel.add_fade(
            self.get_target_values(), self._transition * 1000, pyartnet.fades.LinearFade
        )

        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """
        Instruct the light to turn off. If a transition time has been specified in seconds
        the controller will fade.
        """
        self._transition = kwargs.get(ATTR_TRANSITION, self._fade_time)

        logging.debug(
            "Turning off '%s' with transition  %i", self._name, self._transition
        )
        self._channel.add_fade(
            [0 for k in range(self._channel.width)],
            self._transition * 1000,
            pyartnet.fades.LinearFade,
        )

        self._state = False
        self.async_schedule_update_ha_state()


class ArtnetDimmer(ArtnetBaseLight):
    CONF_TYPE = "dimmer"
    CHANNEL_WIDTH = 1

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._channel_width = 1
        self._supported_color_modes.add(COLOR_MODE_BRIGHTNESS)
        self._features = SUPPORT_TRANSITION
        self._color_mode = COLOR_MODE_BRIGHTNESS

    def get_target_values(self):
        return [self.brightness * self._channel_size[2]]

    async def async_turn_on(self, **kwargs):

        # Update state from service call
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]

        await super().async_create_fade(**kwargs)


class ArtnetRGB(ArtnetBaseLight):
    CONF_TYPE = "rgb"
    CHANNEL_WIDTH = 3

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._channel_width = 3
        self._supported_color_modes.add(COLOR_MODE_RGB)
        self._features = SUPPORT_TRANSITION
        self._color_mode = COLOR_MODE_RGB
        self._vals = [255, 255, 255]

    @property
    def rgb_color(self) -> tuple:
        """Return the rgb color value."""
        return self._vals

    def get_target_values(self):
        l = [floor(k * self._scale_factor * self._channel_size[2]) for k in self._vals]
        return l

    async def async_turn_on(self, **kwargs):
        """
        Instruct the light to turn on.
        """

        # RGB already contains brightness information
        if ATTR_RGB_COLOR in kwargs:
            self._vals = kwargs[ATTR_RGB_COLOR]
            # self._scale_factor = 1

        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
            self._scale_factor = self._brightness / 255

        await super().async_create_fade(**kwargs)
        return None


class ArtnetWhite(ArtnetBaseLight):
    CONF_TYPE = "color_temp"
    CHANNEL_WIDTH = 2

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._channel_width = 2
        self._supported_color_modes.add(COLOR_MODE_COLOR_TEMP)
        self._supported_color_modes.add('white')
        self._features = SUPPORT_TRANSITION
        self._color_mode = COLOR_MODE_COLOR_TEMP
        self._vals = (self._max_mireds + self._min_mireds) / 2 or 300

    @property
    def color_temp(self) -> int:
        """Return the CT color temperature."""
        return self._vals

    @property
    def min_mireds(self) -> int:
        """Return the coldest color_temp that this light supports."""
        return self._min_mireds

    @property
    def max_mireds(self) -> int:
        """Return the warmest color_temp that this light supports."""
        return self._max_mireds

    def get_target_values(self):
        ww_fraction = (self._vals - self.min_mireds) / (
            self.max_mireds - self.min_mireds
        )
        cw_fraction = 1 - ww_fraction
        max_fraction = max(ww_fraction, cw_fraction)
        l = [
            floor(
                self.is_on
                * self._brightness
                * (cw_fraction / max_fraction)
                * self._channel_size[2]
            ),
            floor(
                self.is_on
                * self._brightness
                * (ww_fraction / max_fraction)
                * self._channel_size[2]
            ),
        ]
        return l

    async def async_turn_on(self, **kwargs):
        """
        Instruct the light to turn on.
        """
        if ATTR_COLOR_TEMP in kwargs:
            self._vals = kwargs[ATTR_COLOR_TEMP]
            # self._scale_factor = 1

        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
            self._scale_factor = self._brightness / 255

        await super().async_create_fade(**kwargs)
        return None


class ArtnetRGBW(ArtnetBaseLight):
    CONF_TYPE = "rgbw"
    CHANNEL_WIDTH = 4

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._channel_width = 4
        self._supported_color_modes.add(COLOR_MODE_RGBW)
        self._features = SUPPORT_TRANSITION
        self._color_mode = COLOR_MODE_RGBW
        self._vals = [255, 255, 255, 255]

    @property
    def rgbw_color(self) -> tuple:
        """Return the rgbw color value."""
        return self._vals

    def get_target_values(self):
        l = [floor(k * self._scale_factor * self._channel_size[2]) for k in self._vals]
        return l

    async def async_turn_on(self, **kwargs):
        """
        Instruct the light to turn on.
        """
        # RGB already contains brightness information
        if ATTR_RGBW_COLOR in kwargs:
            self._vals = kwargs[ATTR_RGBW_COLOR]
            # self._scale_factor = 1

        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
            self._scale_factor = self._brightness / 255

        await super().async_create_fade(**kwargs)
        return None


class ArtnetRGBWW(ArtnetBaseLight):
    CONF_TYPE = "rgbww"
    CHANNEL_WIDTH = 5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._channel_width = 5
        self._supported_color_modes.add(COLOR_MODE_RGBWW)
        self._features = SUPPORT_TRANSITION
        self._color_mode = COLOR_MODE_RGBWW
        self._vals = [255, 255, 255, 255, 255]

    @property
    def rgbww_color(self) -> tuple:
        """Return the rgbww color value."""
        return self._vals

    def get_target_values(self):
        l = [floor(k * self._scale_factor * self._channel_size[2]) for k in self._vals]
        return l

    async def async_turn_on(self, **kwargs):
        """
        Instruct the light to turn on.
        """

        # RGB already contains brightness information
        if ATTR_RGBWW_COLOR in kwargs:
            self._vals = kwargs[ATTR_RGBWW_COLOR]
            # self._scale_factor = 1

        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
            self._scale_factor = self._brightness / 255

        await super().async_create_fade(**kwargs)
        return None


# ------------------------------------------------------------------------------
# conf
# ------------------------------------------------------------------------------

__CLASS_LIST = [ArtnetDimmer, ArtnetRGB, ArtnetWhite, ArtnetRGBW, ArtnetRGBWW]
__CLASS_TYPE = {k.CONF_TYPE: k for k in __CLASS_LIST}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NODE_HOST): cv.string,
        vol.Required(CONF_NODE_UNIVERSES): {
            vol.All(int, vol.Range(min=0, max=1024)): {
                vol.Optional(CONF_OUTPUT_CORRECTION, default=None): vol.Any(
                    None, vol.In(AVAILABLE_CORRECTIONS)
                ),
                CONF_DEVICES: vol.All(
                    cv.ensure_list,
                    [
                        {
                            vol.Required(CONF_DEVICE_CHANNEL): vol.All(
                                vol.Coerce(int), vol.Range(min=1, max=512)
                            ),
                            vol.Required(CONF_DEVICE_NAME): cv.string,
                            vol.Optional(CONF_DEVICE_FRIENDLY_NAME): cv.string,
                            vol.Optional(CONF_DEVICE_TYPE, default='dimmer'): vol.In(
                                [k.CONF_TYPE for k in __CLASS_LIST]
                            ),
                            vol.Optional(CONF_DEVICE_TRANSITION, default=0): vol.All(
                                vol.Coerce(float), vol.Range(min=0, max=999)
                            ),
                            vol.Optional(CONF_OUTPUT_CORRECTION, default=None): vol.Any(
                                None, vol.In(AVAILABLE_CORRECTIONS)
                            ),
                            vol.Optional(CONF_CHANNEL_SIZE, default="8bit"): vol.Any(
                                None, vol.In(CHANNEL_SIZE)
                            ),
                        }
                    ],
                ),
            },
        },
        vol.Optional(CONF_NODE_PORT, default=6454): cv.port,
        vol.Optional(CONF_NODE_MAX_FPS, default=25): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=40)
        ),
        vol.Optional(CONF_NODE_REFRESH, default=120): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=9999)
        ),
    },
    required=True,
    extra=vol.PREVENT_EXTRA,
)
