"""Run mocked Lua regressions using an existing game LuaJIT DLL."""
import os
from pathlib import Path

from inspect_game import run_lua


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    run_lua("dofile([[tools/trainer_runtime_test.lua]])")
