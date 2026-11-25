"""Run isolated fixture scripts and compile overlay sources with pinned LuaJIT."""

import os
from pathlib import Path
import sys
from unittest import SkipTest


def _fixture_runtime():
    try:
        from lupa.luajit21 import LuaRuntime
    except ModuleNotFoundError as error:
        if error.name not in ('lupa', 'lupa.luajit21'):
            raise
        raise SkipTest('Lua fixture requires lupa.luajit21 from requirements-dev.txt') from error
    return LuaRuntime(encoding=None)


def run_fixture(script, *arguments):
    lua = _fixture_runtime()
    lua.globals().arg = lua.table_from({
        index: os.fsencode(value) for index, value in enumerate((script, *arguments))
    })
    lua.execute(script.read_bytes(), name=os.fsencode(f'@{script}'))


def compile_fixture(source, output):
    lua = _fixture_runtime()
    chunk = lua.compile(source.read_bytes(), name=os.fsencode(f'@{source}'))
    output.write_bytes(lua.eval(b'string.dump')(chunk, True))


if __name__ == '__main__':
    run_fixture(Path(sys.argv[1]), *sys.argv[2:])
