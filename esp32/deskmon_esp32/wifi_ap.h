// WiFi cài đặt do ESP32 tự phát khi không vào được WiFi nhà: điện thoại nối vào, trang
// cài đặt tự bật lên (captive portal) để chọn WiFi. Bảo vệ bằng mật khẩu hiện trên màn hình,
// vì web không còn mật khẩu: ai nối được vào đây đều đổi được WiFi và nạp được firmware.
#pragma once
#include <Arduino.h>

void apStart();
void apStop();
void apLoop();             // trả lời DNS: mọi tên miền -> ESP32
bool apActive();
const char* apSsid();
const char* apPass();
