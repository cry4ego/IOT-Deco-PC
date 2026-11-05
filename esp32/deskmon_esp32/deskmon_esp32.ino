/*
  DeskMon - firmware ESP32
  ---------------------------------------------------------------
  Màn 2.0"  ST7789 240x320 (xoay ngang 320x240) : lời bài hát
  Màn 1.54" ST7789 240x240                      : theo dõi PC

  PC (deskmon.py) vẽ sẵn giao diện, nén thành QOI/JPEG rồi gửi
  từng vùng thay đổi qua USB-Serial hoặc WiFi (TCP). ESP32 chỉ việc
  giải nén và vẽ lên đúng màn.

  Khi PC tắt (8 s không có gói nào) và có WiFi, ESP32 tự chạy chế độ offline:
    - màn 2.0" : thứ/ngày, đồng hồ (NTP), âm lịch, lịch tuần, nhiệt độ hiện tại
    - màn 1.54": luân phiên thời tiết (Open-Meteo) và lịch tháng có ngày âm
    - tin tức VnExpress kèm ảnh, xen kẽ trên màn chọn ở web (news.cpp, news_view.cpp)
  Không vào được WiFi -> tự phát WiFi cài đặt "DeskMon-Setup" (wifi_ap.cpp).

  Thư viện cần cài (Arduino IDE > Library Manager):
    - LovyanGFX (by lovyan03)
    - ArduinoJson (by Benoit Blanchon), bản 7.x
  Board: "ESP32 Dev Module" (gói esp32 by Espressif)
  WiFi: chép secrets.example.h thành secrets.h rồi điền tên + mật khẩu.

  Giao thức (little-endian):
    'D' 'M' | type u8 | screen u8 | x u16 | y u16 | len u32 | payload
    type: 0=PING 1=JPEG 2=QOI 3=TIME(u32 epoch giờ địa phương) 4=BRIGHTNESS(u8)
          bit 0x80 = gói thuộc lần vẽ lại toàn màn
    ESP32 trả 1 byte: 0x06 ACK, 0x15 lỗi, 0x07 "cần gửi lại toàn màn"
*/
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <time.h>
#include "bootinfo.h"
#include "news.h"
#include "news_view.h"
#include "offline_ui.h"
#include "settings.h"
#include "webui.h"
#include "wifi_ap.h"

// ===================== CẤU HÌNH =====================
// Hầu hết cài đặt chỉnh trên web: http://deskmon.local (lưu trong NVS, xem settings.h).
// WiFi mặc định: secrets.h (mẫu: secrets.example.h). Không có file hoặc không vào được mạng
// -> ESP32 tự phát WiFi cài đặt để chọn mạng từ điện thoại.
#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef DESKMON_WIFI_SSID
#define DESKMON_WIFI_SSID ""
#define DESKMON_WIFI_PASS ""
#endif
const char* FW_VERSION = "2.1.0";
const uint16_t TCP_PORT = 7777;
const char* MDNS_NAME = "deskmon";          // -> deskmon.local

const uint32_t AP_AFTER_MS = 20000;         // chừng này không vào được WiFi nhà -> tự phát WiFi cài đặt
const uint32_t STA_RETRY_MS = 30000;        // đang phát WiFi cài đặt: thử lại WiFi nhà (router vừa bật lại)
const uint32_t AP_TEST_MS = 3UL * 60 * 1000;   // nút "phát thử" trên web

// LovyanGFX chép cả bitmap một chữ lên stack khi vẽ (alloca): chữ số đồng hồ cỡ lớn
// (128 px) chiếm ~6,7 KB, vượt stack mặc định 8 KB của loop() -> ESP32 tự reset.
SET_LOOP_TASK_STACK_SIZE(16 * 1024);

const uint32_t SERIAL_BAUD = 921600;        // phải khớp --baud bên PC

const int32_t TZ_OFFSET_SEC = 7 * 3600;     // giờ Việt Nam (UTC+7, không đổi giờ mùa hè)
const uint32_t WEATHER_RETRY_MS = 60UL * 1000;
const uint32_t REMINDER_SHOW_MS = 5UL * 60 * 1000;
const uint32_t REMINDER_FLASH_MS = 500;
const int BOOT_BUTTON_PIN = 0;              // nút BOOT trên board: tắt nhắc việc
const int UPCOMING_MAX = 6;

// ---- Màn 2.0" (lời bài hát) - bus VSPI ----
#define LYR_SCLK 18      // chân SCL trên màn
#define LYR_MOSI 23      // chân SDA trên màn
#define LYR_CS    5
#define LYR_DC   16
#define LYR_RST  17
#define LYR_ROTATION 1   // 1 hoặc 3 = nằm ngang. Hình bị lộn ngược thì đổi 1 <-> 3
#define LYR_INVERT true  // màu bị âm bản (nền trắng) thì đổi true <-> false
#define LYR_RGB_ORDER false // đỏ và xanh dương bị đảo thì đổi false <-> true

// ---- Màn 1.54" (theo dõi PC) - bus HSPI ----
#define STA_SCLK 14
#define STA_MOSI 13
#define STA_CS   26
#define STA_DC   27
#define STA_RST  33
#define STA_BL   32      // chân BL (đèn nền) - chỉnh độ sáng
#define STA_ROTATION 0   // 0..3
#define STA_INVERT true
#define STA_RGB_ORDER false
#define STA_SPI_MODE 0   // đèn nền sáng mà không hiện hình (đã kiểm tra dây) -> thử 3

#define SPI_FREQ 40000000  // dây cắm dài / hình bị nhiễu -> giảm 27000000 hoặc 20000000
// Chân còn trống cho dự án quạt: GPIO25 (MOSFET), GPIO34 (biến trở)
// =====================================================

class LGFX_Lyrics : public lgfx::LGFX_Device {
  lgfx::Panel_ST7789 _panel;
  lgfx::Bus_SPI _bus;
 public:
  LGFX_Lyrics() {
    {
      auto cfg = _bus.config();
      cfg.spi_host = SPI3_HOST;            // VSPI
      cfg.spi_mode = 0;
      cfg.freq_write = SPI_FREQ;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = true;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = LYR_SCLK;
      cfg.pin_mosi = LYR_MOSI;
      cfg.pin_miso = -1;
      cfg.pin_dc = LYR_DC;
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = LYR_CS;
      cfg.pin_rst = LYR_RST;
      cfg.pin_busy = -1;
      cfg.memory_width = 240;
      cfg.memory_height = 320;
      cfg.panel_width = 240;
      cfg.panel_height = 320;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.readable = false;
      cfg.invert = LYR_INVERT;
      cfg.rgb_order = LYR_RGB_ORDER;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      _panel.config(cfg);
    }
    setPanel(&_panel);
  }
};

class LGFX_Stats : public lgfx::LGFX_Device {
  lgfx::Panel_ST7789 _panel;
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;
 public:
  LGFX_Stats() {
    {
      auto cfg = _bus.config();
      cfg.spi_host = SPI2_HOST;            // HSPI
      cfg.spi_mode = STA_SPI_MODE;
      cfg.freq_write = SPI_FREQ;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = true;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = STA_SCLK;
      cfg.pin_mosi = STA_MOSI;
      cfg.pin_miso = -1;
      cfg.pin_dc = STA_DC;
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = STA_CS;
      cfg.pin_rst = STA_RST;
      cfg.pin_busy = -1;
      cfg.memory_width = 240;
      cfg.memory_height = 320;             // chip ST7789 có RAM 240x320, màn chỉ hiện 240x240
      cfg.panel_width = 240;
      cfg.panel_height = 240;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.readable = false;
      cfg.invert = STA_INVERT;
      cfg.rgb_order = STA_RGB_ORDER;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      _panel.config(cfg);
    }
    {
      auto cfg = _light.config();
      cfg.pin_bl = STA_BL;
      cfg.invert = false;
      cfg.freq = 44100;
      cfg.pwm_channel = 7;
      _light.config(cfg);
      _panel.setLight(&_light);
    }
    setPanel(&_panel);
  }
};

LGFX_Lyrics lcdL;
LGFX_Stats lcdS;

// ---- giao thức ----
enum : uint8_t { T_PING = 0, T_JPEG = 1, T_QOI = 2, T_TIME = 3, T_BRIGHT = 4 };
const uint8_t FLAG_FULL = 0x80;
const uint8_t ACK = 0x06, NAK = 0x15, NEED_FULL = 0x07;
// PC gửi tối đa 23000 byte mỗi gói, ảnh lớn hơn thì tự chia. Bộ đệm này và bộ đệm Serial cùng cỡ,
// để lớn hơn thì không đủ RAM cho HTTPS (mỗi kết nối cần ~45 KB, thành từng khối liền nhau).
const size_t MAX_PAYLOAD = 24 * 1024;
const uint32_t STANDBY_MS = 8000;           // 8 s không nhận gói nào từ PC -> chế độ offline
const uint32_t STANDBY_TICK_MS = 250;
const time_t NTP_VALID_EPOCH = 1700000000;  // time() lớn hơn mốc này = NTP đã đồng bộ

static uint8_t* payload = nullptr;
static volatile uint32_t serialRxErrors = 0;   // bộ đệm USB-Serial tràn -> mất dữ liệu từ PC
static bool needFull[2] = {true, true};
static uint32_t lastPacketMs = 0;
static bool standby = false;
static bool pcOverWifi = false;             // gói gần nhất của PC đến qua WiFi (TCP) hay USB
static uint8_t brightness = 220;

static bool timeValid = false;              // PC đã gửi giờ
static uint32_t syncEpoch = 0, syncMs = 0;

static uint32_t maxLoopMs = 0, lastWeatherMs = 0;   // đo thời gian vòng lặp bị đứng, xem ở /api/status
static bool wifiStarted = false;
static bool mdnsStarted = false;
static WiFiServer server(TCP_PORT);
static WiFiClient client;

static String wifiSsid, wifiPass;           // secrets.h hoặc WiFi đặt từ web
static uint32_t lastStaOkMs = 0, lastStaTryMs = 0, apTestStartMs = 0;
static bool apTest = false;

// ---- trạng thái chế độ offline ----
static Weather weather{};
static AirQuality air{};
static uint32_t weatherTryMs = 0, weatherWaitMs = 0;   // wait = 0 -> lấy ngay khi có WiFi
static float weatherLat = NAN, weatherLon = NAN;       // cài đặt của lần tải gần nhất
static bool weatherAqi = false;
static char weatherUpdated[6] = "";
static bool offlineDirty = true;            // cần vẽ lại toàn bộ
static bool noTimeShown = false;
static int shownMinute = -1, shownYday = -1;
static bool shownWifiOk = false;
static uint8_t staPage = 0;                 // 0 = thời tiết, 1 = lịch tháng, 2 = sắp tới
static uint32_t staPageMs = 0;
static uint32_t lastTickMs = 0;

static Upcoming upcoming[UPCOMING_MAX];
static int upcomingCount = 0, upcomingYday = -1;
static char holidayName[40] = "";

// ---- nhắc việc ----
static bool reminderActive = false;
static uint32_t reminderStartMs = 0, reminderFlashMs = 0;
static bool reminderFlash = false;
static char reminderTime[6] = "", reminderText[64] = "";
static int lastReminderMinute = -1;

static bool otaActive = false;
static int otaShown = -2;

// ---------------------------------------------------------------
static uint32_t nowLocal() { return syncEpoch + (millis() - syncMs) / 1000; }

static bool wifiConfigured() { return wifiSsid.length() > 0; }
static bool wifiOk() { return wifiConfigured() && WiFi.status() == WL_CONNECTED; }

static void loadWifiConfig() {
  if (!wifiOverride(wifiSsid, wifiPass)) {
    wifiSsid = DESKMON_WIFI_SSID;
    wifiPass = DESKMON_WIFI_PASS;
  }
}

// Giờ địa phương: ưu tiên NTP (qua WiFi), không có thì dùng giờ PC gửi sang.
static bool localTime(struct tm& out) {
  time_t utc = time(nullptr);
  time_t local;
  if (utc > NTP_VALID_EPOCH) local = utc + TZ_OFFSET_SEC;
  else if (timeValid) local = (time_t)nowLocal();
  else return false;
  gmtime_r(&local, &out);
  return true;
}

static bool readExact(Stream& s, uint8_t* dst, size_t n, uint32_t timeoutMs) {
  size_t got = 0;
  uint32_t last = millis();
  while (got < n) {
    int a = s.available();
    if (a > 0) {
      size_t want = n - got;
      if ((size_t)a < want) want = (size_t)a;
      got += s.readBytes(dst + got, want);
      last = millis();
    } else {
      if (millis() - last > timeoutMs) return false;
      delay(1);
    }
  }
  return true;
}

// ---------------------------------------------------------------
// Chế độ offline (PC tắt / chưa chạy deskmon.py): ESP32 tự vẽ đồng hồ, thời tiết, lịch
static bool refreshWeatherIfDue(const struct tm& now) {
  if (!wifiOk() || millis() - weatherTryMs < weatherWaitMs) return false;
  if (!newsNetTryLock()) return false;       // task tin tức đang dùng mạng -> lần sau lấy
  const uint32_t weatherStart = millis();
  const Settings& s = settings();
  Weather fresh{};
  bool ok = fetchWeather(s.lat, s.lon, fresh);
  if (!s.aqi) {
    air = AirQuality{};
  } else if (ok) {
    AirQuality a{};
    if (fetchAirQuality(s.lat, s.lon, a)) air = a;
  }
  newsNetUnlock();
  lastWeatherMs = millis() - weatherStart;
  weatherLat = s.lat;
  weatherLon = s.lon;
  weatherAqi = s.aqi;
  weatherTryMs = millis();
  weatherWaitMs = ok ? s.weatherMin * 60000UL : WEATHER_RETRY_MS;
  if (!ok) return false;
  weather = fresh;
  snprintf(weatherUpdated, sizeof(weatherUpdated), "%02d:%02d", now.tm_hour, now.tm_min);
  return true;
}

static void refreshEventsIfNeeded(const struct tm& now) {
  if (now.tm_yday == upcomingYday) return;
  upcomingYday = now.tm_yday;
  upcomingCount = upcomingEvents(now, settings(), upcoming, UPCOMING_MAX);
  const LunarDate ld = solarToLunar(now.tm_mday, now.tm_mon + 1, now.tm_year + 1900);
  const char* h = holidayToday(now, ld, settings());
  strlcpy(holidayName, h ? h : "", sizeof(holidayName));
}

static bool isNight(const struct tm& now) {
  const Settings& s = settings();
  if (s.nightFrom == s.nightTo) return false;
  return s.nightFrom < s.nightTo ? (now.tm_hour >= s.nightFrom && now.tm_hour < s.nightTo)
                                 : (now.tm_hour >= s.nightFrom || now.tm_hour < s.nightTo);
}

static void applyOfflineBrightness(const struct tm& now) {
  lcdS.setBrightness(isNight(now) ? settings().brightNight : settings().brightDay);
}

static void drawStaPage(const OfflineInfo& info) {
  if (apActive()) drawApQr(lcdS, apSsid(), apPass());
  else if (staPage == 0) drawWeatherPage(lcdS, info);
  else if (staPage == 1) drawMonthPage(lcdS, info);
  else drawEventsPage(lcdS, info);
  staPageMs = millis();
}

static void nextStaPage() {
  staPage = (staPage + 1) % 3;
  if (staPage == 0 && !wifiConfigured()) staPage = 1;   // không có WiFi -> bỏ trang thời tiết
}

static void drawNoTimeScreen(bool wifi) {
  noTimeShown = true;
  shownWifiOk = wifi;
  if (apActive()) {
    drawApSteps(lcdL, apSsid(), apPass());
    drawApQr(lcdS, apSsid(), apPass());
    return;
  }
  char ip[32] = "";
  const char* detail = "";
  const char* status;
  if (!wifi) {
    status = "Đang kết nối Wi-Fi...";
  } else {
    status = "Đang lấy giờ từ Internet...";
    snprintf(ip, sizeof(ip), "%s:%u", WiFi.localIP().toString().c_str(), (unsigned)TCP_PORT);
    detail = ip;
  }
  drawNoTime(lcdL, lcdS, status, detail);
}

static void standbyTick() {
  const bool wifi = wifiOk();
  struct tm now;
  if (!localTime(now)) {
    if (!noTimeShown || wifi != shownWifiOk) drawNoTimeScreen(wifi);
    return;
  }

  const bool weatherChanged = refreshWeatherIfDue(now);
  if (offlineDirty) upcomingYday = -1;       // cài đặt đổi -> tính lại sự kiện
  refreshEventsIfNeeded(now);
  const Settings& s = settings();
  const OfflineInfo info{&now, &weather, &air, wifiConfigured(), wifi, s.place, s.clockSize,
                         holidayName[0] ? holidayName : nullptr, upcoming, upcomingCount};
  const bool minuteChanged = now.tm_min != shownMinute;
  const bool full = offlineDirty || noTimeShown || now.tm_yday != shownYday ||
                    wifi != shownWifiOk || weatherChanged;
  const uint8_t newsMask = (s.newsOn && wifi && newsCount() > 0) ? s.newsOffline : 0;
  const uint8_t released = newsViewTick(lcdL, lcdS, now, newsMask, s.newsItemSec * 1000UL, s.newsGapSec * 1000UL);
  const bool lyrNews = newsViewActive(0), staNews = newsViewActive(1);
  if (full) {
    if (staPage == 0 && !wifiConfigured()) staPage = 1;
    if (!lyrNews) drawClockScreen(lcdL, info, true);
    if (!staNews) drawStaPage(info);
  } else {
    if (released & 1) drawClockScreen(lcdL, info, true);
    else if (minuteChanged && !lyrNews) drawClockScreen(lcdL, info, false);
    if (released & 2) {
      drawStaPage(info);
    } else if (!staNews && !apActive() && millis() - staPageMs >= s.pageSec * 1000UL) {
      nextStaPage();
      drawStaPage(info);
    }
  }
  if (full || minuteChanged) applyOfflineBrightness(now);

  offlineDirty = false;
  noTimeShown = false;
  shownMinute = now.tm_min;
  shownYday = now.tm_yday;
  shownWifiOk = wifi;
}

static void enterStandby() {
  standby = true;
  needFull[0] = needFull[1] = true;
  offlineDirty = true;
  newsViewReset(settings().newsGapSec * 1000UL);
  standbyTick();
}

static void leaveStandby() {
  if (!standby) return;
  standby = false;
  if (!reminderActive) lcdS.setBrightness(brightness);
}

// ---------------------------------------------------------------
// Nhắc việc: chạy cả khi PC bật (khung hình của PC tạm bị bỏ qua) lẫn khi offline
static void startReminder(const char* timeText, const char* text) {
  strlcpy(reminderTime, timeText, sizeof(reminderTime));
  strlcpy(reminderText, text, sizeof(reminderText));
  reminderActive = true;
  reminderStartMs = millis();
  reminderFlashMs = 0;
  drawReminderMain(lcdL, reminderTime, reminderText);
  lcdS.setBrightness(255);
}

static void stopReminder() {
  if (!reminderActive) return;
  reminderActive = false;
  needFull[0] = needFull[1] = true;          // PC vẽ lại toàn màn
  offlineDirty = true;                       // hoặc chế độ offline vẽ lại
  newsViewReset(settings().newsGapSec * 1000UL);   // về màn thường, không để sót màn nhắc việc
  lcdS.setBrightness(brightness);
}

static void checkReminders(const struct tm& now) {
  const int minuteOfYear = now.tm_yday * 1440 + now.tm_hour * 60 + now.tm_min;
  if (minuteOfYear == lastReminderMinute) return;
  lastReminderMinute = minuteOfYear;
  const Settings& s = settings();
  for (int i = 0; i < s.reminderCount; i++) {
    const Reminder& r = s.reminders[i];
    if (!r.on || r.hour != now.tm_hour || r.minute != now.tm_min || !(r.days & (1 << now.tm_wday))) continue;
    char t[6];
    snprintf(t, sizeof(t), "%02u:%02u", r.hour, r.minute);
    startReminder(t, r.text);
    return;
  }
}

static void reminderTick() {
  struct tm now;
  if (localTime(now)) checkReminders(now);
  if (!reminderActive) return;
  if (digitalRead(BOOT_BUTTON_PIN) == LOW || millis() - reminderStartMs > REMINDER_SHOW_MS) {
    stopReminder();
    return;
  }
  if (millis() - reminderFlashMs >= REMINDER_FLASH_MS) {
    reminderFlashMs = millis();
    reminderFlash = !reminderFlash;
    drawReminderFlash(lcdS, reminderTime, reminderFlash);
  }
}

// ---------------------------------------------------------------
// Web cấu hình + nạp firmware qua WiFi
static void showOtaProgress(int percent) {
  if (percent == otaShown) return;
  otaShown = percent;
  if (percent < 0) {                          // lỗi -> vẽ lại như cũ
    otaActive = false;
    offlineDirty = true;
    needFull[0] = needFull[1] = true;
    newsViewReset(settings().newsGapSec * 1000UL);
    return;
  }
  otaActive = true;
  drawOtaProgress(lcdL, percent);
}

static void webStatus(JsonObject out) {
  out["mode"] = otaActive ? "Đang nạp firmware"
              : standby   ? "Offline (PC tắt)"
              : pcOverWifi ? "PC đang kết nối qua WiFi" : "PC đang kết nối qua USB";
  out["ip"] = (wifiOk() ? WiFi.localIP() : WiFi.softAPIP()).toString();
  out["staOk"] = wifiOk();
  out["rssi"] = WiFi.RSSI();
  out["ap"] = apActive();
  if (apActive()) out["apSsid"] = apSsid();
  out["uptime"] = millis() / 1000;
  out["fw"] = FW_VERSION;
  out["heap"] = ESP.getFreeHeap();
  const BootInfo& boot = bootInfo();
  out["reset"] = resetReasonText(boot.reason);
  out["brownouts"] = boot.brownouts;
  out["crashes"] = boot.crashes;
  out["stackFree"] = uxTaskGetStackHighWaterMark(nullptr);   // byte stack loop() chưa từng dùng tới
  out["serialRxErrors"] = serialRxErrors;
  out["maxLoopMs"] = maxLoopMs;
  out["lastWeatherMs"] = lastWeatherMs;
  char buf[96];
  if (weather.valid) {
    snprintf(buf, sizeof(buf), "%d°C, %s (lúc %s)", (int)lroundf(weather.temp), weatherText(weather.code), weatherUpdated);
    out["weather"] = buf;
  }
  if (air.valid) {
    snprintf(buf, sizeof(buf), "%d · %s", air.aqi, aqiText(aqiLevel(air.aqi)));
    out["aqi"] = buf;
  }
  if (reminderActive) {
    snprintf(buf, sizeof(buf), "%s %s", reminderTime, reminderText);
    out["reminder"] = buf;
  }
  if (standby && settings().newsOn) {
    snprintf(buf, sizeof(buf), "%d bài", newsCount());
    out["news"] = buf;
  }
}

static void onTestAp() {
  apTest = true;
  apTestStartMs = millis();
}

static void onSettingsChanged() {
  offlineDirty = true;
  // Tải thời tiết làm vòng lặp đứng vài giây: chỉ lấy lại ngay khi đổi vị trí hoặc bật/tắt AQI
  const Settings& s = settings();
  if (s.lat != weatherLat || s.lon != weatherLon || s.aqi != weatherAqi) weatherWaitMs = 0;
  lastReminderMinute = -1;
}

static void onTestReminder(const char* text) {
  struct tm now;
  char t[6] = "--:--";
  if (localTime(now)) snprintf(t, sizeof(t), "%02d:%02d", now.tm_hour, now.tm_min);
  startReminder(t, text);
}

static void startOta() {
  ArduinoOTA.setHostname(MDNS_NAME);
  ArduinoOTA.setMdnsEnabled(false);           // mDNS đã chạy ở startServerIfReady
  ArduinoOTA.onStart([] { otaShown = -2; showOtaProgress(0); });
  ArduinoOTA.onProgress([](unsigned int done, unsigned int total) {
    showOtaProgress(total ? (int)(100ULL * done / total) : 0);
  });
  ArduinoOTA.onError([](ota_error_t) { showOtaProgress(-1); });
  ArduinoOTA.begin();
}

// ---------------------------------------------------------------
static void handlePacket(Stream& s, uint8_t type, uint8_t screen, uint16_t x, uint16_t y, uint32_t len) {
  bool full = type & FLAG_FULL;
  type &= 0x7F;
  lastPacketMs = millis();
  pcOverWifi = (&s != &Serial);
  switch (type) {
    case T_PING:
      // PC đang chạy: nếu màn đang ở màn chờ thì xin PC gửi lại toàn màn
      s.write((!reminderActive && (needFull[0] || needFull[1])) ? NEED_FULL : ACK);
      break;

    case T_JPEG:
    case T_QOI: {
      if (screen > 1) { s.write(NAK); break; }
      leaveStandby();
      if (reminderActive || otaActive) {      // đang nhắc việc / nạp firmware: giữ màn, bỏ khung hình
        needFull[screen] = true;
        s.write(ACK);
        break;
      }
      lgfx::LGFX_Device& d = (screen == 0) ? (lgfx::LGFX_Device&)lcdL : (lgfx::LGFX_Device&)lcdS;
      bool ok = (type == T_JPEG) ? d.drawJpg(payload, len, x, y) : d.drawQoi(payload, len, x, y);
      if (!ok) {
        s.write(NAK);
      } else if (full) {
        needFull[screen] = false;
        s.write(ACK);
      } else if (needFull[screen]) {
        s.write(NEED_FULL);                 // màn đang hiện màn chờ -> xin vẽ lại toàn bộ
      } else {
        s.write(ACK);
      }
      break;
    }

    case T_TIME:
      if (len >= 4) {
        syncEpoch = (uint32_t)payload[0] | ((uint32_t)payload[1] << 8) |
                    ((uint32_t)payload[2] << 16) | ((uint32_t)payload[3] << 24);
        syncMs = millis();
        timeValid = true;
      }
      s.write(ACK);
      break;

    case T_BRIGHT:
      if (len >= 1) {
        brightness = payload[0];
        if (!standby && !reminderActive) lcdS.setBrightness(brightness);
      }
      s.write(ACK);
      break;

    default:
      s.write(NAK);
  }
}

// Đọc và xử lý 1 gói từ Serial hoặc WiFi
static void processOne(Stream& s) {
  while (s.available() > 0) {
    if (s.read() != 'D') continue;          // dò byte đồng bộ
    uint8_t m;
    if (!readExact(s, &m, 1, 100) || m != 'M') continue;
    uint8_t h[10];
    if (!readExact(s, h, sizeof(h), 500)) return;
    uint8_t type = h[0], screen = h[1];
    uint16_t x = h[2] | (h[3] << 8);
    uint16_t y = h[4] | (h[5] << 8);
    uint32_t len = (uint32_t)h[6] | ((uint32_t)h[7] << 8) | ((uint32_t)h[8] << 16) | ((uint32_t)h[9] << 24);
    if (len > MAX_PAYLOAD) { s.write(NAK); return; }
    if (len && !readExact(s, payload, len, 3000)) { s.write(NAK); return; }
    handlePacket(s, type, screen, x, y, len);
    return;
  }
}

// ---------------------------------------------------------------
static void startServerIfReady() {
  if (!mdnsStarted && wifiOk()) {             // mDNS chỉ cần trong mạng nhà
    mdnsStarted = true;
    if (MDNS.begin(MDNS_NAME)) {
      MDNS.addService("deskmon", "tcp", TCP_PORT);
      MDNS.addService("http", "tcp", 80);
    }
    Serial.printf("[wifi] connected %s\n", WiFi.localIP().toString().c_str());
  }
  if (wifiStarted || !(wifiOk() || apActive())) return;
  server.begin();
  server.setNoDelay(true);
  webBegin(WebHooks{webStatus, onSettingsChanged, stopReminder, onTestReminder, showOtaProgress, onTestAp});
  startOta();
  wifiStarted = true;
  offlineDirty = true;
}

static void apChanged() {                     // đổi nội dung màn hình theo trạng thái WiFi cài đặt
  noTimeShown = false;
  offlineDirty = true;
}

// Vào được WiFi nhà -> tắt WiFi cài đặt. Không vào được đủ lâu -> phát, và vẫn thử lại WiFi nhà
// mỗi khi không có ai đang nối vào (mất điện: ESP32 khởi động trước khi router sẵn sàng).
static void wifiTick() {
  if (apTest && millis() - apTestStartMs >= AP_TEST_MS) apTest = false;
  if (wifiOk()) lastStaOkMs = millis();
  const bool wanted = apTest || !wifiConfigured() || millis() - lastStaOkMs >= AP_AFTER_MS;
  if (wanted && !apActive()) {
    apStart();
    lastStaTryMs = millis();
    apChanged();
  } else if (!wanted && apActive()) {
    apStop();
    apChanged();
  }
  if (!apActive()) return;
  apLoop();
  if (!wifiOk() && wifiConfigured() && WiFi.softAPgetStationNum() == 0 && millis() - lastStaTryMs >= STA_RETRY_MS) {
    lastStaTryMs = millis();
    WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  }
}

// Log lý do rớt WiFi (chỉ in khi đổi): 2/15 = sai mật khẩu, 201 = không thấy mạng
static void logWifiDisconnect(WiFiEvent_t, WiFiEventInfo_t info) {
  static int lastReason = -1;
  int reason = info.wifi_sta_disconnected.reason;
  if (reason == lastReason) return;
  lastReason = reason;
  Serial.printf("[wifi] disconnected, reason %d\n", reason);
}

static void startWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(MDNS_NAME);
  WiFi.setSleep(false);                     // giảm độ trễ
  WiFi.setAutoReconnect(true);
  WiFi.onEvent(logWifiDisconnect, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  // giờ hệ thống để ở UTC (TZ = UTC0); localTime() tự cộng TZ_OFFSET_SEC
  configTime(0, 0, "pool.ntp.org", "time.google.com", "time.cloudflare.com");
  if (!wifiConfigured()) return;
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 10000) delay(200);
  startServerIfReady();
}

static void pollWiFi() {
  wifiTick();
  startServerIfReady();
  if (!wifiStarted) return;
  webLoop();
  ArduinoOTA.handle();
  // Kết nối mới luôn thay kết nối cũ: PC ngủ / rớt WiFi để lại kết nối "treo" mà ESP32
  // không biết (nó chỉ trả lời, không tự gửi), nếu chờ thì PC không vào lại được.
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  WiFiClient c = server.accept();
#else
  WiFiClient c = server.available();
#endif
  if (c) {
    client.stop();
    client = c;
    client.setNoDelay(true);
  }
  if (client && client.connected() && client.available() > 0) processOne(client);
}

// ---------------------------------------------------------------
void setup() {
  // phải gọi trước Serial.begin. PC gửi kiểu "1 gói rồi chờ ACK", nên bộ đệm chứa trọn
  // 1 gói lớn nhất thì vòng lặp bận bao lâu (trả lời web, vẽ nhắc việc) cũng không mất byte.
  Serial.setRxBufferSize(MAX_PAYLOAD + 1024);
  Serial.begin(SERIAL_BAUD);
  // Mặc định (baud > 57600) chỉ lấy byte khỏi FIFO phần cứng 128 byte khi đã có 120 byte:
  // ở 921600 baud chỉ còn ~0,09 ms dự phòng, ngắt WiFi/flash trễ hơn là mất byte.
  // 64 byte -> ~0,7 ms dự phòng.
  Serial.setRxFIFOFull(64);
  Serial.onReceiveError([](hardwareSerial_error_t e) {
    if (e == UART_BUFFER_FULL_ERROR || e == UART_FIFO_OVF_ERROR) serialRxErrors++;
  });
  bootInfoBegin(FW_VERSION);
  Serial.printf("[boot] reason %d, brownouts %u, crashes %u\n", (int)bootInfo().reason,
                (unsigned)bootInfo().brownouts, (unsigned)bootInfo().crashes);
  settingsBegin();
  loadWifiConfig();
  newsBegin();
  pinMode(BOOT_BUTTON_PIN, INPUT_PULLUP);

  payload = (uint8_t*)malloc(MAX_PAYLOAD);

  lcdL.init();
  lcdL.setRotation(LYR_ROTATION);
  lcdL.fillScreen(TFT_BLACK);
  lcdS.init();
  lcdS.setRotation(STA_ROTATION);
  lcdS.setBrightness(brightness);
  lcdS.fillScreen(TFT_BLACK);

  if (!payload) {
    lcdL.setTextColor(TFT_RED);
    lcdL.setFont(&fonts::FreeSans9pt7b);
    lcdL.drawString("Out of memory!", 10, 10);
    while (true) delay(1000);
  }

  drawNoTimeScreen(false);
  startWiFi();
  enterStandby();
  Serial.println("DESKMON_READY");
}

void loop() {
  const uint32_t loopStart = millis();
  if (Serial.available() > 0) processOne(Serial);
  pollWiFi();

  if (!standby && millis() - lastPacketMs > STANDBY_MS) enterStandby();
  if (millis() - lastTickMs >= STANDBY_TICK_MS) {
    lastTickMs = millis();
    reminderTick();
    const Settings& s = settings();
    newsConfigure({standby && !otaActive && s.newsOn && s.newsOffline != 0 && wifiOk(), s.newsDomestic, s.newsWorld,
                   s.newsRefreshMin});
    if (!standby) newsViewRelease();
    if (standby && !reminderActive && !otaActive) standbyTick();
  }
  const uint32_t loopMs = millis() - loopStart;
  if (loopMs > maxLoopMs) maxLoopMs = loopMs;
  delay(1);
}
