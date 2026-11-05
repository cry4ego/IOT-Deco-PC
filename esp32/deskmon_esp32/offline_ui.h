// Giao diện chế độ offline (PC tắt): đồng hồ, thời tiết, lịch. ESP32 tự vẽ bằng LovyanGFX.
#pragma once
#ifndef LGFX_USE_V1
#define LGFX_USE_V1
#endif
#include <LovyanGFX.hpp>
#include <time.h>
#include "events.h"
#include "news.h"
#include "weather.h"

struct OfflineInfo {
  const struct tm* now;      // giờ địa phương (khác nullptr)
  const Weather* weather;    // weather->valid = false nếu chưa lấy được
  const AirQuality* air;     // air->valid = false nếu tắt / chưa lấy được
  bool wifiConfigured;
  bool wifiOk;
  const char* place;         // nơi lấy thời tiết, ví dụ "Hà Nội"
  uint8_t clockSize;         // 0 nhỏ, 1 vừa, 2 lớn
  const char* holiday;       // ngày lễ hôm nay, nullptr nếu không có
  const Upcoming* upcoming;  // sự kiện sắp tới (đã sắp xếp)
  int upcomingCount;
};

// Màn 2.0" (320x240): thứ/ngày, giờ:phút, âm lịch, lịch tuần. full=false chỉ vẽ lại giờ:phút.
void drawClockScreen(lgfx::LGFX_Device& d, const OfflineInfo& in, bool full);

// Màn 1.54" (240x240): các trang luân phiên
void drawWeatherPage(lgfx::LGFX_Device& d, const OfflineInfo& in);
void drawMonthPage(lgfx::LGFX_Device& d, const OfflineInfo& in);
void drawEventsPage(lgfx::LGFX_Device& d, const OfflineInfo& in);

// Nhắc việc: màn 2.0" vẽ một lần, màn 1.54" nhấp nháy (gọi lại với flash đảo)
void drawReminderMain(lgfx::LGFX_Device& lyr, const char* timeText, const char* text);
void drawReminderFlash(lgfx::LGFX_Device& sta, const char* timeText, bool flash);

// Đang nạp firmware qua WiFi (0..100)
void drawOtaProgress(lgfx::LGFX_Device& lyr, int percent);

// Chưa có giờ (chưa có WiFi/NTP và PC chưa gửi giờ). Màn 1.54" hiện logo + 3 chấm R G B.
void drawNoTime(lgfx::LGFX_Device& lyr, lgfx::LGFX_Device& sta, const char* status, const char* detail);

// Thẻ tin: nhãn + giờ, ảnh bài báo (nếu có), tiêu đề, tóm tắt
constexpr int newsImageHeight(int screenW) { return screenW >= 320 ? 124 : 104; }
void drawNewsCard(lgfx::LGFX_Device& d, const NewsCard& card, const struct tm& now);

// Đang phát WiFi cài đặt: màn 2.0" hiện các bước (khi chưa có giờ), màn 1.54" hiện mã QR để nối vào
void drawApSteps(lgfx::LGFX_Device& lyr, const char* ssid, const char* pass);
void drawApQr(lgfx::LGFX_Device& sta, const char* ssid, const char* pass);
