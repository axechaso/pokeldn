"""Run the classic pad's actual input queue with a deterministic 1 kHz clock."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("fixture,player", [("pad_bt_input.c", False), ("pad_idle_stop.c", True)])
def test_bluetooth_input_delivery(tmp_path, fixture, player):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    root = Path(__file__).resolve().parents[1]
    src = root / "firmware/pad/main"
    headers = tmp_path / "freertos"
    headers.mkdir()
    (headers / "FreeRTOS.h").write_text("""
        #pragma once
        #include <stdint.h>
        typedef uint32_t TickType_t;
        typedef int portMUX_TYPE;
        typedef int BaseType_t;
        typedef void *QueueHandle_t;
        #include <assert.h>
        #define configASSERT(x) assert(x)
        #define pdTRUE 1
        #define pdPASS 1
        #define portMAX_DELAY UINT32_MAX
        #define portMUX_INITIALIZER_UNLOCKED 0
        #define pdMS_TO_TICKS(ms) (ms)
        #define taskENTER_CRITICAL(lock) ((void)(lock))
        #define taskEXIT_CRITICAL(lock) ((void)(lock))
    """, encoding="ascii")
    (headers / "task.h").write_text("""
        TickType_t xTaskGetTickCount(void);
        void vTaskDelay(TickType_t);
        BaseType_t xTaskCreate(void (*)(void *), const char *, unsigned, void *, unsigned, void *);
    """, encoding="ascii")
    (headers / "queue.h").write_text("""
        QueueHandle_t xQueueCreate(unsigned, unsigned);
        BaseType_t xQueueSend(QueueHandle_t, const void *, TickType_t);
        BaseType_t xQueueReceive(QueueHandle_t, void *, TickType_t);
    """, encoding="ascii")
    (tmp_path / "esp_app_desc.h").write_text("""
        typedef struct { char version[32]; } esp_app_desc_t;
        const esp_app_desc_t *esp_app_get_description(void);
    """, encoding="ascii")
    (tmp_path / "esp_system.h").write_text("#define ESP_RST_POWERON 1\nint esp_reset_reason(void);\n", encoding="ascii")
    (tmp_path / "esp_attr.h").write_text("#define RTC_NOINIT_ATTR\n", encoding="ascii")
    exe = tmp_path / ("bt-input.exe" if os.name == "nt" else "bt-input")
    sources = [src / "bt_input.c", root / "tests/fixtures" / fixture]
    if player:
        sources.append(src / "player.c")
    subprocess.run([cc, "-std=gnu11", "-DCONFIG_IDF_TARGET_ESP32=1", "-I", str(tmp_path), "-I", str(src),
                    *map(str, sources), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=10)
