// Ngày lễ Việt Nam (dương + âm lịch) và sự kiện riêng đặt trên web: đếm ngược.
#pragma once
#include <time.h>
#include "lunar.h"
#include "settings.h"

struct Upcoming {
  char name[40];
  int days;            // 0 = hôm nay
  int day, month, year;   // ngày dương lịch
  bool custom;         // sự kiện người dùng tự thêm
};

// Tên ngày lễ / sự kiện rơi đúng hôm nay, nullptr nếu không có
const char* holidayToday(const struct tm& today, const LunarDate& lunar, const Settings& s);

// Các sự kiện sắp tới, gần nhất trước. Trả về số phần tử đã ghi.
int upcomingEvents(const struct tm& today, const Settings& s, Upcoming* out, int maxOut);
