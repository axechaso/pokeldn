idf_build_get_property(idf_target IDF_TARGET)
if(NOT idf_target STREQUAL "esp32")
    message(FATAL_ERROR "this controller firmware requires a classic ESP32 with Bluetooth Classic")
endif()

idf_component_register(SRCS "player.c" "bt_esp32.c" "uart_link.c" "pro_report.c" INCLUDE_DIRS "."
                       PRIV_REQUIRES bt nvs_flash esp_timer esp_app_format esp_driver_gpio esp_driver_uart)
