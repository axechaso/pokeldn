#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_app_desc.h"
#include "esp_system.h"
#include "player.h"

static int queue_slot;
TickType_t xTaskGetTickCount(void) { return 0; }
void vTaskDelay(TickType_t t) { abort(); }
QueueHandle_t xQueueCreate(unsigned length, unsigned size) { return &queue_slot; }
BaseType_t xTaskCreate(void (*fn)(void *), const char *name, unsigned stack, void *arg, unsigned priority, void *handle)
{
    return pdPASS;  // the idle player never runs during this test
}
BaseType_t xQueueSend(QueueHandle_t queue, const void *value, TickType_t wait) { abort(); }
BaseType_t xQueueReceive(QueueHandle_t queue, void *value, TickType_t wait) { abort(); }
const esp_app_desc_t *esp_app_get_description(void)
{
    static const esp_app_desc_t desc = {"1.3.0-br2"};
    return &desc;
}
int esp_reset_reason(void) { return ESP_RST_POWERON; }
void pad_enter_loader(void) { abort(); }

int main(void)
{
    uint8_t status[64], out[REPORT_LEN], right[REPORT_LEN];
    memcpy(right, pad_neutral, sizeof right);
    right[2] = 2;
    pad_init();
    assert(pad_live_report(right) == 0);
    assert(pad_status(status, sizeof status, false) > 14 && status[0] == 0);
    uint8_t stop = 0x13;
    assert(pad_command(&stop, 1) == 0);
    pad_get_report(out);
    assert(memcmp(out, pad_neutral, sizeof out) == 0);
    assert(pad_live_report(pad_neutral) == 0);
    return 0;
}
