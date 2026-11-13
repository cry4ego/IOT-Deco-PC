/*
  Màn hình phụ - firmware ESP32 (dự án riêng, tách khỏi DeskMon)
  ---------------------------------------------------------------
  Windows có thêm một màn hình ảo (Virtual Display Driver). Chương trình PC (monitor/pc/man_phu.py)
  chụp màn ảo đó, thu nhỏ về 320x240, chỉ gửi vùng thay đổi (JPEG/QOI) qua USB hoặc WiFi.
  ESP32 vẽ lên màn 2.0". Màn 1.54" hiện trạng thái: kết nối, số khung hình mỗi giây, IP.

  Giao thức: protocol.h. Đi dây: giống DeskMon (README ở thư mục gốc).
*/
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <WiFi.h>
#include <esp_ota_ops.h>
#include <esp_rom_crc.h>
#include "net.h"
#include "protocol.h"
#include "../../../esp32/deskmon_esp32/fonts_vi.h"   // phông tiếng Việt của DeskMon (chỉ đọc)

const char* FW_VERSION = "man-phu 0.3.0";
const uint32_t SERIAL_BAUD = 2000000;       // CH340 chạy được 2 Mbaud; phải khớp --baud bên PC
const uint16_t TCP_PORT = 7778;             // khác cổng 7777 của DeskMon
const uint32_t IDLE_MS = 5000;              // chừng này không có khung hình -> hiện màn chờ
const uint32_t STATUS_MS = 1000;
const uint32_t READ_TIMEOUT_MS = 1000;
// Chừng này không có hình từ PC (PC tắt / ngủ) -> khởi động vào DeskMon ở vùng firmware còn lại,
// để màn vẫn là đồng hồ/thời tiết/tin tức. Bật PC lên, man_phu.py tự nạp lại màn phụ.
const uint32_t SWITCH_TO_DESKMON_MS = 90000;

// Chép stack loop() lên 16 KB như DeskMon: LovyanGFX chép cả bitmap chữ lên stack khi vẽ.
SET_LOOP_TASK_STACK_SIZE(16 * 1024);

// ---- Màn 2.0" - bus VSPI (giống DeskMon) ----
class LGFX_Main : public lgfx::LGFX_Device {
  lgfx::Panel_ST7789 _panel;
  lgfx::Bus_SPI _bus;
 public:
  LGFX_Main() {
    auto b = _bus.config();
    b.spi_host = SPI3_HOST;
    b.spi_mode = 0;
    b.freq_write = 80000000;              // gấp đôi DeskMon: đẩy hình nhanh hơn (hình lỗi thì hạ về 40000000)
    b.freq_read = 16000000;
    b.spi_3wire = true;
    b.use_lock = true;
    b.dma_channel = SPI_DMA_CH_AUTO;
    b.pin_sclk = 18;
    b.pin_mosi = 23;
    b.pin_miso = -1;
    b.pin_dc = 16;
    _bus.config(b);
    _panel.setBus(&_bus);
    auto p = _panel.config();
    p.pin_cs = 5;
    p.pin_rst = 17;
    p.pin_busy = -1;
    p.memory_width = p.panel_width = 240;
    p.memory_height = p.panel_height = 320;
    p.readable = false;
    p.invert = true;
    p.rgb_order = false;
    p.bus_shared = false;
    _panel.config(p);
    setPanel(&_panel);
  }
};

// ---- Màn 1.54" - bus HSPI (giống DeskMon) ----
class LGFX_Status : public lgfx::LGFX_Device {
  lgfx::Panel_ST7789 _panel;
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;
 public:
  LGFX_Status() {
    auto b = _bus.config();
    b.spi_host = SPI2_HOST;
    b.spi_mode = 0;
    b.freq_write = 40000000;
    b.freq_read = 16000000;
    b.spi_3wire = true;
    b.use_lock = true;
    b.dma_channel = SPI_DMA_CH_AUTO;
    b.pin_sclk = 14;
    b.pin_mosi = 13;
    b.pin_miso = -1;
    b.pin_dc = 27;
    _bus.config(b);
    _panel.setBus(&_bus);
    auto p = _panel.config();
    p.pin_cs = 26;
    p.pin_rst = 33;
    p.pin_busy = -1;
    p.memory_width = 240;
    p.memory_height = 320;
    p.panel_width = p.panel_height = 240;
    p.readable = false;
    p.invert = true;
    p.rgb_order = false;
    p.bus_shared = false;
    _panel.config(p);
    auto l = _light.config();
    l.pin_bl = 32;
    l.freq = 44100;
    l.pwm_channel = 7;
    _light.config(l);
    _panel.setLight(&_light);
    setPanel(&_panel);
  }
};

LGFX_Main lcd;
LGFX_Status sta;

static uint8_t* payload = nullptr;
static WiFiServer tcpServer(TCP_PORT);
static WiFiClient tcpClient;
static bool tcpStarted = false;

// ---- thống kê ----
static volatile uint32_t serialRxErrors = 0;
static uint32_t frames = 0, framesShown = 0, crcErrors = 0, decodeErrors = 0, bytesIn = 0;
static uint32_t waitMs = 0, drawMs = 0;     // trong mỗi giây: chờ dữ liệu tới / giải mã + vẽ
static uint32_t waitPct = 0, drawPct = 0;
static uint32_t lastFrameMs = 0, statusMs = 0, fpsWindowMs = 0;
static float fps = 0, kbps = 0;
static bool frameBad = false;               // khung đang nhận có gói hỏng
static bool idleShown = false;              // đang hiện màn chờ: PC phải gửi lại toàn màn
static uint32_t lastStaFrameMs = 0;         // PC đang tự vẽ màn 1.54" (trang hiệu năng / màn phụ)
static bool staDriven = false, staDirty = false;   // staDirty: ESP32 vừa tự vẽ trang trạng thái
static const char* linkName = "";
static int otaPercent = -2;

static const uint16_t BG = 0x0861, FG = 0xF7BE, DIM = 0x8C94, ACCENT = 0x3DFF;

static void text(lgfx::LGFX_Device& d, const uint8_t* font, const char* s, int x, int y, textdatum_t datum,
                 uint16_t color) {
  d.loadFont(font);
  d.setTextDatum(datum);
  d.setTextColor(color, BG);
  d.drawString(s, x, y);
}

static void drawIdle() {
  lcd.fillScreen(BG);
  text(lcd, FONT_MED, "Màn hình phụ", 160, 100, textdatum_t::middle_center, FG);
  text(lcd, FONT_SMALL, "Đang chờ PC gửi hình...", 160, 132, textdatum_t::middle_center, DIM);
  idleShown = true;
}

static void drawStatus() {
  char buf[48];
  sta.fillScreen(BG);
  text(sta, FONT_MED, "Màn hình phụ", 120, 22, textdatum_t::middle_center, FG);
  const bool live = millis() - lastFrameMs < IDLE_MS;
  text(sta, FONT_SMALL, live ? linkName : "Chờ PC", 120, 62, textdatum_t::middle_center, live ? ACCENT : DIM);
  snprintf(buf, sizeof(buf), "%.1f", live ? fps : 0.0f);
  text(sta, FONT_BIG, buf, 120, 110, textdatum_t::middle_center, FG);
  text(sta, FONT_SMALL, "khung hình / giây", 120, 146, textdatum_t::middle_center, DIM);
  snprintf(buf, sizeof(buf), "%.0f KB/s", live ? kbps : 0.0f);
  text(sta, FONT_SMALL, buf, 120, 172, textdatum_t::middle_center, DIM);
  const String ip = netIp();
  text(sta, FONT_SMALL, ip.length() ? ip.c_str() : "Chưa có WiFi", 120, 214, textdatum_t::middle_center, DIM);
}

static void showOtaProgress(int percent) {
  if (percent == otaPercent) return;
  otaPercent = percent;
  char buf[24];
  lcd.fillScreen(BG);
  if (percent < 0) {
    text(lcd, FONT_MED, "Nạp firmware lỗi", 160, 120, textdatum_t::middle_center, FG);
    idleShown = true;
    return;
  }
  snprintf(buf, sizeof(buf), "Đang nạp %d%%", percent);
  text(lcd, FONT_MED, buf, 160, 110, textdatum_t::middle_center, FG);
  text(lcd, FONT_SMALL, "Đừng rút điện", 160, 140, textdatum_t::middle_center, DIM);
}

static void webStatus(JsonObject out) {
  out["firmware"] = FW_VERSION;
  out["link"] = millis() - lastFrameMs < IDLE_MS ? linkName : "chờ PC";
  out["fps"] = fps;
  out["kbps"] = kbps;
  out["waitPct"] = waitPct;                 // % thời gian chờ dữ liệu: cao = đường truyền chậm
  out["drawPct"] = drawPct;                 // % thời gian giải mã + vẽ: cao = ESP32 là chỗ nghẽn
  out["frames"] = frames;
  out["crcErrors"] = crcErrors;
  out["decodeErrors"] = decodeErrors;
  out["serialRxErrors"] = serialRxErrors;
  out["heap"] = ESP.getFreeHeap();
  out["uptime"] = millis() / 1000;
  out["rssi"] = WiFi.RSSI();
}

// ---------------------------------------------------------------- nhận gói
static bool readExact(Stream& s, uint8_t* dst, size_t n) {
  size_t got = 0;
  uint32_t last = millis();
  while (got < n) {
    const int a = s.available();
    if (a > 0) {
      got += s.readBytes(dst + got, min((size_t)a, n - got));
      last = millis();
    } else if (millis() - last > READ_TIMEOUT_MS) {
      return false;
    } else {
      delay(0);
    }
  }
  return true;
}

static void endFrame(Stream& s) {
  frames++;
  lastFrameMs = millis();
  if (frameBad) {
    s.write(NAK);                           // PC gửi lại toàn màn
  } else if (idleShown || staDirty) {
    s.write(NEED_FULL);
    idleShown = staDirty = false;
  } else {
    s.write(ACK);
  }
  frameBad = false;
}

static void handlePacket(Stream& s, uint8_t type, uint8_t screen, uint16_t x, uint16_t y, uint32_t len,
                         uint32_t crc) {
  bytesIn += HEADER_LEN + len;
  switch (type) {
    case P_HELLO:
      frameBad = false;
      s.write(ACK);
      break;
    case P_JPEG:
    case P_QOI: {
      if (esp_rom_crc32_le(0, payload, len) != crc) {
        crcErrors++;
        frameBad = true;
        break;
      }
      if (screen == 1) {
        lastStaFrameMs = millis();
        staDriven = true;
      }
      // khung đã hỏng / PC sắp gửi lại toàn màn: khỏi vẽ
      if (frameBad || (screen == 0 ? idleShown : staDirty)) break;
      lgfx::LGFX_Device& d = screen == 1 ? (lgfx::LGFX_Device&)sta : (lgfx::LGFX_Device&)lcd;
      const uint32_t t0 = millis();
      const bool ok = type == P_JPEG ? d.drawJpg(payload, len, x, y) : d.drawQoi(payload, len, x, y);
      drawMs += millis() - t0;
      if (!ok) {
        decodeErrors++;
        frameBad = true;
      }
      break;
    }
    case P_FRAME:
      endFrame(s);
      break;
    default:
      frameBad = true;
  }
}

static void processOne(Stream& s, const char* name) {
  while (s.available() > 0) {
    if (s.read() != MAGIC0) continue;
    uint8_t m;
    if (!readExact(s, &m, 1) || m != MAGIC1) continue;
    uint8_t h[HEADER_LEN - 2];
    if (!readExact(s, h, sizeof(h))) return;
    const uint8_t type = h[0];
    const uint16_t x = h[2] | (h[3] << 8), y = h[4] | (h[5] << 8);
    const uint32_t len = (uint32_t)h[6] | ((uint32_t)h[7] << 8) | ((uint32_t)h[8] << 16) | ((uint32_t)h[9] << 24);
    const uint32_t crc = (uint32_t)h[10] | ((uint32_t)h[11] << 8) | ((uint32_t)h[12] << 16) | ((uint32_t)h[13] << 24);
    const uint32_t t0 = millis();
    const bool got = len <= MAX_PAYLOAD && (len == 0 || readExact(s, payload, len));
    waitMs += millis() - t0;
    if (!got) {
      frameBad = true;
      return;
    }
    linkName = name;
    handlePacket(s, type, h[1], x, y, len, crc);
    return;
  }
}

static void pollTcp() {
  if (!tcpStarted) {
    if (!netWifiOk()) return;
    tcpServer.begin();
    tcpServer.setNoDelay(true);
    tcpStarted = true;
  }
  WiFiClient c = tcpServer.available();
  if (c) {                                  // kết nối mới thay kết nối cũ (PC ngủ dậy, rớt WiFi)
    tcpClient.stop();
    tcpClient = c;
    tcpClient.setNoDelay(true);
  }
  if (tcpClient && tcpClient.connected() && tcpClient.available() > 0) processOne(tcpClient, "WiFi");
}

// Vùng firmware còn lại chứa DeskMon (nạp qua WiFi luôn ghi vào vùng không chạy). Ảnh không hợp lệ
// thì esp_ota_set_boot_partition từ chối và màn phụ chạy tiếp.
static void switchToDeskMon() {
  static bool tried = false;
  if (tried) return;
  tried = true;
  const esp_partition_t* other = esp_ota_get_next_update_partition(nullptr);
  if (!other || esp_ota_set_boot_partition(other) != ESP_OK) {
    Serial.println("[switch] khong co DeskMon o vung con lai");
    return;
  }
  lcd.fillScreen(BG);
  text(lcd, FONT_MED, "PC đã tắt", 160, 100, textdatum_t::middle_center, FG);
  text(lcd, FONT_SMALL, "Chuyển sang DeskMon...", 160, 132, textdatum_t::middle_center, DIM);
  delay(1500);
  ESP.restart();
}

static void updateStats() {
  const uint32_t now = millis();
  if (now - fpsWindowMs >= STATUS_MS) {
    const float sec = (now - fpsWindowMs) / 1000.0f;
    fps = (frames - framesShown) / sec;
    kbps = bytesIn / 1024.0f / sec;
    waitPct = waitMs / (sec * 10);
    drawPct = drawMs / (sec * 10);
    framesShown = frames;
    bytesIn = waitMs = drawMs = 0;
    fpsWindowMs = now;
  }
  const bool pcDrawsSta = staDriven && now - lastStaFrameMs < IDLE_MS;
  if (now - statusMs >= STATUS_MS && !pcDrawsSta) {   // PC đang vẽ màn 1.54" thì thôi, đỡ tốn thời gian
    statusMs = now;
    drawStatus();
    if (staDriven) staDirty = true;                    // PC quay lại phải gửi toàn màn 1.54"
  }
  if (!idleShown && lastFrameMs && now - lastFrameMs > IDLE_MS && otaPercent < 0) drawIdle();
  if (now - lastFrameMs > SWITCH_TO_DESKMON_MS && otaPercent < 0) switchToDeskMon();
}

// ---------------------------------------------------------------- chính
void setup() {
  Serial.setRxBufferSize(49152);             // PC gửi trước tối đa 40 KB (BYTES_IN_FLIGHT) trong lúc ESP32 vẽ
  Serial.begin(SERIAL_BAUD);
  Serial.setRxFIFOFull(32);                  // 2 Mbaud: lấy byte khỏi FIFO sớm để không bị tràn
  Serial.onReceiveError([](hardwareSerial_error_t e) {
    if (e == UART_BUFFER_FULL_ERROR || e == UART_FIFO_OVF_ERROR) serialRxErrors++;
  });
  payload = (uint8_t*)malloc(MAX_PAYLOAD);
  lcd.init();
  lcd.setRotation(1);                        // nằm ngang 320x240, giống DeskMon
  sta.init();
  sta.setRotation(0);
  sta.setBrightness(180);
  drawIdle();
  drawStatus();
  netBegin(NetHooks{webStatus, showOtaProgress});
  Serial.println("MAN_PHU_READY");
}

void loop() {
  if (Serial.available() > 0) processOne(Serial, "USB");
  pollTcp();
  netLoop();
  updateStats();
  delay(0);
}
