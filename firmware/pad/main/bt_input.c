#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "bt_input.h"

// All producers and the Bluedroid callbacks share this lock. No stack calls or waits inside it.
#define CAPACITY 32
struct change {
    uint8_t report[REPORT_LEN];
    TickType_t at;
};
static portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;
static struct change changes[CAPACITY];
static uint32_t head, count, flight_head;
static uint8_t latest[REPORT_LEN];
static bool online, in_flight, flight_is_change, delivered;
static TickType_t last_input, last_change, last_packet;
static TickType_t pause_start, pause_ticks, sniff_start, sniff_ticks;

static void append(TickType_t now)
{
    struct change *c = &changes[(head + count) % CAPACITY];
    memcpy(c->report, latest, REPORT_LEN);
    c->at = now;
    count++;
}

static void clear_history(void)
{
    head += count + 1;  // a completion outstanding at STOP must not consume its replacement
    count = 0;
    delivered = false;
}

bool bt_input_write(const uint8_t *r)
{
    bool accepted = true;
    taskENTER_CRITICAL(&lock);
    if (memcmp(r, latest, REPORT_LEN - 1) != 0) {  // vendor byte has no Pro Controller mapping
        accepted = !online || count < CAPACITY;
        if (accepted) {
            memcpy(latest, r, REPORT_LEN);
            if (online)
                append(xTaskGetTickCount());
        }
    }
    taskEXIT_CRITICAL(&lock);
    return accepted;
}

void bt_input_neutral(void)
{
    taskENTER_CRITICAL(&lock);
    clear_history();
    memcpy(latest, pad_neutral, REPORT_LEN);
    if (online)
        append(xTaskGetTickCount());
    taskEXIT_CRITICAL(&lock);
}

void bt_input_latest(uint8_t *out)
{
    taskENTER_CRITICAL(&lock);
    memcpy(out, latest, REPORT_LEN);
    taskEXIT_CRITICAL(&lock);
}

void bt_input_link(bool up)
{
    taskENTER_CRITICAL(&lock);
    online = up;
    clear_history();
    in_flight = false;
    sniff_ticks = 0;
    if (up) {
        TickType_t now = xTaskGetTickCount();
        append(now);
        pause_start = now;
        pause_ticks = pdMS_TO_TICKS(1000);  // original Switch Lite link setup window
    }
    taskEXIT_CRITICAL(&lock);
}

void bt_input_sniff(void)
{
    taskENTER_CRITICAL(&lock);
    sniff_start = xTaskGetTickCount();
    sniff_ticks = pdMS_TO_TICKS(250);
    taskEXIT_CRITICAL(&lock);
}

void bt_input_replied(void)
{
    taskENTER_CRITICAL(&lock);
    last_packet = xTaskGetTickCount();
    taskEXIT_CRITICAL(&lock);
}

static bool prepare(uint8_t *out, TickType_t now)
{
    if (!online || in_flight || now - pause_start < pause_ticks)
        return false;
    pause_ticks = 0;
    if (now - sniff_start < sniff_ticks)
        return false;
    sniff_ticks = 0;
    if (count) {
        const struct change *c = &changes[head % CAPACITY];
        if (delivered) {
            // Normal input follows its arrival time. When catching up, allow 10 ms for
            // polling/completion jitter so a sustained 30 Hz stream does not accumulate lag.
            // Even a burst retains 20 ms between states, and a delayed 50 ms tap retains 40.
            TickType_t gap = c->at - last_input;
            if (gap > pdMS_TO_TICKS(50))
                gap = pdMS_TO_TICKS(50);
            TickType_t dwell = gap > pdMS_TO_TICKS(30) ? gap - pdMS_TO_TICKS(10) : pdMS_TO_TICKS(20);
            if (now - last_change < dwell)
                return false;
        }
        memcpy(out, c->report, REPORT_LEN);
    } else {
        if (now - last_packet < pdMS_TO_TICKS(100))
            return false;
        memcpy(out, latest, REPORT_LEN);
    }
    flight_head = head;
    flight_is_change = count != 0;
    in_flight = true;
    return true;
}

bool bt_input_next(uint8_t *out)
{
    taskENTER_CRITICAL(&lock);
    bool ready = prepare(out, xTaskGetTickCount());
    taskEXIT_CRITICAL(&lock);
    return ready;
}

void bt_input_complete(bool accepted)
{
    taskENTER_CRITICAL(&lock);
    if (in_flight) {
        TickType_t now = xTaskGetTickCount();
        in_flight = false;
        if (accepted) {
            last_packet = now;
            if (flight_is_change && flight_head == head && count) {
                last_change = now;
                last_input = changes[head % CAPACITY].at;
                head++;
                count--;
                delivered = true;
            }
        } else {
            pause_start = now;
            pause_ticks = pdMS_TO_TICKS(45);  // retain the refused head through the stock backoff
        }
    }
    taskEXIT_CRITICAL(&lock);
}
