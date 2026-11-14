"""Small host compiler adapter for the firmware's executable C regression tests."""
import shutil
import subprocess

import pytest


def compile_c(output, sources, includes=(), exports=(), threaded=False):
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc:
        args = [cc, "-std=c11", "-O1"]
        if exports:
            args += ["-shared", "-fPIC"]
        if threaded:
            args += ["-D_POSIX_C_SOURCE=200809L", "-pthread", "-Wall", "-Wextra",
                     "-Wno-unused-parameter", "-Werror"]
        for path in includes:
            args += ["-I", str(path)]
        args += [*map(str, sources), "-o", str(output)]
    elif cl := shutil.which("cl"):
        args = [cl, "/nologo", "/std:c11", "/D_CRT_SECURE_NO_WARNINGS"]
        if exports:
            args += ["/LD"]
        for path in includes:
            args += [f"/I{path}"]
        args += [*map(str, sources), f"/Fe:{output}", "/link", *[f"/EXPORT:{s}" for s in exports]]
    else:
        pytest.skip("no host C compiler (cc/gcc or an activated MSVC environment)")
    subprocess.run(args, cwd=output.parent, check=True)
