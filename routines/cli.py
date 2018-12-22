import operator as op
from functools import reduce
from typing import List

import click
from click import echo

import routines
from lifxlan import LifxLAN, Group, Colors, Color, Themes, Theme, ColorPower

__author__ = 'acushner'


class LifxProxy(LifxLAN):
    """proxy to LifxLAN. will only be created once when necessary"""

    def __init__(self):
        self._lights: LifxLAN = None

    def __getattribute__(self, item):
        ga = super().__getattribute__
        if ga('_lights') is None:
            self._lights = LifxLAN()
        return getattr(ga('_lights'), item)


lifx = LifxProxy()

DEFAULT_GROUP = 'ALL'
DEFAULT_COLOR = 'DEFAULT'
DEFAULT_THEME = 'copilot'


def _parse_groups(ctx, param, name_or_names) -> Group:
    if name_or_names == DEFAULT_GROUP:
        return lifx

    names = name_or_names.split(',')
    return reduce(op.add, (lifx[n] for n in names))


def _parse_colors(ctx, param, colors) -> List[Color]:
    if not colors:
        return []
    return [Colors[c.upper()] for c in colors.split(',')]


def _parse_themes(ctx, param, themes) -> List[Theme]:
    if not themes:
        return []
    return [Themes[t.lower()] for t in themes.split(',')]


class Config:
    def __init__(self):
        self.group: Group = None
        self.colors: List[Color] = []
        self.themes: List[Theme] = []

    @property
    def merged_colors(self) -> List[Color]:
        self.validate_colors()
        themes = [c for t in self.themes for c in t] if self.themes else []
        colors = self.colors or []
        return colors + themes

    @property
    def merged_themes(self) -> Theme:
        self.validate_colors()
        color_theme = Theme.from_colors(*self.colors)
        themes = self.themes or []
        return reduce(op.add, themes + [color_theme])

    @property
    def color_theme(self):
        return self.merged_themes

    def validate_colors(self):
        """ensure that at least one of themes/colors is populated"""
        if not (self.themes or self.colors):
            raise ValueError('must set at least themes or colors')

    def __str__(self):
        return f'{self.colors}\n{self.themes}\n{self.group}'


pass_conf = click.make_pass_decorator(Config, ensure=True)


@click.group()
@click.option('--groups', 'group', callback=_parse_groups, default=DEFAULT_GROUP,
              help='csv of group or light name[s]')
@click.option('--colors', 'colors', callback=_parse_colors, default=None,
              help='csv of color[s] to apply')
@click.option('--themes', 'themes', callback=_parse_themes, default=None,
              help='csv of theme[s] to apply')
@pass_conf
def cli_main(conf: Config, group, colors, themes):
    conf.group = group
    conf.colors = colors
    conf.themes = themes


@cli_main.command()
@click.option('-l', '--light', is_flag=True, help='display info on all existing lights/groups')
@click.option('-c', '--color', is_flag=True, help='display info related to colors/themes')
@click.option('-d', '--debug', is_flag=True, help='display light debug info')
def info(light, color, debug):
    """display info about existing lights/groups and colors/themes"""
    if not (light or color or debug):
        light = color = True  # skip debug intentionally

    if light:
        echo(str(lifx))
        echo('\n'.join(map(str, lifx.auto_group().values())))

    if debug:
        echo('\n'.join(l.info_str() for l in lifx))

    if color:
        echo(80 * '=')
        echo('COLORS\n')
        echo('\n'.join(map(str, (c.color_str(name) for name, c in Colors))))

        echo('\n\n')
        echo(80 * '=')
        echo('THEMES\n')
        echo('\n\n'.join(map(str, (t.color_str(name) for name, t in Themes))))
        echo('\n')


@cli_main.command()
@click.option('--dot', callback=_parse_colors, default=None,
              help='set color for dot')
@click.option('--dash', callback=_parse_colors, default=None,
              help='set color for dash (will default to dot if not provided')
@click.argument('phrase', nargs=-1, required=True)
@pass_conf
def morse_code(conf: Config, dot, dash, phrase):
    """convert phrase into morse code"""
    phrase = ' '.join(phrase)
    echo(f'morse code: {phrase}')
    s = routines.MCSettings()

    if dot:
        dot = ColorPower(dot[0], 1)
    if dash:
        dash = ColorPower(dash[0], 1)

    dot = dot or s.dot
    dash = dash or dot or s.dash

    routines.morse_code(phrase, conf.group, routines.MCSettings(dot, dash))


@cli_main.command()
@click.option('-t', '--duration-secs', default=.5, help='how many secs for each color to appear')
@click.option('--smooth', is_flag=True, help='smooth transition between colors')
@pass_conf
def rainbow(conf: Config, duration_secs, smooth):
    """make lights cycle through rainbow color group"""
    routines.rainbow(conf.group, conf.merged_colors or Themes.rainbow, duration_secs=duration_secs, smooth=smooth)


@cli_main.command()
@pass_conf
def key_control(conf: Config):
    """control lights with the computer keyboard"""
    routines.key_control(conf.group, conf.color_theme)


@cli_main.command()
@click.option('-s', '--breath-secs', default=8)
@click.option('-m', '--duration-mins', default=20)
@click.option('--min-brightness-pct', default=30)
@click.option('--max-brightness-pct', default=60)
@pass_conf
def breathe(conf: Config, breath_secs, duration_mins, min_brightness_pct, max_brightness_pct):
    """make lights oscillate between darker and brighter """
    routines.breathe(conf.group, breath_secs, min_brightness_pct, max_brightness_pct, conf.color_theme,
                     duration_mins)


@cli_main.command()
@click.option('-s', '--blink-secs', default=.5)
@click.option('--how-long-secs', default=8)
@pass_conf
def blink_color(conf: Config, blink_secs, how_long_secs):
    """blink lights' colors"""
    routines.blink_color(conf.group, conf.color_theme, blink_secs, how_long_secs)


@cli_main.command()
@click.option('-s', '--blink-secs', default=.5)
@click.option('--how-long-secs', default=8)
@pass_conf
def blink_power(conf: Config, blink_secs, how_long_secs):
    """blink lights' power"""
    routines.blink_power(conf.group, blink_secs, how_long_secs)


@cli_main.command()
@click.option('-s', '--rotate-secs', default=60, help='how many seconds between each theme application')
@click.option('-m', '--duration-mins', default=20, help='how many minutes the command will run')
@click.option('--transition-secs', default=10, help='how many seconds to transition between themes')
@pass_conf
def cycle_themes(conf: Config, rotate_secs, duration_mins, transition_secs):
    """cycle through themes/colors passed in"""
    routines.cycle_themes(conf.group, *conf.themes, *conf.colors, rotate_secs=rotate_secs, duration_mins=duration_mins,
                          transition_secs=transition_secs)


@cli_main.command()
@pass_conf
def reset(conf: Config):
    """reset light colors to either DEFAULT or the first color you pass in"""
    lifx.set_color(conf.colors[0] if conf.colors else Colors.DEFAULT)


def __main():
    return cli_main()


if __name__ == '__main__':
    __main()
