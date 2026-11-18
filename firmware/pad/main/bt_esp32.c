// The classic ESP32 transport: a Pro Controller over Bluetooth Classic HID, paired from the Switch's
// Change Grip/Order, with the host on UART0 (docs/hardware_pad.md, The Bluetooth Classic side).
// Protocol: dekuNukem Nintendo_Switch_Reverse_Engineering (bluetooth_hid_notes, subcommands, spi_flash).
#include <string.h>
#include <stdlib.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "nvs_flash.h"
#include "nvs.h"
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
#include "bt_input.h"

#define LED_GPIO 2
#define BOOT_GPIO 0
#define PAYLOAD 48 // 0x21 and 0x30 after the report ID

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

static atomic_bool open_, paired, recovering;
static atomic_uint session;
static uint8_t timer;
static uint8_t mac[6];
struct reply_frame {
    unsigned link;
    uint8_t payload[PAYLOAD];
};
static QueueHandle_t replies;
static QueueHandle_t completions;
struct completion {
    uint8_t id;
    bool ok;
};

static portMUX_TYPE peer_lock = portMUX_INITIALIZER_UNLOCKED;
static uint8_t peer[6];
static bool registered, known_peer, forget_pending;
static TickType_t last_attempt, retry_delay, opened_at;

static void reply(uint8_t ack, uint8_t sub, const uint8_t *data, size_t len)
{
    struct reply_frame response = {.link = session};
    uint8_t *p = response.payload;
    p[12] = ack;
    p[13] = sub;
    if (len > PAYLOAD - 14)
        len = PAYLOAD - 14;
    if (len)
        memcpy(p + 14, data, len);
    xQueueSend(replies, &response, 0);
}

static void subcommand(const uint8_t *d, uint16_t len)
{
    if (len < 10)
        return;
    uint8_t sub = d[9];
    const uint8_t *arg = d + 10;
    switch (sub) {
    case 0x02: { // device info: firmware 4.00, Pro Controller, MAC, colours from SPI
        uint8_t info[12] = {0x04, 0x00, 0x03, 0x02, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], 0x01, 0x01};
        reply(0x82, sub, info, sizeof(info));
        break;
    }
    case 0x10: { // SPI flash read: address u32, length u8
        if (len < 15)
            return;
        uint32_t a = arg[0] | arg[1] << 8 | arg[2] << 16 | (uint32_t)arg[3] << 24;
        uint8_t n = arg[4] > 0x1D ? 0x1D : arg[4];
        uint8_t data[5 + 0x1D];
        memcpy(data, arg, 5);
        for (uint8_t i = 0; i < n; i++)
            data[5 + i] = pro_spi_byte(a + i);
        reply(0x90, sub, data, 5 + n);
        break;
    }
    case 0x03: // input report mode; 0x30 streams full reports
        reply(0x80, sub, NULL, 0);
        break;
    case 0x04: { // trigger buttons elapsed time, 10 ms units: L and R held 3 s
        static const uint8_t t[14] = {0x2C, 0x01, 0x2C, 0x01};
        reply(0x83, sub, t, sizeof(t));
        break;
    }
    case 0x21: { // NFC/IR MCU configuration (joycontrol protocol.py)
        uint8_t m[34] = {0x01, 0x00, 0xFF, 0x00, 0x08, 0x00, 0x1B, 0x01};
        m[33] = 0xC8;
        reply(0xA0, sub, m, sizeof(m));
        break;
    }
    default: // shipment state, IMU, vibration, HOME light, MCU state and the rest: a plain ACK
        reply(0x80, sub, NULL, 0);
    }
}

// Some IDF builds restore a GAP bond without the HID virtual-cable flag. With multiple
// bonds, only page a receiver that completed the player-light handshake on this board.
static bool restore_peer(esp_bd_addr_t *address)
{
    int n = esp_bt_gap_get_bond_device_num();
    if (n == 1)
        return esp_bt_gap_get_bond_device_list(&n, address) == ESP_OK && n == 1;
    nvs_handle_t storage;
    if (n < 1 || nvs_open("pad_bt", NVS_READONLY, &storage) != ESP_OK)
        return false;
    size_t len = sizeof(esp_bd_addr_t);
    esp_err_t err = nvs_get_blob(storage, "last_host", *address, &len);
    nvs_close(storage);
    if (err != ESP_OK || len != sizeof(esp_bd_addr_t))
        return false;
    esp_bd_addr_t *bonds = calloc(n, sizeof(*bonds));
    bool found = false;
    if (bonds && esp_bt_gap_get_bond_device_list(&n, bonds) == ESP_OK) {
        for (int i = 0; i < n && !found; i++)
            found = memcmp(bonds[i], *address, len) == 0;
    }
    free(bonds);
    return found;
}

static void remember_peer(const uint8_t *address)
{
    nvs_handle_t storage;
    if (nvs_open("pad_bt", NVS_READWRITE, &storage) != ESP_OK)
        return;
    esp_bd_addr_t saved;
    size_t len = sizeof(saved);
    if (address && nvs_get_blob(storage, "last_host", saved, &len) == ESP_OK &&
        len == sizeof(saved) && memcmp(saved, address, len) == 0) {
        nvs_close(storage);
        return;
    }
    esp_err_t err = address ? nvs_set_blob(storage, "last_host", address, sizeof(saved))
                            : nvs_erase_key(storage, "last_host");
    if (err == ESP_OK)
        nvs_commit(storage);
    nvs_close(storage);
}

// Keep a discovery window between pages. BOOT retries the remembered host without erasing its key.
static void maintain_link(void)
{
    static bool button_down;
    static int last_led = -1;
    bool pressed = gpio_get_level(BOOT_GPIO) == 0;
    bool manual = pressed && !button_down;
    button_down = pressed;
    uint8_t address[6];
    bool connect = false, disconnect = false;
    taskENTER_CRITICAL(&peer_lock);
    TickType_t now = xTaskGetTickCount();
    bool up = open_, ready = paired && !recovering;
    bool forget = forget_pending;
    forget_pending = false;
    if (up && ((!ready && now - opened_at >= pdMS_TO_TICKS(12000)) || recovering)) {
        if (!recovering || now - last_attempt >= pdMS_TO_TICKS(3000)) {
            recovering = true;
            last_attempt = now;
            disconnect = true;
        }
    } else if (!up && registered && known_peer && (manual || now - last_attempt >= retry_delay)) {
        memcpy(address, peer, sizeof(peer));
        last_attempt = now;
        retry_delay = pdMS_TO_TICKS(15000);  // allow the stack to finish an outstanding page
        connect = true;
    }
    int led = registered && (ready || ((now / pdMS_TO_TICKS(up ? 125 : 500)) & 1));
    taskEXIT_CRITICAL(&peer_lock);
    if (forget)
        remember_peer(NULL);
    if (led != last_led) {
        gpio_set_level(LED_GPIO, led);
        last_led = led;
    }
    if (disconnect)
        esp_bt_hid_device_disconnect();
    if (connect && esp_bt_hid_device_connect(address) != ESP_OK) {
        taskENTER_CRITICAL(&peer_lock);
        retry_delay = pdMS_TO_TICKS(3000);
        taskEXIT_CRITICAL(&peer_lock);
    }
}

// A successful API return only posts a request. Serialize replies and inputs so that even an
// ID-less Bluedroid error belongs to exactly one submission. CLOSE wakes the wait.
static bool send_packet(uint8_t id, uint8_t *p, unsigned link)
{
    struct completion result;
    xQueueReset(completions);
    if (link != session || !open_ || recovering ||
        esp_bt_hid_device_send_report(ESP_HIDD_REPORT_TYPE_INTRDATA, id, PAYLOAD, p) != ESP_OK)
        return false;
    if (xQueueReceive(completions, &result, pdMS_TO_TICKS(2000)) != pdTRUE) {
        // Reopen the session before issuing another report whose callback could be ambiguous.
        if (link == session && open_)
            recovering = true;
        return false;
    }
    return link == session && result.id == id && result.ok;
}

static void sender_task(void *arg)
{
    uint8_t p[PAYLOAD], r[REPORT_LEN];
    struct reply_frame response;
    bool retry_reply = false;
    for (;;) {
        maintain_link();
        if (retry_reply && response.link != session)
            retry_reply = false;
        if (!open_ || recovering) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        if (retry_reply || xQueueReceive(replies, &response, pdMS_TO_TICKS(3)) == pdTRUE) {
            if (response.link != session)
                continue;
            pad_get_report(r);
            pro_state(r, timer++, response.payload);
            retry_reply = !send_packet(0x21, response.payload, response.link);
            if (retry_reply)
                vTaskDelay(pdMS_TO_TICKS(15));
            else if (response.payload[13] == 0x30) {
                esp_bd_addr_t address;
                bool first = false;
                taskENTER_CRITICAL(&peer_lock);
                if (response.link == session && open_ && !paired) {
                    paired = true;
                    first = true;
                    memcpy(address, peer, sizeof(peer));
                    pad_stage(7);
                }
                taskEXIT_CRITICAL(&peer_lock);
                if (first)
                    remember_peer(address);
            }
        } else {
            unsigned link = session;
            if (!bt_input_next(r))
                continue;
            memset(p, 0, sizeof(p));
            pro_state(r, timer++, p);
            bt_input_complete(send_packet(0x30, p, link));
        }
    }
}

// ---- Bluetooth ----

static esp_hidd_app_param_t app = {
    .name = "Wireless Gamepad",
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

static void close_link(bool forget_peer)
{
    taskENTER_CRITICAL(&peer_lock);
    open_ = paired = recovering = false;
    session++;
    last_attempt = xTaskGetTickCount();
    retry_delay = pdMS_TO_TICKS(3000);
    if (forget_peer) {
        known_peer = false;
        forget_pending = true;
    }
    taskEXIT_CRITICAL(&peer_lock);
    bt_input_link(false);
    xQueueReset(replies);
    struct completion closed = {0};
    xQueueOverwrite(completions, &closed);
    pad_disconnected();
    pad_stage(5);
    discoverable(true);
}

static void hidd_cb(esp_hidd_cb_event_t event, esp_hidd_cb_param_t *param)
{
    static uint32_t sent, failed;
    if (event == ESP_HIDD_SEND_REPORT_EVT) {
        struct completion result = {param->send_report.report_id, param->send_report.status == ESP_HIDD_SUCCESS};
        xQueueOverwrite(completions, &result);
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
        if (param->register_app.status != ESP_HIDD_SUCCESS)
            break;
        esp_bd_addr_t address;
        bool have_address = param->register_app.in_use;
        if (have_address)
            memcpy(address, param->register_app.bd_addr, sizeof(address));
        else
            have_address = restore_peer(&address);
        taskENTER_CRITICAL(&peer_lock);
        registered = true;
        known_peer = have_address;
        if (known_peer)
            memcpy(peer, address, sizeof(peer));
        retry_delay = 0;
        taskEXIT_CRITICAL(&peer_lock);
        pad_stage(5);
        esp_bt_cod_t cod = {.service = 1, .major = 5, .minor = 2}; // 0x002508, a gamepad
        esp_bt_gap_set_cod(cod, ESP_BT_SET_COD_ALL);
        discoverable(true);
        break;
    }
    case ESP_HIDD_OPEN_EVT:
        ESP_LOGI(TAG, "open status %d conn %d", param->open.status, param->open.conn_status);
        if (param->open.status == ESP_HIDD_SUCCESS && param->open.conn_status == ESP_HIDD_CONN_STATE_CONNECTED) {
            bt_input_link(true);
            taskENTER_CRITICAL(&peer_lock);
            known_peer = true;
            memcpy(peer, param->open.bd_addr, sizeof(peer));
            opened_at = xTaskGetTickCount();
            session++;
            recovering = paired = false;
            open_ = true;
            taskEXIT_CRITICAL(&peer_lock);
            pad_stage(6);
            discoverable(false);
        } else if (param->open.conn_status == ESP_HIDD_CONN_STATE_DISCONNECTED && !open_) {
            taskENTER_CRITICAL(&peer_lock);
            last_attempt = xTaskGetTickCount();
            retry_delay = pdMS_TO_TICKS(3000);
            taskEXIT_CRITICAL(&peer_lock);
        }
        break;
    case ESP_HIDD_CLOSE_EVT:
        ESP_LOGI(TAG, "close status %d conn %d", param->close.status, param->close.conn_status);
        if ((param->close.status == ESP_HIDD_SUCCESS || param->close.status == ESP_HIDD_NO_CONNECTION) &&
            param->close.conn_status == ESP_HIDD_CONN_STATE_DISCONNECTED)
            close_link(false);
        break;
    case ESP_HIDD_VC_UNPLUG_EVT:
        if (param->vc_unplug.status == ESP_HIDD_SUCCESS)
            close_link(true);
        break;
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
            bt_input_sniff();
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
    return paired && !recovering;
}

// Classic boards reset into the ROM loader through their serial chip's DTR/RTS; a restart is enough.
void pad_enter_loader(void)
{
    esp_restart();
}

void app_main(void)
{
    pad_init();
    gpio_reset_pin(LED_GPIO);
    gpio_set_direction(LED_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level(LED_GPIO, 0);
    gpio_reset_pin(BOOT_GPIO);
    gpio_set_direction(BOOT_GPIO, GPIO_MODE_INPUT);
    gpio_set_pull_mode(BOOT_GPIO, GPIO_PULLUP_ONLY);
    replies = xQueueCreate(8, sizeof(struct reply_frame));
    completions = xQueueCreate(1, sizeof(struct completion));
    configASSERT(replies && completions);
    uart_link_start(mounted);

    esp_err_t err = nvs_flash_init(); // Bluedroid keeps the Switch's link key here
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
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
    esp_bt_gap_set_device_name("Pro Controller");
    esp_bt_io_cap_t iocap = ESP_BT_IO_CAP_NONE;
    esp_bt_gap_set_security_param(ESP_BT_SP_IOCAP_MODE, &iocap, sizeof(iocap));
    esp_bt_pin_code_t pin = {0};
    esp_bt_gap_set_pin(ESP_BT_PIN_TYPE_VARIABLE, 0, pin);
    esp_bt_hid_device_register_callback(hidd_cb);
    ESP_ERROR_CHECK(esp_bt_hid_device_init());
    pad_stage(4);
    xTaskCreate(sender_task, "pad_bt", 4096, NULL, 5, NULL);
}
