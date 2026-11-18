"""Run the classic pad's actual input queue with a deterministic 1 kHz clock."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_bluetooth_input_delivery(tmp_path):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    root = Path(__file__).resolve().parents[1]
    src = root / "firmware/pad/main"
    headers = tmp_path / "freertos"
    headers.mkdir()
    (headers / "FreeRTOS.h").write_text("""
        #include <stdint.h>
        typedef uint32_t TickType_t;
        typedef int portMUX_TYPE;
        #define portMUX_INITIALIZER_UNLOCKED 0
        #define pdMS_TO_TICKS(ms) (ms)
        #define taskENTER_CRITICAL(lock) ((void)(lock))
        #define taskEXIT_CRITICAL(lock) ((void)(lock))
    """, encoding="ascii")
    (headers / "task.h").write_text("TickType_t xTaskGetTickCount(void);\n", encoding="ascii")
    exe = tmp_path / ("bt-input.exe" if os.name == "nt" else "bt-input")
    subprocess.run([cc, "-std=c11", "-I", str(tmp_path), "-I", str(src), str(src / "bt_input.c"),
                    str(root / "tests/fixtures/pad_bt_input.c"), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=10)
