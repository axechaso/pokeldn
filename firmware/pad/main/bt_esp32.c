// The classic ESP32 transport: emulate a Pro Controller accepted by a BlueRetro receiver or Switch.
// The computer's input/macros arrive on UART0 (docs/hardware_pad.md).
// Protocol: dekuNukem Nintendo_Switch_Reverse_Engineering (bluetooth_hid_notes, subcommands, spi_flash).
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "nvs_flash.h"
#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_gap_bt_api.h"
#include "esp_hidd_api.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_log.h"
#include "player.h"
#include "uart_link.h"
#include "pro_report.h"

#define LED_GPIO 2 // the DevKit's blue LED, lit while paired
#define PAYLOAD PRO_PAYLOAD_LEN // 0x21 and 0x30 after the report ID

static const char *TAG = "pad"; // compiled out at CONFIG_LOG_DEFAULT_LEVEL_NONE

// Boot stages (pad_stage): 1 nvs, 2 controller, 3 bluedroid, 4 hid init, 5 app registered,
// 6 opened by a host, 7 paired (player lights set)

// The Pro Controller's Bluetooth report descriptor: input 0x21, 0x30 (48 bytes), 0x31-0x33, 0x3F;
// output 0x01, 0x10-0x12 (nxbt sdp/switch-controller.xml, attribute 0x0206).
static uint8_t descriptor[] = {
    0x05, 0x01, 0x09, 0x05, 0xa1, 0x01, 0x06, 0x01, 0xff, 0x85, 0x21, 0x09,
    0x21, 0x75, 0x08, 0x95, 0x30, 0x81, 0x02, 0x85, 0x30, 0x09, 0x30, 0x75,
    0x08, 0x95, 0x30, 0x81, 0x02, 0x85, 0x31, 0x09, 0x31, 0x75, 0x08, 0x96,
    0x69, 0x01, 0x81, 0x02, 0x85, 0x32, 0x09, 0x32, 0x75, 0x08, 0x96, 0x69,
    0x01, 0x81, 0x02, 0x85, 0x33, 0x09, 0x33, 0x75, 0x08, 0x96, 0x69, 0x01,
    0x81, 0x02, 0x85, 0x3f, 0x05, 0x09, 0x19, 0x01, 0x29, 0x10, 0x15, 0x00,
    0x25, 0x01, 0x75, 0x01, 0x95, 0x10, 0x81, 0x02, 0x05, 0x01, 0x09, 0x39,
    0x15, 0x00, 0x25, 0x07, 0x75, 0x04, 0x95, 0x01, 0x81, 0x42, 0x05, 0x09,
    0x75, 0x04, 0x95, 0x01, 0x81, 0x01, 0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
    0x09, 0x33, 0x09, 0x34, 0x16, 0x00, 0x00, 0x27, 0xff, 0xff, 0x00, 0x00,
    0x75, 0x10, 0x95, 0x04, 0x81, 0x02, 0x06, 0x01, 0xff, 0x85, 0x01, 0x09,
    0x01, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02, 0x85, 0x10, 0x09, 0x10, 0x75,
    0x08, 0x95, 0x30, 0x91, 0x02, 0x85, 0x11, 0x09, 0x11, 0x75, 0x08, 0x95,
    0x30, 0x91, 0x02, 0x85, 0x12, 0x09, 0x12, 0x75, 0x08, 0x95, 0x30, 0x91,
    0x02, 0xc0};

static volatile bool open_, paired;
static uint8_t timer;
static uint8_t mac[6];
static QueueHandle_t replies; // 48-byte 0x21 payloads, sent ahead of 0x30
static QueueHandle_t pairing_result;

// The 12 bytes every input report starts with, from the shared HORI-format state.
static void fill_state(uint8_t *p)
{
    uint8_t r[REPORT_LEN];
    pad_get_report(r);
    pro_state(r, timer++, p);
}

static void subcommand(const uint8_t *d, uint16_t len)
{
    uint8_t p[PAYLOAD], report[REPORT_LEN];
    pad_get_report(report);
    if (!pro_reply(d, len, mac, report, timer++, p))
        return;
    uint8_t sub = d[9];
    ESP_LOGI(TAG, "sub %02x len %u", sub, len);
    if (sub == 0x30) { // both BlueRetro and Switch finish setup by assigning player lights
        paired = true;
        pad_stage(7);
        gpio_set_level(LED_GPIO, 1);
    }
    xQueueSend(replies, p, 0);
}

// Pacing after friendmaker's Switch Lite profile (classic_bt_controller_transport.cpp, SWITCH_LITE):
// a 15 ms 0x30 stream congests the link once it enters sniff, and the Switch then drops it.
#define IDLE_MS 100      // an unchanged 0x30; before the 0x03 mode switch too, or the Switch never starts the handshake
#define OPEN_QUIET_MS 1000
#define SNIFF_QUIET_MS 250
#define CONGESTED_MS 45
static volatile TickType_t quiet_until;

static void quiet_for(uint32_t ms)
{
    TickType_t until = xTaskGetTickCount() + pdMS_TO_TICKS(ms);
    if ((int32_t)(until - quiet_until) > 0)
        quiet_until = until;
}

static void sender_task(void *arg)
{
    uint8_t p[PAYLOAD], sent[9] = {0};
    TickType_t last = 0;
    for (;;) {
        // A reply goes first; 0x30 waits while the Switch still sets the controller up.
        if (xQueueReceive(replies, p, pdMS_TO_TICKS(5)) == pdTRUE) {
            if (open_)
                esp_bt_hid_device_send_report(ESP_HIDD_REPORT_TYPE_INTRDATA, 0x21, PAYLOAD, p);
            last = xTaskGetTickCount();
            continue;
        }
        TickType_t now = xTaskGetTickCount();
        if (!open_ || (int32_t)(quiet_until - now) > 0)
            continue;
        // BlueRetro builds can omit SET_REP_MODE. Keep streaming native 0x30 reports without
        // waiting for 0x03, as BlueRetro accepts this report type in either initialization path.
        memset(p, 0, sizeof(p));
        fill_state(p);
        bool changed = memcmp(sent, p + 2, sizeof(sent)) != 0;
        if (!changed && now - last < pdMS_TO_TICKS(IDLE_MS))
            continue;
        if (changed)
            ESP_LOGI(TAG, "0x30 buttons %02x %02x %02x", p[2], p[3], p[4]);
        memcpy(sent, p + 2, sizeof(sent));
        esp_bt_hid_device_send_report(ESP_HIDD_REPORT_TYPE_INTRDATA, 0x30, PAYLOAD, p);
        last = now;
    }
}

// ---- Bluetooth ----

static esp_hidd_app_param_t app = {
    .name = PRO_DEVICE_NAME,
    .description = "Gamepad",
    .provider = "Nintendo",
    .subclass = 0x08,
    .desc_list = descriptor,
    .desc_list_len = sizeof(descriptor),
};
static esp_hidd_qos_param_t qos = {0};

static void discoverable(bool on)
{
    esp_bt_gap_set_scan_mode(on ? ESP_BT_CONNECTABLE : ESP_BT_NON_CONNECTABLE,
                             on ? ESP_BT_GENERAL_DISCOVERABLE : ESP_BT_NON_DISCOVERABLE);
}

static void hidd_cb(esp_hidd_cb_event_t event, esp_hidd_cb_param_t *param)
{
    static uint32_t sent, failed;
    if (event == ESP_HIDD_SEND_REPORT_EVT) {
        if (param->send_report.status != ESP_HIDD_SUCCESS && param->send_report.reason == 8)
            quiet_for(CONGESTED_MS);
        if (param->send_report.status != ESP_HIDD_SUCCESS && failed++ % 50 == 0)
            ESP_LOGW(TAG, "send %02x failed status %d reason %d (%lu ok)", param->send_report.report_id,
                     param->send_report.status, param->send_report.reason, (unsigned long)sent);
        else if (param->send_report.status == ESP_HIDD_SUCCESS && sent++ % 500 == 0)
            ESP_LOGI(TAG, "sent %lu reports, %lu failed", (unsigned long)sent, (unsigned long)failed);
    } else if (event == ESP_HIDD_INTR_DATA_EVT) {
        if (param->intr_data.report_id != 0x10)
            ESP_LOGI(TAG, "out report %02x len %u", param->intr_data.report_id, param->intr_data.len);
    } else {
        ESP_LOGI(TAG, "hidd event %d", event);
    }
    switch (event) {
    case ESP_HIDD_INIT_EVT:
        if (param->init.status == ESP_HIDD_SUCCESS)
            esp_bt_hid_device_register_app(&app, &qos, &qos);
        break;
    case ESP_HIDD_REGISTER_APP_EVT: {
        pad_stage(5);
        esp_bt_cod_t cod = {.service = 1, .major = 5, .minor = 2}; // 0x002508, a gamepad
        esp_bt_gap_set_cod(cod, ESP_BT_SET_COD_ALL);
        discoverable(true);
        if (param->register_app.in_use) // reconnect to the last receiver/console
            esp_bt_hid_device_connect(param->register_app.bd_addr);
        break;
    }
    case ESP_HIDD_OPEN_EVT:
        ESP_LOGI(TAG, "open status %d conn %d", param->open.status, param->open.conn_status);
        if (param->open.conn_status == ESP_HIDD_CONN_STATE_CONNECTED) {
            pad_stage(6);
            quiet_for(OPEN_QUIET_MS);
            discoverable(false);
            xQueueReset(replies);
            paired = false;
            open_ = true;
        }
        break;
    case ESP_HIDD_CLOSE_EVT:
        ESP_LOGI(TAG, "close status %d conn %d", param->close.status, param->close.conn_status);
        open_ = paired = false;
        xQueueReset(replies);
        gpio_set_level(LED_GPIO, 0);
        pad_disconnected();
        discoverable(true);
        break;
    case ESP_HIDD_VC_UNPLUG_EVT: {
        if (param->vc_unplug.status == ESP_HIDD_SUCCESS) {
            // A normal disconnect keeps the virtual cable and rejects another host. Unplug
            // removes that binding/bond so a BlueRetro can pair after a previous Switch host.
            open_ = paired = false;
            xQueueReset(replies);
            pad_disconnected();
            gpio_set_level(LED_GPIO, 0);
            pad_stage(5);
            discoverable(true);
        }
        int result = param->vc_unplug.status == ESP_HIDD_SUCCESS ? 0 : PAD_REFUSED;
        xQueueSend(pairing_result, &result, 0);
        break;
    }
    case ESP_HIDD_INTR_DATA_EVT:
        if (param->intr_data.report_id == 0x01)
            subcommand(param->intr_data.data, param->intr_data.len);
        break;
    default:
        break;
    }
}

static void gap_cb(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t *param)
{
    switch (event) {
    case ESP_BT_GAP_AUTH_CMPL_EVT:
        ESP_LOGI(TAG, "auth status %d", param->auth_cmpl.stat);
        break;
    case ESP_BT_GAP_ACL_CONN_CMPL_STAT_EVT:
        ESP_LOGI(TAG, "acl up status %d", param->acl_conn_cmpl_stat.stat);
        break;
    case ESP_BT_GAP_ACL_DISCONN_CMPL_STAT_EVT:
        ESP_LOGI(TAG, "acl down reason 0x%02x", param->acl_disconn_cmpl_stat.reason);
        break;
    case ESP_BT_GAP_MODE_CHG_EVT:
        ESP_LOGI(TAG, "mode %d", param->mode_chg.mode);
        if (param->mode_chg.mode != ESP_BT_PM_MD_ACTIVE)
            quiet_for(SNIFF_QUIET_MS);
        break;
    case ESP_BT_GAP_CFM_REQ_EVT:
        esp_bt_gap_ssp_confirm_reply(param->cfm_req.bda, true);
        break;
    case ESP_BT_GAP_PIN_REQ_EVT: {
        esp_bt_pin_code_t pin = {0};
        if (param->pin_req.min_16_digit) {
            esp_bt_gap_pin_reply(param->pin_req.bda, true, 16, pin);
        } else {
            memcpy(pin, "0000", 4);
            esp_bt_gap_pin_reply(param->pin_req.bda, true, 4, pin);
        }
        break;
    }
    default:
        break;
    }
}

static bool mounted(void)
{
    return paired;
}

int pad_pair(void)
{
    xQueueReset(pairing_result);
    if (esp_bt_hid_device_virtual_cable_unplug() != ESP_OK)
        return PAD_REFUSED;
    int result;
    return xQueueReceive(pairing_result, &result, pdMS_TO_TICKS(3000)) == pdTRUE ? result : PAD_REFUSED;
}

// ROM download mode is entered through BOOT or the serial bridge's DTR/RTS sequence.
void pad_restart(void)
{
    esp_restart();
}

void app_main(void)
{
    pad_init();
    gpio_reset_pin(LED_GPIO);
    gpio_set_direction(LED_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level(LED_GPIO, 0);
    replies = xQueueCreate(8, PAYLOAD);
    pairing_result = xQueueCreate(1, sizeof(int));
    configASSERT(replies && pairing_result);
    uart_link_start(mounted);

    esp_err_t err = nvs_flash_init(); // Bluedroid keeps the Switch's link key here
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    pad_stage(1);
    esp_read_mac(mac, ESP_MAC_BT);

    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_BLE));
    esp_bt_controller_config_t cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bt_controller_init(&cfg));
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_CLASSIC_BT));
    esp_bt_sleep_disable(); // friendmaker: modem sleep breaks pairing with a Switch Lite
    pad_stage(2);
    esp_bluedroid_config_t bcfg = BT_BLUEDROID_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bluedroid_init_with_cfg(&bcfg));
    ESP_ERROR_CHECK(esp_bluedroid_enable());
    pad_stage(3);

    esp_bt_gap_register_callback(gap_cb);
    esp_bt_gap_set_device_name(PRO_DEVICE_NAME);
    esp_bt_io_cap_t iocap = ESP_BT_IO_CAP_NONE;
    esp_bt_gap_set_security_param(ESP_BT_SP_IOCAP_MODE, &iocap, sizeof(iocap));
    esp_bt_pin_code_t pin = {0};
    esp_bt_gap_set_pin(ESP_BT_PIN_TYPE_VARIABLE, 0, pin);
    esp_bt_hid_device_register_callback(hidd_cb);
    ESP_ERROR_CHECK(esp_bt_hid_device_init());
    pad_stage(4);
    xTaskCreate(sender_task, "pad_bt", 4096, NULL, 5, NULL);
}
