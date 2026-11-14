// WiFi, mDNS và trang web nhỏ (trạng thái + nạp firmware) của màn hình phụ.
// Giữ tên deskmon.local và trang /update giống DeskMon, để từ dự án cũ nạp lại DeskMon qua WiFi được.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

struct NetHooks {
  void (*status)(JsonObject out);
  void (*otaProgress)(int percent);         // -1 = lỗi
  bool (*deskmonWritten)();                 // đã nạp vào vùng còn lại: true nếu đúng là DeskMon
};

void netBegin(const NetHooks& hooks);       // kết nối WiFi (không chờ)
void netLoop();
bool netWifiOk();
String netIp();
