// Cài đặt người dùng: chỉnh trên web (http://deskmon.local), lưu dạng JSON trong NVS.
// Thêm tiện ích mới: thêm trường vào Settings + đọc/ghi trong settings.cpp + ô nhập trong web_page.h.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

constexpr int MAX_EVENTS = 8;
constexpr int MAX_REMINDERS = 8;

struct EventItem {
  char name[40];
  uint8_t day;
  uint8_t month;
  bool lunar;        // ngày âm lịch
};

struct Reminder {
  uint8_t hour;
  uint8_t minute;
  uint8_t days;      // bit 0 = CN, bit 1 = T2 ... bit 6 = T7
  bool on;
  char text[64];
};

struct Settings {
  // đồng hồ & màn hình khi PC tắt
  uint8_t clockSize = 1;     // 0 nhỏ, 1 vừa, 2 lớn
  uint8_t brightDay = 200;
  uint8_t brightNight = 30;
  uint8_t nightFrom = 23;
  uint8_t nightTo = 7;
  uint8_t pageSec = 15;      // màn 1.54" đổi trang
  // thời tiết & không khí
  char place[40] = "Hà Nội";
  float lat = 21.0285f;
  float lon = 105.8542f;
  uint8_t weatherMin = 15;
  bool aqi = true;
  // sự kiện đếm ngược & nhắc việc
  uint8_t eventCount = 0;
  EventItem events[MAX_EVENTS] = {};
  uint8_t reminderCount = 0;
  Reminder reminders[MAX_REMINDERS] = {};
  // phía PC (deskmon.py đọc qua /api/pc)
  float lyricsLead = 0.35f;
  uint8_t hotCpu = 85;
  uint8_t hotGpu = 85;
  bool pcExtraPage = true;   // trang ổ đĩa & mạng trên màn 1.54"
  uint8_t pcPageSec = 10;
  // tin tức. Màn hiển thị: bit 0 = 2.0", bit 1 = 1.54" (0 = không hiện, 3 = cả hai)
  bool newsOn = true;
  uint8_t newsOffline = 3;   // khi PC tắt
  uint8_t newsOnline = 3;    // khi PC bật
  bool newsDomestic = true;
  bool newsWorld = true;
  uint8_t newsItemSec = 10;  // mỗi tin hiện bao lâu
  uint8_t newsGapSec = 60;   // giữa hai lượt tin, màn trở về nội dung thường. 0 = chỉ hiện tin
  uint8_t newsRefreshMin = 5;
};

void settingsBegin();                                   // nạp từ NVS (gọi trong setup)
const Settings& settings();
uint32_t settingsVersion();                             // tăng mỗi lần lưu
bool settingsUpdate(JsonVariantConst in, String& err);  // gộp JSON (thiếu trường = giữ nguyên) rồi lưu
void settingsToJson(JsonObject out);
void pcSettingsToJson(JsonObject out);                  // phần deskmon.py cần

// WiFi đặt từ web (ghi đè secrets.h). false = chưa đặt.
bool wifiOverride(String& ssid, String& pass);
void saveWifiOverride(const char* ssid, const char* pass);

// Mật khẩu WiFi cài đặt do ESP32 phát (tạo ngẫu nhiên lần đầu, hiện trên màn hình)
String apPassword();
