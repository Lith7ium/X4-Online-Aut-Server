"""Read the active official DLC requirements from an X4 compressed save header."""

import gzip
from xml.parsers import expat


class _HeaderComplete(Exception):
    pass


def active_dlc_ids(path):
    """Return sorted official extension IDs, excluding the save's patch history."""
    element_path = []
    required = set()
    parser = expat.ParserCreate()

    def start_element(name, attributes):
        element_path.append(name)
        if len(element_path) == 1 and name != 'savegame':
            raise ValueError('expected an X4 savegame root')
        if element_path == ['savegame', 'info', 'patches', 'patch']:
            extension = attributes.get('extension', '')
            if extension.startswith('ego_dlc_'):
                required.add(extension)

    def end_element(_name):
        if element_path == ['savegame', 'info']:
            raise _HeaderComplete
        element_path.pop()

    def reject_doctype(*_args):
        raise ValueError('unexpected document type in X4 save header')

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.StartDoctypeDeclHandler = reject_doctype
    # World data can be hundreds of MB. Stop as soon as the info element closes.
    remaining = 4 * 1024 * 1024
    try:
        with gzip.open(path, 'rb') as stream:
            while remaining:
                chunk = stream.read(min(16384, remaining))
                if not chunk:
                    parser.Parse(b'', True)
                    break
                remaining -= len(chunk)
                parser.Parse(chunk, False)
    except _HeaderComplete:
        return tuple(sorted(required))
    except (EOFError, gzip.BadGzipFile, expat.ExpatError) as error:
        raise ValueError('invalid compressed X4 save header') from error
    raise ValueError('could not find a complete X4 save header within 4 MiB')
