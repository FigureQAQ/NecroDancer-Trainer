"""Inspect local Synchrony bytecode without executing game scripts."""
import argparse
import ctypes
import os
from pathlib import Path
import re
import struct
import zlib

GAME = Path(os.environ.get(
    "NECRODANCER_GAME_DIR",
    r"E:\SteamLibrary\steamapps\common\Crypt of the NecroDancer\Necrodancer64",
))


def resources():
    result = {}
    for archive in (GAME / "NecroDancer.wsp", GAME / "versions/v4.2.1.wsp"):
        data = archive.read_bytes()
        for match in re.finditer(rb"scripts/[A-Za-z0-9_./-]+\.lua\x00", data):
            start = match.end()
            if start + 9 >= len(data) or data[start] != 1:
                continue
            compressed, size = struct.unpack_from("<II", data, start + 1)
            try:
                payload = zlib.decompress(data[start + 9:start + 9 + compressed])
            except zlib.error:
                continue
            if len(payload) == size:
                result[match[0][:-1].decode()] = payload
    return result


def run_lua(source):
    lua = ctypes.CDLL(str(GAME / "lua51.dll"))
    lua.luaL_newstate.restype = ctypes.c_void_p
    lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
    lua.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    lua.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lua.lua_tolstring.restype = ctypes.c_char_p
    lua.lua_close.argtypes = [ctypes.c_void_p]
    state = lua.luaL_newstate()
    try:
        lua.luaL_openlibs(state)
        encoded = source.encode()
        status = lua.luaL_loadbuffer(state, encoded, len(encoded), b"inspector")
        if not status:
            status = lua.lua_pcall(state, 0, 0, 0)
        if status:
            raise RuntimeError(lua.lua_tolstring(state, -1, None).decode(errors="replace"))
    finally:
        lua.lua_close(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pattern")
    parser.add_argument("--code", action="store_true")
    args = parser.parse_args()
    for name, payload in resources().items():
        if args.pattern.casefold() not in name.casefold():
            continue
        print(name, flush=True)
        literal = '"' + "".join("\\%03d" % c for c in payload) + '"'
        run_lua("""
local util = require "jit.util"
local bit = require "bit"
local function dump(f, indent)
    local info = util.funcinfo(f)
    print(indent .. "PROTO params=" .. info.params .. " slots=" .. info.stackslots)
    for i = 0, info.gcconsts - 1 do
        local k = util.funck(f, -i-1)
        if type(k) == "proto" then
            print(indent .. "K" .. i)
            dump(k, indent .. "  ")
        elseif type(k) == "table" then
            print(indent .. "K" .. i .. " TABLE")
            for key, value in pairs(k) do print(indent .. "  " .. tostring(key) .. "=" .. tostring(value)) end
        else
            print(indent .. "K" .. i .. " " .. tostring(k))
        end
    end
    for i = 0, info.nconsts - 1 do print(indent .. "N" .. i .. " " .. tostring(util.funck(f, i))) end
""" + ("""
    for pc=1,info.bytecodes-1 do
        local ins, mode = util.funcbc(f, pc)
        print(string.format("%s%03d op=%02d A=%d B=%d C=%d D=%d", indent, pc,
            bit.band(ins,255), bit.band(bit.rshift(ins,8),255), bit.rshift(ins,24),
            bit.band(bit.rshift(ins,16),255), bit.rshift(ins,16)))
    end
""" if args.code else "") + """
end
dump(assert(loadstring(""" + literal + """)), "")
""")
