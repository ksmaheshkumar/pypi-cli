#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    pypi_cli
    ~~~~~~~~

    A command line interface to the Python Package Index.

    :copyright: (c) 2014 by Steven Loria.
    :license: BSD, see LICENSE for more details.
"""
from __future__ import unicode_literals, division, print_function
import re
import sys
import time
import math
from collections import OrderedDict

import requests
from dateutil.parser import parse as dateparse
import click
from click import echo, style
from click.termui import get_terminal_size

__version__ = "0.1.0-dev"
__author__ = "Steven Loria"
__license__ = "MIT"

DATE_FORMAT = "%y/%m/%d"
MARGIN = 3
TICK = '*'
DEFAULT_PYPI = 'http://pypi.python.org/pypi'
PYPI_RE = re.compile('''^(?:(?P<pypi>https?://[^/]+/pypi)/)?
                        (?P<name>[-A-Za-z0-9.]+)
                        (?:/(?P<version>[-A-Za-z0-9.]+))?$''', re.X)

# Number of characters added by bold formatting
_BOLD_LEN = 8
# Number of characters added by color formatting
_COLOR_LEN = 9

def print_version(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    echo(__version__)
    ctx.exit()


def echof(s, *args, **kwargs):
    echo(style(s, *args, **kwargs))


@click.group()
@click.option('--version', '-v',
    is_flag=True,
    callback=print_version,
    expose_value=False,
    is_eager=True,
    help="Show the current pypi-cli version.")
def cli():
    """The pypi CLI.

    \b
    Examples:
        \b
        pypi stat Django
        pypi browse Flask

    To get help with a subcommand, add the --help option after the command.

    \b
        pypi stat --help
    """
    pass


def abort_not_found(name):
    raise click.ClickException('No versions of "{0}" were found. Please try '
        'your search again. NOTE: Case matters.'.format(name))


@cli.command()
@click.option('--graph/--no-graph', '-g/-q', default=True,
    help="Output a graph of download counts.")
@click.argument('package', nargs=-1, required=True)
def stat(package, graph):
    """Print download statistics for a package.

    \b
    Example:

        pypi stat requests
    """
    client = requests.Session()
    for name_or_url in package:
        m = PYPI_RE.match(name_or_url)
        if not m:
            echo('Invalid name or URL: "{name}"'.format(name=name_or_url),
                  file=sys.stderr)
            continue
        pypi_url = m.group('pypi') or DEFAULT_PYPI
        name = m.group('name')
        package = Package(name, pypi_url=pypi_url, client=client)
        try:
            version_downloads = package.version_downloads
        except NotFoundError:
            abort_not_found(name)
        echo("Fetching statistics for '{url}'. . .".format(url=package.package_url))
        min_ver, min_downloads = package.min_version
        max_ver, max_downloads = package.max_version
        if min_ver is None or max_ver is None:
            raise click.ClickException('Package has no releases')
        avg_downloads = package.average_downloads
        total = package.downloads
        echo()
        echof("Download statistics for {name}".format(name=name), bold=True)
        if graph:
            echo()
            echo('Downloads by version')
            echo(package.chart())
        echo()
        echo("Min downloads:   {min_downloads:12,} ({min_ver})".format(**locals()))
        echo("Max downloads:   {max_downloads:12,} ({max_ver})".format(**locals()))
        echo("Avg downloads:   {avg_downloads:12,}".format(**locals()))
        echo("Total downloads: {total:12,}".format(**locals()))
        echo()
        echo('Last day:    {daily:12,}'.format(daily=package.downloads_last_day))
        echo('Last week:   {weekly:12,}'.format(weekly=package.downloads_last_week))
        echo('Last month:  {monthly:12,}'.format(monthly=package.downloads_last_month))
        echo()

@cli.command()
@click.option('--homepage', is_flag=True, default=False)
@click.argument('package', required=True)
def browse(package, homepage):
    """Browse to a package's PyPI or project homepage."""
    p = Package(package)
    try:
        if homepage:
            url = p.home_page
        else:
            url = p.package_url
    except NotFoundError:
        abort_not_found(package)

    echo('Opening PyPI page for "{0}"'.format(package))
    click.termui.launch(url)


def lazy_property(fn):
    """Decorator that makes a property lazy-evaluated."""
    attr_name = '_lazy_' + fn.__name__

    @property
    def _lazy_property(self):
        if not hasattr(self, attr_name):
            setattr(self, attr_name, fn(self))
        return getattr(self, attr_name)
    return _lazy_property


def bargraph(data, max_key_width=30):
    """Return a bar graph as a string, given a dictionary of data."""
    lines = []
    max_length = min(max(len(key) for key in data.keys()), max_key_width)
    max_val = max(data.values())
    max_val_length = max(len('{:,}'.format(val)) for val in data.values())
    term_width = get_terminal_size()[0]
    max_bar_width = term_width - MARGIN - (max_length + 3 + max_val_length + 3)
    template = "{key:{key_width}} [ {value:{val_width},d} ] {bar}"
    for key, value in data.items():
        try:
            bar = int(math.ceil(max_bar_width * value / max_val)) * TICK
        except ZeroDivisionError:
            bar = ''
        line = template.format(
            key=key[:max_length],
            value=value,
            bar=bar,
            key_width=max_length,
            val_width=max_val_length)
        lines.append(line)
    return '\n'.join(lines)


class PackageError(Exception):
    pass


class NotFoundError(PackageError):
    pass


class Package(object):

    def __init__(self, name, client=None, pypi_url=DEFAULT_PYPI):
        self.client = client or requests.Session()
        self.name = name
        self.url = '{pypi_url}/{name}/json'.format(pypi_url=pypi_url, name=name)

    @lazy_property
    def data(self):
        resp = self.client.get(self.url)
        if resp.status_code == 404:
            raise NotFoundError('Package not found')
        return resp.json()

    @lazy_property
    def versions(self):
        """Return a list of versions, sorted by release datae."""
        return [k for k, v in self.release_info]

    @lazy_property
    def version_downloads(self):
        """Return a dictionary of version:download_count pairs."""
        ret = OrderedDict()
        for release, info in self.release_info:
            download_count = sum(file_['downloads'] for file_ in info)
            ret[release] = download_count
        return ret

    @property
    def release_info(self):
        release_info = self.data['releases']
        # filter out any versions that have no releases
        filtered = [(ver, releases) for ver, releases in release_info.items()
                    if len(releases) > 0]
        # sort by first upload date of each release
        return sorted(filtered, key=lambda x: x[1][0]['upload_time'])

    @lazy_property
    def version_dates(self):
        ret = OrderedDict()
        for release, info in self.release_info:
            if info:
                upload_time = dateparse(info[0]['upload_time'])
                ret[release] = upload_time
        return ret

    def chart(self):
        data = OrderedDict()
        for version, dl_count in self.version_downloads.items():
            date = self.version_dates.get(version)
            date_formatted = ''
            if date:
                date_formatted = time.strftime(DATE_FORMAT,
                    self.version_dates[version].timetuple())
            key = "{0:16} {1}".format(
                style(version, fg='cyan'),
                date_formatted
            )
            data[key] = dl_count
        return bargraph(data, max_key_width=20 + _COLOR_LEN)

    @lazy_property
    def downloads(self):
        """Total download count.

        :return: A tuple of the form (version, n_downloads)
        """
        return sum(self.version_downloads.values())

    @lazy_property
    def max_version(self):
        """Version with the most downloads.

        :return: A tuple of the form (version, n_downloads)
        """
        data = self.version_downloads
        if not data:
            return None, 0
        return max(data.items(), key=lambda item: item[1])

    @lazy_property
    def min_version(self):
        """Version with the fewest downloads."""
        data = self.version_downloads
        if not data:
            return (None, 0)
        return min(data.items(), key=lambda item: item[1])

    @lazy_property
    def average_downloads(self):
        """Average number of downloads."""
        return int(self.downloads / len(self.versions))

    @property
    def downloads_last_day(self):
        return self.data['info']['downloads']['last_day']

    @property
    def downloads_last_week(self):
        return self.data['info']['downloads']['last_week']

    @property
    def downloads_last_month(self):
        return self.data['info']['downloads']['last_month']

    @property
    def package_url(self):
        return self.data['info']['package_url']

    @property
    def home_page(self):
        return self.data['info']['home_page']

    def __repr__(self):
        return '<Package(name={0!r})>'.format(self.name)