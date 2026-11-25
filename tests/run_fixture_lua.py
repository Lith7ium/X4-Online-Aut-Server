"""Run isolated fixture scripts and compile overlay sources with pinned LuaJIT."""

import os

from lupa.luajit21 import LuaRuntime


def run_fixture(script, *arguments):
    lua = LuaRuntime(encoding=None)
    lua.globals().arg = lua.table_from({
        index: os.fsencode(value) for index, value in enumerate((script, *arguments))
    })
    lua.execute(script.read_bytes(), name=os.fsencode(f'@{script}'))


def compile_fixture(source, output):
    lua = LuaRuntime(encoding=None)
    chunk = lua.compile(source.read_bytes(), name=os.fsencode(f'@{source}'))
    output.write_bytes(lua.eval(b'string.dump')(chunk, True))
