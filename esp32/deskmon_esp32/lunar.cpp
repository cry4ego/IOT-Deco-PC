// Đổi dương lịch -> âm lịch Việt Nam. Port từ thuật toán của Hồ Ngọc Đức
// (https://www.informatik.uni-leipzig.de/~duc/amlich/), múi giờ cố định +7.
// Đã đối chiếu với Tết 2024/2025/2026, tháng 6 nhuận 2025, Trung thu 2025.
#include "lunar.h"
#include <math.h>
#include <stdio.h>

namespace {

constexpr double TZ = 7.0;
constexpr double PI_D = 3.14159265358979323846;

long jdFromDate(int dd, int mm, int yy) {
  long a = (14 - mm) / 12;
  long y = yy + 4800 - a;
  long m = mm + 12 * a - 3;
  long jd = dd + (153 * m + 2) / 5 + 365 * y + y / 4 - y / 100 + y / 400 - 32045;
  if (jd < 2299161) jd = dd + (153 * m + 2) / 5 + 365 * y + y / 4 - 32083;
  return jd;
}

long newMoonDay(long k) {
  double T = k / 1236.85, T2 = T * T, T3 = T2 * T, dr = PI_D / 180;
  double jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * T2 - 0.000000155 * T3;
  jd1 += 0.00033 * sin((166.56 + 132.87 * T - 0.009173 * T2) * dr);
  double M = 359.2242 + 29.10535608 * k - 0.0000333 * T2 - 0.00000347 * T3;
  double Mpr = 306.0253 + 385.81691806 * k + 0.0107306 * T2 + 0.00001236 * T3;
  double F = 21.2964 + 390.67050646 * k - 0.0016528 * T2 - 0.00000239 * T3;
  double C1 = (0.1734 - 0.000393 * T) * sin(M * dr) + 0.0021 * sin(2 * dr * M);
  C1 = C1 - 0.4068 * sin(Mpr * dr) + 0.0161 * sin(dr * 2 * Mpr);
  C1 = C1 - 0.0004 * sin(dr * 3 * Mpr);
  C1 = C1 + 0.0104 * sin(dr * 2 * F) - 0.0051 * sin(dr * (M + Mpr));
  C1 = C1 - 0.0074 * sin(dr * (M - Mpr)) + 0.0004 * sin(dr * (2 * F + M));
  C1 = C1 - 0.0004 * sin(dr * (2 * F - M)) - 0.0006 * sin(dr * (2 * F + Mpr));
  C1 = C1 + 0.0010 * sin(dr * (2 * F - Mpr)) + 0.0005 * sin(dr * (2 * Mpr + M));
  double deltat = (T < -11)
      ? 0.001 + 0.000839 * T + 0.0002261 * T2 - 0.00000845 * T3 - 0.000000081 * T * T3
      : -0.000278 + 0.000265 * T + 0.000262 * T2;
  return (long)floor(jd1 + C1 - deltat + 0.5 + TZ / 24);
}

int sunLongitude(long jdn) {
  double T = (jdn - 2451545.5 - TZ / 24) / 36525, T2 = T * T, dr = PI_D / 180;
  double M = 357.52910 + 35999.05030 * T - 0.0001559 * T2 - 0.00000048 * T * T2;
  double L0 = 280.46645 + 36000.76983 * T + 0.0003032 * T2;
  double DL = (1.914600 - 0.004817 * T - 0.000014 * T2) * sin(dr * M);
  DL += (0.019993 - 0.000101 * T) * sin(dr * 2 * M) + 0.000290 * sin(dr * 3 * M);
  double L = (L0 + DL) * dr;
  L -= PI_D * 2 * floor(L / (PI_D * 2));
  return (int)floor(L / PI_D * 6);
}

long lunarMonth11(int yy) {
  long off = jdFromDate(31, 12, yy) - 2415021;
  long k = (long)floor(off / 29.530588853);
  long nm = newMoonDay(k);
  if (sunLongitude(nm) >= 9) nm = newMoonDay(k - 1);
  return nm;
}

int leapMonthOffset(long a11) {
  long k = (long)floor((a11 - 2415021.076998695) / 29.530588853 + 0.5);
  int i = 1;
  int arc = sunLongitude(newMoonDay(k + i));
  int last;
  do {
    last = arc;
    i++;
    arc = sunLongitude(newMoonDay(k + i));
  } while (arc != last && i < 14);
  return i - 1;
}

void jdToDate(long jd, int& dd, int& mm, int& yy) {
  long b, c;
  if (jd > 2299160) {
    long a = jd + 32044;
    b = (4 * a + 3) / 146097;
    c = a - (b * 146097) / 4;
  } else {
    b = 0;
    c = jd + 32082;
  }
  long d = (4 * c + 3) / 1461;
  long e = c - (1461 * d) / 4;
  long m = (5 * e + 2) / 153;
  dd = (int)(e - (153 * m + 2) / 5 + 1);
  mm = (int)(m + 3 - 12 * (m / 10));
  yy = (int)(b * 100 + d - 4800 + m / 10);
}

}  // namespace

long julianDay(int dd, int mm, int yy) { return jdFromDate(dd, mm, yy); }

bool lunarToSolar(int lunarDay, int lunarMonth, int lunarYear, bool leap, int& dd, int& mm, int& yy) {
  long a11, b11;
  if (lunarMonth < 11) {
    a11 = lunarMonth11(lunarYear - 1);
    b11 = lunarMonth11(lunarYear);
  } else {
    a11 = lunarMonth11(lunarYear);
    b11 = lunarMonth11(lunarYear + 1);
  }
  long k = (long)floor(0.5 + (a11 - 2415021.076998695) / 29.530588853);
  int off = lunarMonth - 11;
  if (off < 0) off += 12;
  if (b11 - a11 > 365) {
    int leapOff = leapMonthOffset(a11);
    int leapMonth = leapOff - 2;
    if (leapMonth < 0) leapMonth += 12;
    if (leap && lunarMonth != leapMonth) return false;
    if (leap || off >= leapOff) off += 1;
  } else if (leap) {
    return false;
  }
  long monthStart = newMoonDay(k + off);
  jdToDate(monthStart + lunarDay - 1, dd, mm, yy);
  return true;
}

LunarDate solarToLunar(int dd, int mm, int yy) {
  long dayNumber = jdFromDate(dd, mm, yy);
  long k = (long)floor((dayNumber - 2415021.076998695) / 29.530588853);
  long monthStart = newMoonDay(k + 1);
  if (monthStart > dayNumber) monthStart = newMoonDay(k);

  long a11 = lunarMonth11(yy);
  long b11 = a11;
  int lunarYear;
  if (a11 >= monthStart) {
    lunarYear = yy;
    a11 = lunarMonth11(yy - 1);
  } else {
    lunarYear = yy + 1;
    b11 = lunarMonth11(yy + 1);
  }

  int diff = (int)floor((monthStart - a11) / 29.0);
  int lunarMonth = diff + 11;
  bool leap = false;
  if (b11 - a11 > 365) {
    int leapDiff = leapMonthOffset(a11);
    if (diff >= leapDiff) {
      lunarMonth = diff + 10;
      leap = (diff == leapDiff);
    }
  }
  if (lunarMonth > 12) lunarMonth -= 12;
  if (lunarMonth >= 11 && diff < 4) lunarYear -= 1;

  return LunarDate{(int)(dayNumber - monthStart + 1), lunarMonth, lunarYear, leap};
}

void canChiYear(int lunarYear, char* out, size_t n) {
  static const char* const CAN[10] = {"Giáp", "Ất", "Bính", "Đinh", "Mậu",
                                      "Kỷ", "Canh", "Tân", "Nhâm", "Quý"};
  static const char* const CHI[12] = {"Tý", "Sửu", "Dần", "Mão", "Thìn", "Tỵ",
                                      "Ngọ", "Mùi", "Thân", "Dậu", "Tuất", "Hợi"};
  snprintf(out, n, "%s %s", CAN[(lunarYear + 6) % 10], CHI[(lunarYear + 8) % 12]);
}
