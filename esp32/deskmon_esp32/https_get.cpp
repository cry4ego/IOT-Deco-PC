#include "https_get.h"
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

namespace {

constexpr const char* USER_AGENT = "DeskMon";
constexpr uint16_t HTTP_TIMEOUT_MS = 8000;
constexpr uint32_t TLS_HANDSHAKE_TIMEOUT_S = 15;
constexpr uint32_t BODY_TIMEOUT_MS = 20000;
constexpr uint32_t TLS_MIN_HEAP = 50000;

// Chờ có dữ liệu. false = hết giờ hoặc kết nối đã đóng.
bool waitData(WiFiClient& in, uint32_t deadline) {
  while (!in.available()) {
    if (!in.connected() || (int32_t)(millis() - deadline) >= 0) return false;
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  return true;
}

bool readLine(WiFiClient& in, char* line, size_t cap, uint32_t deadline) {
  size_t n = 0;
  for (;;) {
    if (!waitData(in, deadline)) return false;
    const int c = in.read();
    if (c < 0) return false;
    if (c == '\n') break;
    if (n + 1 < cap) line[n++] = (char)c;
  }
  line[n] = '\0';
  return true;
}

// Chép len byte sang sink (len < 0: tới khi server đóng kết nối). false = lỗi mạng hoặc sink không nhận nữa.
bool copyBody(WiFiClient& in, Stream& sink, int len, uint32_t deadline) {
  uint8_t buf[512];
  while (len != 0) {
    if (!waitData(in, deadline)) return len < 0 && !in.connected();
    const size_t want = (len > 0 && (size_t)len < sizeof(buf)) ? (size_t)len : sizeof(buf);
    const int got = in.read(buf, want);
    if (got <= 0) return false;
    if (sink.write(buf, (size_t)got) != (size_t)got) return false;
    if (len > 0) len -= got;
  }
  return true;
}

bool readBody(HTTPClient& http, Stream& sink) {
  WiFiClient* in = http.getStreamPtr();
  if (!in) return false;
  const uint32_t deadline = millis() + BODY_TIMEOUT_MS;
  if (!http.header("Transfer-Encoding").equalsIgnoreCase("chunked")) {
    return copyBody(*in, sink, http.getSize(), deadline);
  }
  char line[16];
  for (;;) {
    if (!readLine(*in, line, sizeof(line), deadline)) return false;
    const long size = strtol(line, nullptr, 16);
    if (size <= 0) return size == 0;
    if (!copyBody(*in, sink, (int)size, deadline)) return false;
    if (!readLine(*in, line, sizeof(line), deadline)) return false;   // CRLF cuối mỗi đoạn
  }
}

}  // namespace

bool httpsGet(const char* url, Stream& sink, char* err, size_t errLen) {
  if (WiFi.status() != WL_CONNECTED) return false;
  if (ESP.getFreeHeap() < TLS_MIN_HEAP) {
    snprintf(err, errLen, "Thiếu RAM: %u", (unsigned)ESP.getFreeHeap());
    return false;
  }
  WiFiClientSecure tls;
  tls.setInsecure();   // chỉ đọc dữ liệu công khai, không gửi gì nhạy cảm
  tls.setHandshakeTimeout(TLS_HANDSHAKE_TIMEOUT_S);
  HTTPClient http;
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setUserAgent(USER_AGENT);
  const char* headers[] = {"Transfer-Encoding"};
  http.collectHeaders(headers, 1);
  if (!http.begin(tls, url)) return false;
  const int status = http.GET();
  bool complete = false;
  if (status == HTTP_CODE_OK) {
    complete = readBody(http, sink);
  } else {
    char tlsError[48] = "";
    tls.lastError(tlsError, sizeof(tlsError));
    snprintf(err, errLen, "HTTP %d %s (RAM %u, khoi lon nhat %u)", status, tlsError,
             (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMaxAllocHeap());
  }
  http.end();
  return complete;
}
