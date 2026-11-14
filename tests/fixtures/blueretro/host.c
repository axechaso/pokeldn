// Drive the real BlueRetro Nintendo receiver without its ESP32 radio or hardware timers.
#include "host.h"
#include "sw.h"
#include "pro_report.h"
#include <assert.h>
#include <stddef.h>

__typeof__(bt_adapter) bt_adapter;
struct bt_hci_pkt bt_hci_pkt_tmp;
const uint8_t bt_hid_led_dev_id_map[] = {1, 2, 4, 8};
static struct bt_dev device;
static esp_timer_create_args_t timer_args;
static uint8_t requests[16][48], last_report[48];
static unsigned produced, consumed, bridged;
static bool completed;

int esp_timer_create(const esp_timer_create_args_t *args, esp_timer_handle_t *timer)
{
    timer_args = *args;
    *timer = &timer_args;
    return 0;
}
int esp_timer_start_once(esp_timer_handle_t timer, uint64_t us) { return 0; }
int esp_timer_restart(esp_timer_handle_t timer, uint64_t us) { return 0; }
int esp_timer_delete(esp_timer_handle_t timer) { completed = true; return 0; }

int32_t bt_host_get_dev_from_id(uint8_t id, struct bt_dev **out)
{
    *out = id == 0 ? &device : NULL;
    return id == 0 ? 0 : -1;
}
void bt_type_update(int32_t id, int32_t type, uint32_t subtype)
{
    assert(id == 0);
    device.ids.type = type;
    device.ids.subtype = subtype;
}
void bt_host_bridge(struct bt_dev *peer, uint8_t report, uint8_t *data, uint32_t len)
{
    assert(peer == &device && report == 0x30 && len == sizeof(last_report));
    memcpy(last_report, data, sizeof(last_report));
    bridged++;
}
void bt_hid_cmd(uint16_t handle, uint16_t cid, uint8_t hdr, uint8_t report, uint16_t len)
{
    assert(hdr == BT_HIDP_DATA_OUT && report == 0x01 && len == 48);
    assert(produced < 16);
    memcpy(requests[produced++], bt_hci_pkt_tmp.hidp_data, len);
}

const char *blue_name(void) { return PRO_DEVICE_NAME; }
void blue_begin(void)
{
    memset(&device, 0, sizeof(device));
    memset(&bt_adapter, 0, sizeof(bt_adapter));
    produced = consumed = bridged = 0;
    completed = false;
    device.ids.type = BT_SW;
    bt_adapter.data[0].base.pids = &device.ids;
    bt_hid_sw_init(&device);
    timer_args.callback(timer_args.arg);
}
int blue_next(uint8_t *out)
{
    if (consumed == produced)
        return 0;
    memcpy(out, requests[consumed++], 48);
    return 48;
}
void blue_deliver(uint8_t report, const uint8_t *data)
{
    struct bt_hci_pkt packet = {0};
    packet.sig_hdr.code = BT_HIDP_DATA_IN;
    packet.hidp_hdr.protocol = report;
    memcpy(packet.hidp_data, data, 48);
    bt_hid_sw_hdlr(&device, &packet, 48 + BT_HCI_H4_HDR_SIZE + BT_HCI_ACL_HDR_SIZE +
                   sizeof(struct bt_l2cap_hdr) + sizeof(struct bt_hidp_hdr));
}
int blue_ready(void)
{
    return completed && atomic_test_bit(&device.flags, BT_DEV_CALIB_SET);
}
int blue_calibration(uint16_t *out)
{
    struct bt_hid_sw_ctrl_calib *cal = NULL;
    bt_hid_sw_get_calib(0, &cal);
    if (!cal)
        return 0;
    for (unsigned stick = 0; stick < 2; stick++)
        for (unsigned axis = 0; axis < 2; axis++)
            memcpy(out + stick * 6 + axis * 3, cal->sticks[stick].axes[axis].val, 6);
    out[12] = cal->sticks[0].deadzone;
    out[13] = cal->sticks[1].deadzone;
    return 14;
}
unsigned blue_last_report(uint8_t *out)
{
    memcpy(out, last_report, sizeof(last_report));
    return bridged;
}
