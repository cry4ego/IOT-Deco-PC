#include "events.h"
#include <string.h>

namespace {

struct Holiday {
  uint8_t day;
  uint8_t month;
  bool lunar;
  const char* name;
};

const Holiday HOLIDAYS[] = {
  {1, 1, false, "Tết Dương lịch"},
  {14, 2, false, "Lễ Tình nhân"},
  {8, 3, false, "Quốc tế Phụ nữ"},
  {30, 4, false, "Giải phóng miền Nam"},
  {1, 5, false, "Quốc tế Lao động"},
  {1, 6, false, "Quốc tế Thiếu nhi"},
  {2, 9, false, "Quốc khánh"},
  {20, 10, false, "Phụ nữ Việt Nam"},
  {20, 11, false, "Nhà giáo Việt Nam"},
  {25, 12, false, "Giáng sinh"},
  {1, 1, true, "Tết Nguyên Đán"},
  {15, 1, true, "Rằm tháng Giêng"},
  {10, 3, true, "Giỗ Tổ Hùng Vương"},
  {5, 5, true, "Tết Đoan Ngọ"},
  {15, 7, true, "Lễ Vu Lan"},
  {15, 8, true, "Tết Trung thu"},
  {23, 12, true, "Ông Công Ông Táo"},
};

// Lần xảy ra gần nhất (>= hôm nay) của một ngày lặp lại hằng năm
bool nextOccurrence(int day, int month, bool lunar, const struct tm& today, long todayJd,
                    int& dd, int& mm, int& yy, long& jd) {
  const int year = today.tm_year + 1900;
  long best = -1;
  for (int y = year - 1; y <= year + 1; y++) {
    int d, m, yr;
    if (lunar) {
      if (!lunarToSolar(day, month, y, false, d, m, yr)) continue;
    } else {
      d = day;
      m = month;
      yr = y;
    }
    long j = julianDay(d, m, yr);
    if (j >= todayJd && (best < 0 || j < best)) {
      best = j;
      dd = d;
      mm = m;
      yy = yr;
    }
  }
  if (best < 0) return false;
  jd = best;
  return true;
}

void insertSorted(Upcoming* out, int& n, int maxOut, const Upcoming& item) {
  int pos = n;
  while (pos > 0 && out[pos - 1].days > item.days) pos--;
  if (pos >= maxOut) return;
  int last = (n < maxOut) ? n : maxOut - 1;
  for (int i = last; i > pos; i--) out[i] = out[i - 1];
  out[pos] = item;
  if (n < maxOut) n++;
}

}  // namespace

const char* holidayToday(const struct tm& today, const LunarDate& lunar, const Settings& s) {
  const int d = today.tm_mday, m = today.tm_mon + 1;
  for (int i = 0; i < s.eventCount; i++) {
    const EventItem& e = s.events[i];
    if (e.lunar ? (!lunar.leap && e.day == lunar.day && e.month == lunar.month) : (e.day == d && e.month == m)) {
      return e.name;
    }
  }
  for (const Holiday& h : HOLIDAYS) {
    if (h.lunar ? (!lunar.leap && h.day == lunar.day && h.month == lunar.month) : (h.day == d && h.month == m)) {
      return h.name;
    }
  }
  return nullptr;
}

int upcomingEvents(const struct tm& today, const Settings& s, Upcoming* out, int maxOut) {
  const long todayJd = julianDay(today.tm_mday, today.tm_mon + 1, today.tm_year + 1900);
  int n = 0;
  auto consider = [&](int day, int month, bool lunar, const char* name, bool custom) {
    Upcoming u{};
    long jd;
    if (!nextOccurrence(day, month, lunar, today, todayJd, u.day, u.month, u.year, jd)) return;
    strlcpy(u.name, name, sizeof(u.name));
    u.days = (int)(jd - todayJd);
    u.custom = custom;
    insertSorted(out, n, maxOut, u);
  };
  for (int i = 0; i < s.eventCount; i++) {
    consider(s.events[i].day, s.events[i].month, s.events[i].lunar, s.events[i].name, true);
  }
  for (const Holiday& h : HOLIDAYS) consider(h.day, h.month, h.lunar, h.name, false);
  return n;
}
