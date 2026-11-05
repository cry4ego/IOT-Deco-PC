// Web cấu hình chạy trên ESP32: http://deskmon.local
//   GET  /                 trang cấu hình (web_page.h)
//   GET  /api/config       cài đặt hiện tại      POST /api/config  lưu (JSON, thiếu trường = giữ nguyên)
//   GET  /api/status       trạng thái thiết bị + bài đang phát trên PC
//   GET  /api/news         tin ESP32 đã tải (khi PC tắt)
//   POST /api/media        {"cmd":"play_pause"|"next"|"prev"} -> PC thực hiện
//   POST /api/pc           deskmon.py báo bài đang phát, nhận cài đặt + lệnh nhạc
//   POST /api/reminder/dismiss | /api/reminder/test
//   POST /api/wifi/scan    các mạng WiFi quanh đây
//   POST /api/wifi, /api/ap/test, /api/reboot, /update
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

struct WebHooks {
  void (*status)(JsonObject out);          // thêm trường trạng thái riêng của firmware
  void (*settingsChanged)();
  void (*dismissReminder)();
  void (*testReminder)(const char* text);
  void (*otaProgress)(int percent);        // nạp firmware qua trình duyệt, -1 = lỗi
  void (*testAp)();                        // phát thử WiFi cài đặt trong ít phút
};

void webBegin(const WebHooks& hooks);
void webLoop();
void webSetCaptive(bool on);               // đang phát WiFi cài đặt: địa chỉ lạ -> về trang cài đặt
bool pcConnectedRecently();                // deskmon.py gọi /api/pc trong 10 s gần đây
