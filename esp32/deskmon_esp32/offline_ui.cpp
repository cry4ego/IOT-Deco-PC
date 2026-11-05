#include "offline_ui.h"
#include "fonts_vi.h"
#include "lunar.h"
#include <WiFi.h>
#include <math.h>
#include <stdio.h>

namespace {

using Dev = lgfx::LGFX_Device;

constexpr uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
  return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

// Bảng màu giống giao diện PC (deskmon.py)
constexpr uint16_t BG = rgb(11, 14, 20);
constexpr uint16_t FG = rgb(241, 245, 249);
constexpr uint16_t DIM = rgb(139, 147, 167);
constexpr uint16_t FAINT = rgb(82, 91, 112);
constexpr uint16_t TRACK = rgb(34, 39, 51);
constexpr uint16_t ACCENT = rgb(56, 189, 248);
constexpr uint16_t ACCENT_BG = rgb(16, 62, 92);   // nền ô "hôm nay"
constexpr uint16_t SUNDAY = rgb(248, 113, 113);
constexpr uint16_t HOT = rgb(248, 113, 113);
constexpr uint16_t SUN_C = rgb(251, 191, 36);
constexpr uint16_t MOON_C = rgb(226, 232, 240);
constexpr uint16_t CLOUD_C = rgb(203, 213, 225);
constexpr uint16_t CLOUD_DARK = rgb(100, 116, 139);
constexpr uint16_t RAIN_C = rgb(56, 189, 248);

const char* const WEEKDAY_LONG[7] = {"Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư",
                                     "Thứ Năm", "Thứ Sáu", "Thứ Bảy"};
const char* const WEEKDAY_SHORT[7] = {"CN", "T2", "T3", "T4", "T5", "T6", "T7"};

// Vị trí trên màn 2.0"
constexpr int CLOCK_CY = 92;
constexpr int CLOCK_TOP = 40;
constexpr int CLOCK_H = 104;

void drawText(Dev& d, const uint8_t* font, const char* s, int x, int y, textdatum_t datum,
              uint16_t fg, uint16_t bg) {
  d.loadFont(font);
  d.setTextDatum(datum);
  d.setTextColor(fg, bg);
  d.drawString(s, x, y);
}

int measure(Dev& d, const uint8_t* font, const char* s) {
  d.loadFont(font);
  return d.textWidth(s);
}

// Ngày cách `base` một số ngày. Giờ "địa phương" được lưu như UTC (TZ = UTC0),
// nên mktime/gmtime_r không bị lệch múi giờ.
struct tm shiftDays(const struct tm& base, int delta) {
  struct tm t = base;
  t.tm_mday += delta;
  t.tm_hour = 12;
  t.tm_min = t.tm_sec = 0;
  t.tm_isdst = 0;
  time_t e = mktime(&t);
  struct tm out;
  gmtime_r(&e, &out);
  return out;
}

int daysInMonth(int month0, int year) {
  static const int DAYS[12] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  bool leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
  return (month0 == 1 && leap) ? 29 : DAYS[month0];
}

int roundi(float v) { return (int)lroundf(v); }

// Cắt chuỗi UTF-8 cho vừa maxW (thêm "..."), kết quả ghi vào out
void fitText(Dev& d, const uint8_t* font, const char* s, int maxW, char* out, size_t n) {
  strlcpy(out, s, n);
  d.loadFont(font);
  if (d.textWidth(out) <= maxW) return;
  size_t len = strlen(out);
  while (len > 0) {
    do { len--; } while (len > 0 && ((uint8_t)out[len] & 0xC0) == 0x80);
    if (len + 4 > n) continue;
    memcpy(out + len, "...", 4);
    if (d.textWidth(out) <= maxW) return;
  }
  strlcpy(out, "...", n);
}

// Ngắt dòng theo từ, tối đa maxLines dòng (dòng cuối bị cắt "..." nếu thiếu chỗ)
int wrapText(Dev& d, const uint8_t* font, const char* s, int maxW, char lines[][72], int maxLines) {
  d.loadFont(font);
  int count = 0;
  const char* p = s;
  while (*p == ' ') p++;
  while (*p && count < maxLines) {
    const char* end = p;   // cuối phần chữ còn vừa dòng
    const char* q = p;
    while (*q) {
      const char* w = q;
      while (*w && *w != ' ') w++;
      const size_t len = (size_t)(w - p);
      if (len >= 72) break;
      char trial[72];
      memcpy(trial, p, len);
      trial[len] = '\0';
      if (d.textWidth(trial) > maxW) break;
      end = w;
      q = (*w == ' ') ? w + 1 : w;
    }
    if (end == p) {                       // một từ dài hơn cả dòng: cắt cứng
      fitText(d, font, p, maxW, lines[count++], 72);
      break;
    }
    char line[72];
    memcpy(line, p, (size_t)(end - p));
    line[end - p] = '\0';
    const char* rest = end;
    while (*rest == ' ') rest++;
    if (count == maxLines - 1 && *rest) { // hết dòng mà còn chữ -> "..."
      char tail[160];
      snprintf(tail, sizeof(tail), "%s %s", line, rest);
      fitText(d, font, tail, maxW, lines[count++], 72);
      break;
    }
    strlcpy(lines[count++], line, 72);
    p = rest;
  }
  return count;
}

uint16_t aqiColor(int level) {
  static const uint16_t COLORS[6] = {rgb(52, 211, 153), rgb(251, 191, 36), rgb(251, 146, 60),
                                     rgb(248, 113, 113), rgb(167, 139, 250), rgb(190, 18, 60)};
  return COLORS[level < 0 ? 0 : (level > 5 ? 5 : level)];
}

void bellShape(Dev& d, int cx, int cy, int r, uint16_t c) {
  d.fillSmoothCircle(cx, cy - r / 4, r * 6 / 10, c);
  d.fillRect(cx - r * 6 / 10, cy - r / 4, r * 12 / 10, r * 6 / 10, c);
  d.fillSmoothRoundRect(cx - r * 85 / 100, cy + r * 3 / 10, r * 17 / 10, r * 2 / 10 + 2, r / 10 + 1, c);
  d.fillSmoothCircle(cx, cy + r * 62 / 100, r * 16 / 100 + 1, c);
  d.fillSmoothCircle(cx, cy - r * 95 / 100, r * 12 / 100 + 1, c);
}

const uint8_t* clockFont(uint8_t size) {
  if (size == 0) return FONT_CLOCK_S;
  if (size == 2) return FONT_CLOCK_L;
  return FONT_CLOCK;
}

LunarDate lunarOf(const struct tm& t) { return solarToLunar(t.tm_mday, t.tm_mon + 1, t.tm_year + 1900); }

// ---------------------------------------------------------------- biểu tượng thời tiết
void cloudShape(Dev& d, int cx, int cy, int r, uint16_t c) {
  d.fillSmoothCircle(cx - r * 42 / 100, cy + r * 12 / 100, r * 32 / 100, c);
  d.fillSmoothCircle(cx - r * 4 / 100, cy - r * 14 / 100, r * 44 / 100, c);
  d.fillSmoothCircle(cx + r * 42 / 100, cy + r * 10 / 100, r * 34 / 100, c);
  d.fillSmoothRoundRect(cx - r * 74 / 100, cy + r * 4 / 100, r * 150 / 100, r * 40 / 100, r * 20 / 100, c);
}

void sunShape(Dev& d, int cx, int cy, int r) {
  float w = r < 16 ? 1.0f : r * 0.06f;
  for (int i = 0; i < 8; i++) {
    float a = i * (float)M_PI / 4;
    d.drawWideLine(cx + (int)(cosf(a) * r * 0.66f), cy + (int)(sinf(a) * r * 0.66f),
                   cx + (int)(cosf(a) * r * 0.95f), cy + (int)(sinf(a) * r * 0.95f), w, SUN_C);
  }
  d.fillSmoothCircle(cx, cy, r * 48 / 100, SUN_C);
}

void moonShape(Dev& d, int cx, int cy, int r, uint16_t bg) {
  d.fillSmoothCircle(cx, cy, r * 55 / 100, MOON_C);
  d.fillSmoothCircle(cx + r * 28 / 100, cy - r * 20 / 100, r * 48 / 100, bg);
}

void drawIcon(Dev& d, int cx, int cy, int r, WxIcon icon, uint16_t bg) {
  const float line = r < 16 ? 1.0f : r * 0.06f;
  switch (icon) {
    case WxIcon::Sun:
      sunShape(d, cx, cy, r);
      break;
    case WxIcon::Moon:
      moonShape(d, cx, cy, r, bg);
      break;
    case WxIcon::PartSun:
      sunShape(d, cx - r * 28 / 100, cy - r * 26 / 100, r * 62 / 100);
      cloudShape(d, cx + r * 10 / 100, cy + r * 14 / 100, r * 82 / 100, CLOUD_C);
      break;
    case WxIcon::PartMoon:
      moonShape(d, cx - r * 25 / 100, cy - r * 25 / 100, r * 65 / 100, bg);
      cloudShape(d, cx + r * 10 / 100, cy + r * 14 / 100, r * 82 / 100, CLOUD_C);
      break;
    case WxIcon::Cloud:
      cloudShape(d, cx, cy, r, CLOUD_C);
      break;
    case WxIcon::Fog:
      for (int i = -1; i <= 1; i++) {
        int y = cy + i * r * 30 / 100;
        d.drawWideLine(cx - r * 70 / 100, y, cx + r * 70 / 100, y, line * 1.4f, CLOUD_DARK);
      }
      break;
    case WxIcon::Drizzle:
      cloudShape(d, cx, cy - r * 18 / 100, r * 90 / 100, CLOUD_C);
      for (int i = -1; i <= 1; i++) d.fillSmoothCircle(cx + i * r * 32 / 100, cy + r * 58 / 100, r < 16 ? 1 : r * 7 / 100, RAIN_C);
      break;
    case WxIcon::Rain:
      cloudShape(d, cx, cy - r * 18 / 100, r * 90 / 100, CLOUD_C);
      for (int i = -1; i <= 1; i++) {
        int x = cx + i * r * 32 / 100;
        d.drawWideLine(x + r * 6 / 100, cy + r * 38 / 100, x - r * 8 / 100, cy + r * 75 / 100, line, RAIN_C);
      }
      break;
    case WxIcon::Storm:
      cloudShape(d, cx, cy - r * 18 / 100, r * 90 / 100, CLOUD_DARK);
      d.fillTriangle(cx + r * 8 / 100, cy + r * 20 / 100, cx - r * 22 / 100, cy + r * 58 / 100,
                     cx, cy + r * 58 / 100, SUN_C);
      d.fillTriangle(cx - r * 6 / 100, cy + r * 50 / 100, cx + r * 20 / 100, cy + r * 50 / 100,
                     cx - r * 12 / 100, cy + r * 92 / 100, SUN_C);
      break;
    case WxIcon::Snow:
      cloudShape(d, cx, cy - r * 18 / 100, r * 90 / 100, CLOUD_C);
      for (int i = -1; i <= 1; i++) d.fillSmoothCircle(cx + i * r * 32 / 100, cy + r * 60 / 100, r < 16 ? 1 : r * 9 / 100, FG);
      break;
  }
}

// ---------------------------------------------------------------- màn 2.0"
void drawClockDigits(Dev& d, const struct tm& t, uint8_t size) {
  char hm[6];
  snprintf(hm, sizeof(hm), "%02d:%02d", t.tm_hour, t.tm_min);
  const uint8_t* font = clockFont(size);
  const int w = measure(d, font, hm);
  const int x0 = (d.width() - w) / 2;
  drawText(d, font, hm, d.width() / 2, CLOCK_CY, textdatum_t::middle_center, FG, BG);
  // xoá phần thừa hai bên khi bề rộng chữ số thay đổi (không xoá cả vùng -> không nháy)
  d.fillRect(0, CLOCK_TOP, x0, CLOCK_H, BG);
  d.fillRect(x0 + w, CLOCK_TOP, d.width() - x0 - w, CLOCK_H, BG);
}

void drawHeader(Dev& d, const OfflineInfo& in) {
  const struct tm& t = *in.now;
  char buf[48];
  snprintf(buf, sizeof(buf), "%s, %02d/%02d/%04d", WEEKDAY_LONG[t.tm_wday], t.tm_mday, t.tm_mon + 1,
           t.tm_year + 1900);
  drawText(d, FONT_MED, buf, 12, 8, textdatum_t::top_left, FG, BG);

  const int right = d.width() - 12;
  if (!in.wifiOk) {
    drawText(d, FONT_SMALL, in.wifiConfigured ? "Mất Wi-Fi" : "Chưa cài Wi-Fi", right, 12,
             textdatum_t::top_right, HOT, BG);
    return;
  }
  const Weather& w = *in.weather;
  if (!w.valid) return;
  char temp[8];
  snprintf(temp, sizeof(temp), "%d°", roundi(w.temp));
  const int tw = measure(d, FONT_MED, temp);
  drawText(d, FONT_MED, temp, right, 8, textdatum_t::top_right, FG, BG);
  drawIcon(d, right - tw - 18, 20, 14, weatherIcon(w.code, w.isDay), BG);
}

void drawLunarLine(Dev& d, const struct tm& t, const char* holiday) {
  LunarDate ld = lunarOf(t);
  char buf[96];
  if (holiday) {
    char raw[96];
    snprintf(raw, sizeof(raw), "Hôm nay: %s · %d/%d âm", holiday, ld.day, ld.month);
    fitText(d, FONT_SMALL, raw, d.width() - 16, buf, sizeof(buf));
    drawText(d, FONT_SMALL, buf, d.width() / 2, 158, textdatum_t::middle_center, SUN_C, BG);
    return;
  }
  char canChi[24];
  canChiYear(ld.year, canChi, sizeof(canChi));
  snprintf(buf, sizeof(buf), "Âm lịch %d/%d%s · năm %s", ld.day, ld.month, ld.leap ? " nhuận" : "", canChi);
  drawText(d, FONT_SMALL, buf, d.width() / 2, 158, textdatum_t::middle_center, DIM, BG);
}

void drawWeekStrip(Dev& d, const struct tm& today) {
  constexpr int COL_W = 44, TOP = 172, H = 64;
  const int x0 = (d.width() - COL_W * 7) / 2;
  const int todayCol = (today.tm_wday + 6) % 7;   // tuần bắt đầu từ thứ Hai
  for (int i = 0; i < 7; i++) {
    const struct tm day = shiftDays(today, i - todayCol);
    const bool isToday = (i == todayCol);
    const int cx = x0 + i * COL_W + COL_W / 2;
    const uint16_t bg = isToday ? ACCENT_BG : BG;
    if (isToday) d.fillSmoothRoundRect(cx - 20, TOP, 40, H, 8, ACCENT_BG);

    const uint16_t labelColor = isToday ? FG : (day.tm_wday == 0 ? SUNDAY : DIM);
    drawText(d, FONT_SMALL, WEEKDAY_SHORT[day.tm_wday], cx, TOP + 11, textdatum_t::middle_center, labelColor, bg);

    char num[4];
    snprintf(num, sizeof(num), "%d", day.tm_mday);
    drawText(d, FONT_MED, num, cx, TOP + 33, textdatum_t::middle_center, FG, bg);

    const LunarDate ld = lunarOf(day);
    char lunar[8];
    if (ld.day == 1) snprintf(lunar, sizeof(lunar), "%d/%d", ld.day, ld.month);
    else snprintf(lunar, sizeof(lunar), "%d", ld.day);
    const uint16_t lunarColor = (ld.day == 1 || ld.day == 15) ? ACCENT : (isToday ? DIM : FAINT);
    drawText(d, FONT_TINY, lunar, cx, TOP + 54, textdatum_t::middle_center, lunarColor, bg);
  }
}

}  // namespace

// ---------------------------------------------------------------- API
void drawClockScreen(Dev& d, const OfflineInfo& in, bool full) {
  if (full) {
    d.fillScreen(BG);
    drawHeader(d, in);
    drawLunarLine(d, *in.now, in.holiday);
    drawWeekStrip(d, *in.now);
  }
  drawClockDigits(d, *in.now, in.clockSize);
}

void drawWeatherPage(Dev& d, const OfflineInfo& in) {
  const Weather& w = *in.weather;
  const int W = d.width();
  d.fillScreen(BG);
  drawText(d, FONT_SMALL, in.place, 12, 8, textdatum_t::top_left, DIM, BG);

  if (!w.valid) {
    drawIcon(d, W / 2, 92, 40, WxIcon::Cloud, BG);
    drawText(d, FONT_MED, "Chưa có thời tiết", W / 2, 158, textdatum_t::middle_center, FG, BG);
    const char* why = in.wifiOk ? "Đang tải..." : (in.wifiConfigured ? "Mất Wi-Fi" : "Chưa cài Wi-Fi");
    drawText(d, FONT_SMALL, why, W / 2, 186, textdatum_t::middle_center, DIM, BG);
    return;
  }

  char buf[48];
  if (in.air && in.air->valid) {
    const int level = aqiLevel(in.air->aqi);
    const uint16_t pill = aqiColor(level);
    const uint16_t ink = level <= 2 ? BG : FG;
    snprintf(buf, sizeof(buf), "AQI %d · %s", in.air->aqi, aqiText(level));
    const int tw = measure(d, FONT_SMALL, buf);
    const int pw = tw + 16, px = W - 8 - pw;
    d.fillSmoothRoundRect(px, 5, pw, 22, 11, pill);
    drawText(d, FONT_SMALL, buf, px + pw / 2, 16, textdatum_t::middle_center, ink, pill);
  }
  drawIcon(d, 64, 82, 40, weatherIcon(w.code, w.isDay), BG);
  snprintf(buf, sizeof(buf), "%d°", roundi(w.temp));
  drawText(d, FONT_BIG, buf, 122, 82, textdatum_t::middle_left, FG, BG);
  drawText(d, FONT_MED, weatherText(w.code), W / 2, 138, textdatum_t::middle_center, FG, BG);

  const float feels = isnan(w.feels) ? w.temp : w.feels;
  if (w.humidity >= 0) snprintf(buf, sizeof(buf), "Cảm giác %d° · Ẩm %d%%", roundi(feels), w.humidity);
  else snprintf(buf, sizeof(buf), "Cảm giác %d°", roundi(feels));
  drawText(d, FONT_SMALL, buf, W / 2, 162, textdatum_t::middle_center, DIM, BG);
  d.drawFastHLine(12, 177, W - 24, TRACK);

  constexpr int COL_W = 80;
  constexpr int RAIN_SHOW_PCT = 30;
  for (int i = 1; i <= 3; i++) {
    const WeatherDay& day = w.days[i];
    const int cx = (i - 1) * COL_W + COL_W / 2;
    const struct tm date = shiftDays(*in.now, i);
    const char* label = (i == 1) ? "Mai" : WEEKDAY_SHORT[date.tm_wday];
    const bool rainy = day.rainProb >= RAIN_SHOW_PCT;
    if (rainy) snprintf(buf, sizeof(buf), "%s %d%%", label, day.rainProb);
    else snprintf(buf, sizeof(buf), "%s", label);
    drawText(d, FONT_SMALL, buf, cx, 190, textdatum_t::middle_center, rainy ? RAIN_C : DIM, BG);
    drawIcon(d, cx, 208, 12, weatherIcon(day.code, true), BG);
    snprintf(buf, sizeof(buf), "%d°/%d°", roundi(day.tmax), roundi(day.tmin));
    drawText(d, FONT_SMALL, buf, cx, 229, textdatum_t::middle_center, FG, BG);
  }
}

void drawMonthPage(Dev& d, const OfflineInfo& in) {
  const struct tm& t = *in.now;
  const int W = d.width();
  constexpr int COL_W = 34, TOP = 56, ROW_H = 30;
  const int x0 = (W - COL_W * 7) / 2;

  d.fillScreen(BG);
  char buf[32];
  snprintf(buf, sizeof(buf), "Tháng %d / %d", t.tm_mon + 1, t.tm_year + 1900);
  drawText(d, FONT_MED, buf, W / 2, 16, textdatum_t::middle_center, FG, BG);

  static const char* const HEAD[7] = {"T2", "T3", "T4", "T5", "T6", "T7", "CN"};
  for (int c = 0; c < 7; c++) {
    drawText(d, FONT_SMALL, HEAD[c], x0 + c * COL_W + COL_W / 2, 42, textdatum_t::middle_center,
             c == 6 ? SUNDAY : DIM, BG);
  }

  const struct tm first = shiftDays(t, 1 - t.tm_mday);
  const int lead = (first.tm_wday + 6) % 7;
  const int days = daysInMonth(t.tm_mon, t.tm_year + 1900);
  const int todayCell = lead + t.tm_mday - 1;
  {
    const int cx = x0 + (todayCell % 7) * COL_W + COL_W / 2;
    d.fillSmoothRoundRect(cx - 16, TOP + (todayCell / 7) * ROW_H, 32, ROW_H - 2, 6, ACCENT_BG);
  }

  // số dương lịch (một lần nạp phông cho cả tháng)
  d.loadFont(FONT_SMALL);
  d.setTextDatum(textdatum_t::middle_center);
  for (int day = 1; day <= days; day++) {
    const int cell = lead + day - 1;
    const int cx = x0 + (cell % 7) * COL_W + COL_W / 2;
    const int cy = TOP + (cell / 7) * ROW_H;
    const bool isToday = (cell == todayCell);
    d.setTextColor((cell % 7 == 6 && !isToday) ? SUNDAY : FG, isToday ? ACCENT_BG : BG);
    snprintf(buf, sizeof(buf), "%d", day);
    d.drawString(buf, cx, cy + 10);
  }

  // ngày âm lịch nhỏ bên dưới
  d.loadFont(FONT_TINY);
  for (int day = 1; day <= days; day++) {
    const int cell = lead + day - 1;
    const int cx = x0 + (cell % 7) * COL_W + COL_W / 2;
    const int cy = TOP + (cell / 7) * ROW_H;
    const bool isToday = (cell == todayCell);
    const LunarDate ld = solarToLunar(day, t.tm_mon + 1, t.tm_year + 1900);
    if (ld.day == 1) snprintf(buf, sizeof(buf), "%d/%d", ld.day, ld.month);
    else snprintf(buf, sizeof(buf), "%d", ld.day);
    const uint16_t color = (ld.day == 1 || ld.day == 15) ? ACCENT : (isToday ? DIM : FAINT);
    d.setTextColor(color, isToday ? ACCENT_BG : BG);
    d.drawString(buf, cx, cy + 23);
  }
}

void drawEventsPage(Dev& d, const OfflineInfo& in) {
  const int W = d.width();
  constexpr int TOP = 42, ROW_H = 39, MAX_ROWS = 5;
  d.fillScreen(BG);
  drawText(d, FONT_MED, "Sắp tới", 12, 8, textdatum_t::top_left, FG, BG);
  if (in.upcomingCount <= 0) {
    drawText(d, FONT_SMALL, "Chưa có sự kiện", W / 2, 120, textdatum_t::middle_center, DIM, BG);
    return;
  }
  char buf[48];
  for (int i = 0; i < in.upcomingCount && i < MAX_ROWS; i++) {
    const Upcoming& u = in.upcoming[i];
    const int y = TOP + i * ROW_H;
    const bool first = (i == 0);
    const uint16_t bg = first ? ACCENT_BG : BG;
    if (first) d.fillSmoothRoundRect(6, y - 3, W - 12, ROW_H - 3, 8, ACCENT_BG);

    if (u.days == 0) snprintf(buf, sizeof(buf), "Hôm nay");
    else if (u.days == 1) snprintf(buf, sizeof(buf), "Ngày mai");
    else snprintf(buf, sizeof(buf), "%d ngày", u.days);
    const uint8_t* leftFont = u.days <= 1 ? FONT_SMALL : FONT_MED;
    const int leftW = measure(d, leftFont, buf);
    const uint16_t leftColor = u.days == 0 ? SUN_C : (first ? FG : ACCENT);
    drawText(d, leftFont, buf, W - 14, y + ROW_H / 2 - 3, textdatum_t::middle_right, leftColor, bg);

    char name[40];
    fitText(d, FONT_SMALL, u.name, W - 14 - leftW - 26, name, sizeof(name));
    drawText(d, FONT_SMALL, name, 14, y + 1, textdatum_t::top_left, u.custom ? SUN_C : FG, bg);
    snprintf(buf, sizeof(buf), "%02d/%02d/%04d", u.day, u.month, u.year);
    drawText(d, FONT_TINY, buf, 14, y + 21, textdatum_t::top_left, first ? DIM : FAINT, bg);
  }
}

void drawReminderMain(Dev& lyr, const char* timeText, const char* text) {
  const int cx = lyr.width() / 2;
  const uint16_t bg = rgb(24, 30, 48);
  lyr.fillScreen(bg);
  bellShape(lyr, cx, 40, 24, SUN_C);
  drawText(lyr, FONT_SMALL, "Nhắc việc", cx, 78, textdatum_t::middle_center, DIM, bg);
  drawText(lyr, FONT_BIG, timeText, cx, 112, textdatum_t::middle_center, FG, bg);
  char lines[2][72];
  const int n = wrapText(lyr, FONT_MED, (text && text[0]) ? text : "Đến giờ rồi!", lyr.width() - 24, lines, 2);
  for (int i = 0; i < n; i++) {
    drawText(lyr, FONT_MED, lines[i], cx, 158 + i * 26, textdatum_t::middle_center, FG, bg);
  }
  drawText(lyr, FONT_SMALL, "Bấm nút BOOT hoặc tắt trên web", cx, 224, textdatum_t::middle_center, FAINT, bg);
}

void drawReminderFlash(Dev& sta, const char* timeText, bool flash) {
  const uint16_t bg = flash ? SUN_C : BG;
  const uint16_t ink = flash ? BG : SUN_C;
  const int cx = sta.width() / 2;
  sta.fillScreen(bg);
  bellShape(sta, cx, 84, 44, ink);
  drawText(sta, FONT_BIG, timeText, cx, 176, textdatum_t::middle_center, flash ? BG : FG, bg);
}

void drawOtaProgress(Dev& lyr, int percent) {
  const int W = lyr.width();
  constexpr int BAR_X = 40, BAR_Y = 132, BAR_H = 12;
  if (percent <= 0) {
    lyr.fillScreen(BG);
    drawText(lyr, FONT_MED, "Đang cập nhật firmware", W / 2, 92, textdatum_t::middle_center, FG, BG);
    drawText(lyr, FONT_SMALL, "Đừng rút điện", W / 2, 186, textdatum_t::middle_center, DIM, BG);
    lyr.fillSmoothRoundRect(BAR_X, BAR_Y, W - 2 * BAR_X, BAR_H, BAR_H / 2, TRACK);
  }
  const int fill = (W - 2 * BAR_X) * (percent < 0 ? 0 : (percent > 100 ? 100 : percent)) / 100;
  if (fill > BAR_H) lyr.fillSmoothRoundRect(BAR_X, BAR_Y, fill, BAR_H, BAR_H / 2, ACCENT);
  char buf[8];
  snprintf(buf, sizeof(buf), "%d%%", percent);
  lyr.fillRect(W / 2 - 30, 150, 60, 22, BG);
  drawText(lyr, FONT_SMALL, buf, W / 2, 160, textdatum_t::middle_center, DIM, BG);
}

void drawNoTime(Dev& lyr, Dev& sta, const char* status, const char* detail) {
  const int cx = lyr.width() / 2;
  lyr.fillScreen(BG);
  drawText(lyr, FONT_MED, "DeskMon", cx, 92, textdatum_t::middle_center, FG, BG);
  drawText(lyr, FONT_SMALL, status, cx, 132, textdatum_t::middle_center, DIM, BG);
  if (detail && detail[0]) drawText(lyr, FONT_SMALL, detail, cx, 156, textdatum_t::middle_center, FAINT, BG);

  sta.fillScreen(BG);
  sta.fillArc(120, 100, 60, 52, 135, 405, TRACK);
  sta.fillArc(120, 100, 60, 52, 135, 300, ACCENT);
  drawText(sta, FONT_MED, "DeskMon", 120, 100, textdatum_t::middle_center, FG, BG);
  drawText(sta, FONT_SMALL, "R      G      B", 120, 194, textdatum_t::middle_center, DIM, BG);
  sta.fillCircle(84, 215, 6, rgb(255, 0, 0));
  sta.fillCircle(120, 215, 6, rgb(0, 255, 0));
  sta.fillCircle(156, 215, 6, rgb(0, 0, 255));
}

// ---------------------------------------------------------------- tin tức
void drawNewsCard(Dev& d, const NewsCard& card, const struct tm& now) {
  constexpr int HEAD_H = 26, MARGIN = 8, IMG_GAP = 4, MED_LINE = 26, SMALL_LINE = 20, MAX_LINES = 8;
  const int W = d.width(), H = d.height();
  const NewsItem& item = card.item;
  d.fillScreen(BG);

  const char* label = item.world ? "THẾ GIỚI" : "TRONG NƯỚC";
  const uint16_t pill = item.world ? SUN_C : ACCENT;
  const int pw = measure(d, FONT_SMALL, label) + 14;
  d.fillSmoothRoundRect(MARGIN, 3, pw, 20, 10, pill);
  drawText(d, FONT_SMALL, label, MARGIN + pw / 2, 13, textdatum_t::middle_center, BG, pill);
  char head[24];
  snprintf(head, sizeof(head), "VnExpress · %02d:%02d", now.tm_hour, now.tm_min);
  drawText(d, FONT_SMALL, head, W - MARGIN, 13, textdatum_t::middle_right, DIM, BG);

  int y = HEAD_H;
  if (card.jpg && d.drawJpg(card.jpg, card.jpgLen, 0, y, W, newsImageHeight(W))) y += newsImageHeight(W);
  y += IMG_GAP;

  char lines[MAX_LINES][72];
  const int textW = W - 2 * MARGIN;
  const int room = H - y - 2;
  const int maxMed = min(room / MED_LINE, MAX_LINES - 1);
  int n = wrapText(d, FONT_MED, item.title, textW, lines, maxMed + 1);   // +1 để biết có bị dài quá không
  const uint8_t* font = FONT_MED;
  int lineH = MED_LINE;
  if (n > maxMed) {                         // tiêu đề dài: chữ nhỏ hơn để đọc được hết
    font = FONT_SMALL;
    lineH = SMALL_LINE;
    n = wrapText(d, FONT_SMALL, item.title, textW, lines, min(room / SMALL_LINE, MAX_LINES));
  }
  for (int i = 0; i < n; i++) drawText(d, font, lines[i], MARGIN, y + i * lineH, textdatum_t::top_left, FG, BG);
  y += n * lineH + 2;

  const int left = min((H - y) / SMALL_LINE, MAX_LINES);
  if (left <= 0 || !item.summary[0]) return;
  n = wrapText(d, FONT_SMALL, item.summary, textW, lines, left);
  for (int i = 0; i < n; i++) drawText(d, FONT_SMALL, lines[i], MARGIN, y + i * SMALL_LINE, textdatum_t::top_left, DIM, BG);
}

// ---------------------------------------------------------------- WiFi cài đặt
void drawApSteps(Dev& lyr, const char* ssid, const char* pass) {
  constexpr int X = 16;
  char buf[64];
  lyr.fillScreen(BG);
  drawText(lyr, FONT_MED, "Chưa vào được WiFi", X, 16, textdatum_t::top_left, SUN_C, BG);
  drawText(lyr, FONT_SMALL, "Dùng điện thoại quét mã QR ở màn nhỏ,", X, 54, textdatum_t::top_left, DIM, BG);
  drawText(lyr, FONT_SMALL, "hoặc nối vào WiFi này:", X, 74, textdatum_t::top_left, DIM, BG);
  drawText(lyr, FONT_MED, ssid, X, 102, textdatum_t::top_left, FG, BG);
  snprintf(buf, sizeof(buf), "Mật khẩu: %s", pass);
  drawText(lyr, FONT_MED, buf, X, 132, textdatum_t::top_left, FG, BG);
  drawText(lyr, FONT_SMALL, "Trang cài đặt sẽ tự mở. Nếu không, vào:", X, 172, textdatum_t::top_left, DIM, BG);
  snprintf(buf, sizeof(buf), "http://%s", WiFi.softAPIP().toString().c_str());
  drawText(lyr, FONT_MED, buf, X, 194, textdatum_t::top_left, ACCENT, BG);
}

void drawApQr(Dev& sta, const char* ssid, const char* pass) {
  constexpr int QR_SIZE = 164;
  const int W = sta.width();
  char qr[96];
  snprintf(qr, sizeof(qr), "WIFI:T:WPA;S:%s;P:%s;;", ssid, pass);
  sta.fillScreen(BG);
  drawText(sta, FONT_SMALL, "Quét để cài WiFi", W / 2, 11, textdatum_t::middle_center, FG, BG);
  sta.qrcode(qr, (W - QR_SIZE) / 2, 24, QR_SIZE);
  char buf[32];
  snprintf(buf, sizeof(buf), "Mật khẩu: %s", pass);
  drawText(sta, FONT_SMALL, ssid, W / 2, 200, textdatum_t::middle_center, DIM, BG);
  drawText(sta, FONT_SMALL, buf, W / 2, 222, textdatum_t::middle_center, FG, BG);
}
