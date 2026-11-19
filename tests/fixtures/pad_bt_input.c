#include <assert.h>
#include <string.h>
#include "bt_input.h"

const uint8_t pad_neutral[REPORT_LEN] = {0, 0, 8, 128, 128, 128, 128, 0};
static const uint8_t right[REPORT_LEN] = {0, 0, 2, 128, 128, 128, 128, 0};
static const uint8_t left[REPORT_LEN] = {0, 0, 6, 128, 128, 128, 128, 0};
static uint32_t now;
uint32_t xTaskGetTickCount(void) { return now; }

static void expect(const uint8_t *report)
{
    uint8_t out[REPORT_LEN];
    assert(bt_input_next(out));
    assert(memcmp(out, report, REPORT_LEN) == 0);
    assert(!bt_input_next(out));  // one outstanding 0x30
}

static void start(void)
{
    bt_input_link(false);
    bt_input_neutral();
    bt_input_link(true);
    now += 1000;
    expect(pad_neutral);
    bt_input_complete(true);
}

int main(void)
{
    uint8_t out[REPORT_LEN];
    now = UINT32_MAX - 1010;
    start();
    now += 100;
    bt_input_sniff();
    assert(!bt_input_next(out));
    assert(bt_input_write(right));
    assert(!bt_input_next(out));
    now += 50;
    assert(bt_input_write(pad_neutral));  // a whole tap falls inside the stock sniff window
    now += 199;
    assert(!bt_input_next(out));
    now++;
    expect(right);
    bt_input_complete(false);
    now += 44;
    assert(!bt_input_next(out));
    now++;
    expect(right);
    bt_input_complete(true);
    now += 39;
    assert(!bt_input_next(out));
    now++;
    expect(pad_neutral);
    bt_input_complete(false);
    now += 45;
    expect(pad_neutral);
    bt_input_complete(true);
    now += 99;
    bt_input_replied();
    now += 99;
    assert(!bt_input_next(out));
    now++;
    expect(pad_neutral);
    bt_input_complete(true);

    // A backlog survives async failures. STOP invalidates an already submitted head.
    for (unsigned i = 0; i < 32; i++)
        assert(bt_input_write(i % 2 ? left : right));
    assert(bt_input_write(left));  // a duplicate needs no slot
    assert(!bt_input_write(right));
    now += 100;
    expect(right);
    bt_input_neutral();
    bt_input_complete(true);
    expect(pad_neutral);
    bt_input_complete(true);
    assert(!bt_input_next(out));

    // Alternating live reports at the EasyCon throttle must not accumulate transport jitter.
    unsigned delivered = 0;
    for (unsigned t = 0; t < 300010; t++, now++) {
        if (t < 300000 && t % 30 == 0)
            assert(bt_input_write(t / 30 % 2 ? left : right));
        if (t % 3 == 0 && bt_input_next(out)) {
            assert(memcmp(out, delivered % 2 ? left : right, REPORT_LEN) == 0);
            assert(t >= delivered * 30 && t - delivered * 30 <= 40);
            delivered++;
        }
        if (t % 3 == 2)
            bt_input_complete(true);
    }
    assert(delivered == 10000);

    assert(bt_input_write(right));
    bt_input_link(false);
    bt_input_neutral();
    assert(!bt_input_next(out));
    bt_input_link(true);
    now += 1000;
    expect(pad_neutral);
    return 0;
}
