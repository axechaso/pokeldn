// Classic HID input history. The UART and macro player can change state between radio polls.
#pragma once
#include "player.h"

bool bt_input_write(const uint8_t *report);
void bt_input_neutral(void);
void bt_input_latest(uint8_t *out);
void bt_input_link(bool up);
void bt_input_sniff(void);
void bt_input_replied(void);
bool bt_input_next(uint8_t *out);
void bt_input_complete(bool accepted);
