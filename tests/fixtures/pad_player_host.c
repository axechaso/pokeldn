// Minimal threaded queue/task host for the real player.c; no player logic is reimplemented here.
#include "host_rtos.h"
#include "player.h"
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

struct Queue {
    pthread_mutex_t lock;
    pthread_cond_t changed;
    bool full;
    unsigned size;
    uint8_t item[1];
};

QueueHandle_t xQueueCreate(unsigned length, unsigned size)
{
    assert(length == 1 && size == 1);
    struct Queue *q = calloc(1, sizeof(*q));
    assert(q);
    pthread_mutex_init(&q->lock, NULL);
    pthread_cond_init(&q->changed, NULL);
    q->size = size;
    return q;
}

int xQueueSend(QueueHandle_t q, const void *item, TickType_t wait)
{
    pthread_mutex_lock(&q->lock);
    while (q->full)
        pthread_cond_wait(&q->changed, &q->lock);
    memcpy(q->item, item, q->size);
    q->full = true;
    pthread_cond_broadcast(&q->changed);
    pthread_mutex_unlock(&q->lock);
    return pdTRUE;
}

int xQueueReceive(QueueHandle_t q, void *item, TickType_t wait)
{
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_sec += wait / 1000;
    until.tv_nsec += (wait % 1000) * 1000000L;
    if (until.tv_nsec >= 1000000000L) {
        until.tv_nsec -= 1000000000L;
        until.tv_sec++;
    }
    pthread_mutex_lock(&q->lock);
    while (!q->full) {
        if (!wait)
            break;
        int rc = wait == portMAX_DELAY ? pthread_cond_wait(&q->changed, &q->lock) :
                 pthread_cond_timedwait(&q->changed, &q->lock, &until);
        if (rc)
            break;
    }
    bool got = q->full;
    if (got) {
        memcpy(item, q->item, q->size);
        q->full = false;
        pthread_cond_broadcast(&q->changed);
    }
    pthread_mutex_unlock(&q->lock);
    return got;
}

struct Task { void (*fn)(void *); void *arg; };
static void *run(void *arg)
{
    struct Task t = *(struct Task *)arg;
    free(arg);
    t.fn(t.arg);
    return NULL;
}

int xTaskCreate(void (*fn)(void *), const char *name, unsigned stack, void *arg, unsigned prio, void *handle)
{
    struct Task *t = malloc(sizeof(*t));
    *t = (struct Task){fn, arg};
    pthread_t thread;
    assert(pthread_create(&thread, NULL, run, t) == 0);
    pthread_detach(thread);
    return pdPASS;
}

TickType_t xTaskGetTickCount(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (TickType_t)((uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000);
}

void vTaskDelay(TickType_t ticks)
{
    struct timespec delay = {ticks / 1000, (ticks % 1000) * 1000000L};
    nanosleep(&delay, NULL);
}

const esp_app_desc_t *esp_app_get_description(void)
{
    static const esp_app_desc_t desc = {"1.3.1"};
    return &desc;
}
int esp_reset_reason(void) { return ESP_RST_POWERON; }
void pad_restart(void) { abort(); }

static void command(uint8_t cmd)
{
    assert(pad_command(&cmd, 1) == 0);
}

static void load(uint8_t button, uint16_t ms)
{
    uint8_t header[] = {0x10, 1, 0, 0, 0, 1, 0, 0, 0};
    assert(pad_command(header, sizeof(header)) == 0);
    uint8_t data[] = {0x11, 0, 0, button, 0, 8, 128, 128, 128, 128, 0,
                      ms & 0xff, ms >> 8};
    assert(pad_command(data, sizeof(data)) == 0);
}

static void wait_button(uint8_t button)
{
    uint8_t report[REPORT_LEN];
    TickType_t start = xTaskGetTickCount();
    do {
        pad_get_report(report);
        if (report[0] == button)
            return;
        vTaskDelay(1);
    } while (xTaskGetTickCount() - start < 500);
    assert(!"player did not reach expected report");
}

int main(void)
{
    pad_init();
    load(4, 60000);
    command(0x12);
    wait_button(4);
    TickType_t start = xTaskGetTickCount();
    command(0x13);
    assert(xTaskGetTickCount() - start < 500);
    wait_button(0);
    assert(!pad_playing());

    // Replacing a sleeping macro must not leave its timer/loop in charge of the new program.
    load(4, 60000);
    command(0x12);
    wait_button(4);
    start = xTaskGetTickCount();
    load(2, 30);
    assert(xTaskGetTickCount() - start < 500);
    command(0x12);
    wait_button(2);
    wait_button(0);
    vTaskDelay(100);
    assert(!pad_playing());

    // Replaying while held restarts from entry zero, and the subsequent STOP still wakes it.
    load(4, 60000);
    command(0x12);
    wait_button(4);
    command(0x12);
    wait_button(4);
    command(0x13);
    wait_button(0);
    return 0;
}
