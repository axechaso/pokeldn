// Host-only shims for the unmodified BlueRetro receiver; firmware does not include this file.
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#ifdef _MSC_VER
#define __packed
#pragma pack(push, 1)
#else
#define __packed __attribute__((packed))
#endif
#include "hidp.h"
#define BT_MAX_DEV 1
#define BT_HCI_H4_HDR_SIZE 1
#define BT_HCI_ACL_HDR_SIZE 4
#define BT_SUBTYPE_DEFAULT 0
#define BT_SW_RIGHT_JOYCON 2
#define BT_SW 4
#define BT_DEV_CALIB_SET 1
#define BT_DEV_HID_INIT_DONE 2
#define FB_TYPE_RUMBLE 1
#define TESTS_CMDS_LOG(...) ((void)0)
#define bt_mon_log(...) ((void)0)
#define data_dump(...) ((void)0)
#define atomic_test_bit(flags, bit) ((*(flags) & (1 << (bit))) != 0)
#define atomic_set_bit(flags, bit) (*(flags) |= (1 << (bit)))

struct bt_ids { int32_t id; uint32_t out_idx, subtype; unsigned long report_type; int32_t type; };
struct bt_dev {
    int flags;
    struct bt_ids ids;
    uint16_t acl_handle;
    uint32_t hid_state;
    void *timer_hdl;
    uint8_t tid, hid_retry_cnt;
    struct { uint16_t dcid; } intr_chan;
};
struct bt_l2cap_hdr { uint16_t len, cid; };
struct bt_hci_pkt {
    struct { uint8_t code; } sig_hdr;
    struct bt_hidp_hdr hidp_hdr;
    uint8_t hidp_data[1024];
};
struct bt_data { struct { uint8_t output[128]; struct bt_ids *pids; } base; };
struct bt_host_adapter { struct bt_data data[BT_MAX_DEV]; };
extern struct bt_host_adapter bt_adapter;
extern struct bt_hci_pkt bt_hci_pkt_tmp;
void bt_type_update(int32_t id, int32_t type, uint32_t subtype);
void bt_host_bridge(struct bt_dev *device, uint8_t report, uint8_t *data, uint32_t len);
int32_t bt_host_get_dev_from_id(uint8_t id, struct bt_dev **device);

typedef void *esp_timer_handle_t;
typedef struct { void (*callback)(void *); void *arg; const char *name; } esp_timer_create_args_t;
int esp_timer_create(const esp_timer_create_args_t *args, esp_timer_handle_t *timer);
int esp_timer_start_once(esp_timer_handle_t timer, uint64_t us);
int esp_timer_restart(esp_timer_handle_t timer, uint64_t us);
int esp_timer_delete(esp_timer_handle_t timer);
