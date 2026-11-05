// Âm lịch Việt Nam (múi giờ +7), thuật toán của Hồ Ngọc Đức.
#pragma once
#include <stddef.h>

struct LunarDate {
  int day;
  int month;
  int year;
  bool leap;   // tháng nhuận
};

LunarDate solarToLunar(int dd, int mm, int yy);

// Âm -> dương. false nếu không có ngày đó (ví dụ tháng nhuận không tồn tại năm đó).
bool lunarToSolar(int lunarDay, int lunarMonth, int lunarYear, bool leap, int& dd, int& mm, int& yy);

// Số ngày Julius, dùng để tính khoảng cách giữa hai ngày
long julianDay(int dd, int mm, int yy);

// Tên năm can chi, ví dụ 2026 -> "Bính Ngọ"
void canChiYear(int lunarYear, char* out, size_t n);
