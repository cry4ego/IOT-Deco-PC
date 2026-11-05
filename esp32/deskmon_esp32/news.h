// Tin tức khi PC tắt: một task nền tải RSS (VnExpress) và ảnh bài báo, nên màn hình
// và web không bị đứng trong lúc chờ mạng. Khi PC bật, deskmon.py tự tải và vẽ tin.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

struct NewsItem {
  char title[160];
  char summary[200];
  char image[220];     // URL ảnh gốc, rỗng nếu bài không có ảnh
  bool world;          // false = trong nước
};

struct NewsCard {
  NewsItem item;
  uint8_t* jpg;        // nullptr nếu không tải được ảnh -> vẽ thẻ chỉ có chữ
  size_t jpgLen;
};

struct NewsConfig {
  bool wanted;         // có cần tải tin không (PC tắt, có WiFi, đã bật tin)
  bool domestic;
  bool world;
  uint8_t refreshMin;
};

void newsBegin();                          // gọi 1 lần trong setup()
void newsConfigure(const NewsConfig& cfg); // task nền chỉ được tạo khi lần đầu cần
int newsCount();

// Lấy tin kế tiếp cho từng màn (0 = 2.0", 1 = 1.54"): xin -> task nền tải ảnh -> vẽ -> trả lại.
bool newsRequestCard(int screen, int imgW, int imgH);
const NewsCard* newsCardReady(int screen); // nullptr = chưa xong
void newsCardDone(int screen);

// Một kết nối HTTPS tốn ~45 KB RAM, không đủ cho hai cái cùng lúc. Thời tiết chạy ở loop()
// nên chỉ thử giành, bận thì lần sau lấy, không đứng chờ.
bool newsNetTryLock();
void newsNetUnlock();

void newsToJson(JsonObject out);
