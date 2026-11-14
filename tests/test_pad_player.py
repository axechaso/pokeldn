"""Run the actual macro player on a threaded host to exercise STOP/LOAD during long holds."""
import os
import subprocess
from pathlib import Path

from pad_compile import compile_c


def test_long_holds_stop_reload_and_replay_without_an_old_player_resuming(tmp_path):
    root = Path(__file__).resolve().parents[1]
    headers = tmp_path / "freertos"
    headers.mkdir()
    if os.name == "nt":
        (tmp_path / "pthread.h").write_bytes((root / "tests/fixtures/pad_win_pthread.h").read_bytes())
    (tmp_path / "host_rtos.h").write_text(r'''
#pragma once
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stddef.h>
typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef struct Queue *QueueHandle_t;
typedef pthread_mutex_t portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED PTHREAD_MUTEX_INITIALIZER
#define taskENTER_CRITICAL(lock) pthread_mutex_lock(lock)
#define taskEXIT_CRITICAL(lock) pthread_mutex_unlock(lock)
#define portMAX_DELAY UINT32_MAX
#define pdTRUE 1
#define pdPASS 1
#define pdMS_TO_TICKS(ms) (ms)
#define configASSERT(expr) assert(expr)
#define RTC_NOINIT_ATTR
#define ESP_RST_POWERON 1
typedef struct { char version[32]; } esp_app_desc_t;
const esp_app_desc_t *esp_app_get_description(void);
int esp_reset_reason(void);
QueueHandle_t xQueueCreate(unsigned length, unsigned size);
int xQueueSend(QueueHandle_t queue, const void *item, TickType_t wait);
int xQueueReceive(QueueHandle_t queue, void *item, TickType_t wait);
int xTaskCreate(void (*fn)(void *), const char *name, unsigned stack, void *arg, unsigned prio, void *handle);
TickType_t xTaskGetTickCount(void);
void vTaskDelay(TickType_t ticks);
''', encoding="utf-8")
    for path in ("freertos/FreeRTOS.h", "freertos/task.h", "freertos/queue.h",
                 "esp_app_desc.h", "esp_attr.h", "esp_system.h"):
        (tmp_path / path).write_text('#include "host_rtos.h"\n', encoding="utf-8")
    binary = tmp_path / ("player-test.exe" if os.name == "nt" else "player-test")
    compile_c(binary, [root / "firmware/pad/main/player.c", root / "tests/fixtures/pad_player_host.c"],
              includes=(tmp_path, root / "firmware/pad/main"), threaded=True)
    subprocess.run([str(binary)], check=True, timeout=10)
