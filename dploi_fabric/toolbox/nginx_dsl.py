# -*- coding: utf-8 -*-
from contextlib import contextmanager


class Config(object):
    """
    A configuration directive
    """
    def __init__(self, *keys):
        self.keys = map(str, keys)
        self.values = []

    def set_values(self, values):
        self.values = map(str, values)

    def render(self):
        return ['%s;' % ' '.join(self.keys + self.values)]


class Nginx(object):
    """
    Configuration section (or root)
    """
    # multi key configs are configurations which don't have a unique directive (first argument)
    # the number indicates how many arguments together should form the cache key
    _multi_key_configs = {
        'proxy_set_header': 2,
        'add_header': 2,
        'server': 2,
    }

    def __init__(self, *args):
        self._args = map(str, args)
        self._children = []
        self._cache = {}

    def _make_child(self, cls, *args):
        """
        Add a child class cls instantiating it with *args, or return it from cache
        """
        if args in self._cache:
            return self._cache[args]
        child = cls(*args)
        self._children.append(child)
        self._cache[args] = child
        return child

    def _remove(self, cache_key):
        """
        Remove a child
        """
        child = self._cache.pop(cache_key)
        self._children.remove(child)

    def render(self):
        """
        Render the section (return as list of strings)
        """
        lines = []
        if self._args:
            lines.append('%s {' % ' '.join(self._args))
        for child in self._children:
            lines.extend(child.render())
        if self._args:
            lines.append('}')
        return lines

    @contextmanager
    def server(self, listen, *names):
        """
        Add a 'server' section.

        This is a special case and not used by Nginx.section because it takes no arguments, which means we have to use
        some tricks with the cache keys.
        """
        yield self._make_child(Server, 'server', listen, *names)

    @contextmanager
    def section(self, name, first_arg, *other_args):
        """
        Add a section <name>.

        first_arg is not in *args because this function requires at least two arguments to effectively cache it without
        causing duplicate cache keys.
        """
        if name == 'server':
            raise TypeError("Use Nginx.server() instead of Nginx.section('server')")
        yield self._make_child(Nginx, name, first_arg, *other_args)

    def remove_section(self, name, first_arg, *other_args):
        """
        Remove a section.
        """
        if name == 'server':
            raise TypeError("Cannot remove server")
        cache_key = (name, first_arg) + other_args
        self._remove(cache_key)

    def _config_cache_key_values(self, args):
        """
        Split a tuple of args into its cache key and values (to be used for Config)
        """
        cache_length = self._multi_key_configs.get(args[0], 1)
        cache_key = args[:cache_length]
        values = args[cache_length:]
        return cache_key, values

    def config(self, *args):
        """
        Add a configuration directive
        """
        cache_key, values = self._config_cache_key_values(args)
        child = self._make_child(Config, *cache_key)
        child.set_values(values)
        return child

    def get_config(self, *args):
        """
        Get a (cached) config directive value.

        Nginx.config can't be used because if it's only called with the cache key (directive), it will set the value
        to nothing.
        """
        return self._cache[args].values

    def remove_config(self, *args):
        """
        Remove a configuration directive.
        """
        cache_key, values = self._config_cache_key_values(args)
        self._remove(cache_key)


class Server(Nginx):
    """
    See Nginx.server for the reason this is a special case.
    """
    def __init__(self, _, listen, *names):
        super(Server, self).__init__('server')
        self.config('listen', listen)
        self.config('server_name', *names)


def prettify(nginx):
    """
    Indents the sections and adds empty lines after a section is closed.
    """
    indent = 0
    for line in nginx.render():
        if line.startswith('}'):
            indent -= 1
        yield '%s%s' % ('    '  * indent, line)
        if line.startswith('}'):
            yield ''
        if line.endswith('{'):
            indent += 1

