import colorsys
import logging
import operator as op
from functools import reduce
from typing import List, NamedTuple

from .settings import DEFAULT_KELVIN

__author__ = 'acushner'

log = logging.getLogger(__name__)


class RGBk(NamedTuple):
    r: int
    g: int
    b: int
    k: int = DEFAULT_KELVIN

    @property
    def hex(self) -> str:
        """loses kelvin in this conversion"""
        return hex((self.r << 16) + (self.g << 8) + self.b)

    @property
    def color(self) -> 'Color':
        return Color.from_rgb(self)

    def __add__(self, other) -> 'RGBk':
        add = lambda v1, v2: int(((v1 ** 2 + v2 ** 2) / 2) ** .5)
        return RGBk(add(self.r, other.r), add(self.g, other.g), add(self.b, other.b), (self.k + other.k) // 2)


class Color(NamedTuple):
    hue: int
    saturation: int
    brightness: int
    kelvin: int = DEFAULT_KELVIN

    _mult = 2 ** 16
    _max_complements = 1024

    @classmethod
    def from_hex(cls, h, kelvin=DEFAULT_KELVIN) -> 'Color':
        nums = []
        for _ in range(3):
            nums.append(h & 0xff)
            h >>= 8
        nums.reverse()
        return cls.from_rgb(RGBk(*nums, kelvin))

    @classmethod
    def from_rgb(cls, rgb: RGBk) -> 'Color':
        h, s, b = colorsys.rgb_to_hsv(*rgb[:3])
        mult = cls._mult - 1
        return cls(*map(int, (h * mult, s * mult, b / 255 * mult, rgb.k)))

    @property
    def hex(self) -> str:
        return self.rgb.hex

    @property
    def rgb(self) -> RGBk:
        mult = self._mult - 1
        h, s, b = self.hue / mult, self.saturation / mult, self.brightness / mult * 255
        return RGBk(*map(int, colorsys.hsv_to_rgb(h, s, b)), self.kelvin)

    def offset_hue(self, degrees) -> 'Color':
        return self._replace(hue=int(abs(self.hue + degrees / 360 * self._mult) % self._mult))

    def __add__(self, other) -> 'Color':
        """avg colors together using math"""
        return (self.rgb + other.rgb).color

    def __iadd__(self, other):
        return self + other

    def get_complements(self, degrees) -> List['Color']:
        """
        return list of colors offset by degrees

        this list will contain all unique colors that can be produced by this
        degree offset (i.e., it will keep offsetting until it makes it around the color wheel
        back to the starting point)

        useful because it avoids rounding errors that can occur by doing something like:
        >>> c = Colors.YALE_BLUE
        >>> for _ in range(1000):
        >>>     c = c.offset_hue(30)
        """
        hue_d = self.hue // 360
        res = [self]
        for n in range(1, self._max_complements):
            n_deg = n * degrees
            if n_deg % 360 == 0:
                break

            res.append(self.offset_hue(n_deg))
        else:
            from warnings import warn
            warn(f'exceeded max number of complements: {self._max_complements}, something may have gone wrong')

        return res


class ColorsMeta(type):
    def __iter__(cls):
        yield from ((name, val)
                    for name, val in vars(cls).items()
                    if isinstance(val, Color))

    def __getitem__(cls, item):
        return cls.__dict__[item]

    def __str__(cls):
        colors = '\n\t'.join(map(str, cls))
        return f'Colors:\n\t{colors}'


class Colors(metaclass=ColorsMeta):
    DEFAULT = Color(43520, 0, 39321)
    RED = Color(65535, 65535, 65535, 3500)
    ORANGE = Color(6500, 65535, 65535, 3500)
    YELLOW = Color(9000, 65535, 65535, 3500)
    GREEN = Color(16173, 65535, 65535, 3500)
    CYAN = Color(29814, 65535, 65535, 3500)
    BLUE = Color(43634, 65535, 65535, 3500)
    PURPLE = Color(50486, 65535, 65535, 3500)
    PINK = Color(58275, 65535, 47142, 3500)
    WHITE = Color(58275, 0, 65535, 5500)
    COLD_WHITE = Color(58275, 0, 65535, 9000)
    WARM_WHITE = Color(58275, 0, 65535)
    GOLD = Color(58275, 0, 65535, 2500)

    YALE_BLUE = Color.from_hex(0xf4d92)

    HANUKKAH_BLUE = Color.from_hex(0x09239b)

    STEELERS_GOLD = Color.from_hex(0xffb612)
    STEELERS_BLACK = Color.from_hex(0x101820)
    STEELERS_BLUE = Color.from_hex(0x00539b)
    STEELERS_RED = Color.from_hex(0xc60c30)
    STEELERS_SILVER = Color.from_hex(0xa5acaf)

    SNES_LIGHT_PURPLE = Color.from_hex(0xb5b6e4)
    SNES_DARK_PURPLE = Color.from_hex(0x4f43ae)
    SNES_DARK_GREY = Color.from_hex(0x908a99)
    SNES_LIGHT_GREY = Color.from_hex(0xcec9cc)
    SNES_BLACK = Color.from_hex(0x211a21)

    COPILOT_DARK_BLUE = Color.from_hex(0x193849)
    COPILOT_BLUE = Color.from_hex(0x00b4e3)
    COPILOT_BLUE_GREY = Color.from_hex(0x386e8f)
    COPILOT_BLUE_GREEN = Color.from_hex(0x00827d)

    RAINBOW = RED, ORANGE, YELLOW, GREEN, CYAN, BLUE, PURPLE, PINK

    @classmethod
    def sum(cls, *colors: Color):
        """average together all colors provided"""
        return reduce(op.add, colors)

    @classmethod
    def by_name(cls, name) -> List[Color]:
        """get colors if they contain `name` in their name"""
        name = name.lower()
        return [c for n, c in cls if name in n.lower()]


class ColorPower(NamedTuple):
    color: Color
    power: int
