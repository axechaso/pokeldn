// Pure Pro Controller encoding (pro_report.c).
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

// Check the bytes consumed by the Bluetooth subcommand handler before it reads any arguments.
bool pro_subcommand_valid(const uint8_t *data, size_t len);

// The first 12 bytes of a 0x21 or 0x30 payload: timer, battery, buttons, sticks, vibrator, from an
// 8-byte HORI-format state (player.h).
void pro_state(const uint8_t *hori, uint8_t timer, uint8_t *out);

// One byte of the SPI flash the Switch reads: calibration, colours; 0xFF elsewhere.
uint8_t pro_spi_byte(uint32_t address);
