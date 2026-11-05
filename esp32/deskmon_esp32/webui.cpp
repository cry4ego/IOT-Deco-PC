#include "webui.h"
#include <WebServer.h>
#include <WiFi.h>
#include <Update.h>
#include "news.h"
#include "settings.h"
#include "web_page.h"

namespace {

constexpr uint32_t PC_TIMEOUT_MS = 10000;
constexpr uint32_t RESTART_DELAY_MS = 600;
constexpr int MAX_SCAN_RESULTS = 30;

WebServer server(80);
WebHooks hooks{};
bool captive = false;                     // đang phát WiFi cài đặt: mọi địa chỉ lạ dẫn về trang này

// bài đang phát do deskmon.py báo lên
struct NowPlaying {
  char title[96];
  char artist[96];
  char source[24];
  bool playing;
};
NowPlaying pcPlaying{};
uint32_t pcSeenMs = 0;
bool pcSeen = false;

// lệnh nhạc chờ PC lấy
uint32_t mediaSeq = 0;
char mediaCmd[12] = "";

bool uploadAllowed = false;
bool uploadFailed = false;

void sendJson(int code, JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  server.send(code, "application/json; charset=utf-8", out);
}

void sendError(int code, const char* msg) {
  JsonDocument doc;
  doc["error"] = msg;
  sendJson(code, doc);
}

bool readBody(JsonDocument& doc) {
  if (!server.hasArg("plain")) {
    sendError(400, "Thiếu nội dung JSON");
    return false;
  }
  if (deserializeJson(doc, server.arg("plain"))) {
    sendError(400, "JSON không hợp lệ");
    return false;
  }
  return true;
}

// Web không có mật khẩu: chặn trang web lạ đang mở trong trình duyệt tự gửi lệnh tới thiết bị.
// Trình duyệt luôn kèm Origin khi POST từ một trang; deskmon.py và curl thì không.
bool originAllowed() {
  const String origin = server.header("Origin");
  return origin.isEmpty() || origin == "http://" + server.hostHeader();
}

bool sameOrigin() {
  if (originAllowed()) return true;
  sendError(403, "Yêu cầu đến từ trang web khác");
  return false;
}

void restartSoon() {
  delay(RESTART_DELAY_MS);
  ESP.restart();
}

void copyField(char* dst, size_t n, JsonVariantConst v) { strlcpy(dst, v | "", n); }

// ---------------------------------------------------------------- handlers
void handleRoot() { server.send_P(200, "text/html; charset=utf-8", WEB_PAGE); }

void handleGetConfig() {
  JsonDocument doc;
  settingsToJson(doc.to<JsonObject>());
  sendJson(200, doc);
}

void handlePostConfig() {
  if (!sameOrigin()) return;
  JsonDocument in;
  if (!readBody(in)) return;
  String err;
  if (!settingsUpdate(in.as<JsonVariantConst>(), err)) {
    sendError(400, err.c_str());
    return;
  }
  if (hooks.settingsChanged) hooks.settingsChanged();
  handleGetConfig();
}

void handleStatus() {
  JsonDocument doc;
  JsonObject out = doc.to<JsonObject>();
  if (hooks.status) hooks.status(out);
  JsonObject pc = out["pc"].to<JsonObject>();
  pc["connected"] = pcConnectedRecently();
  if (pcConnectedRecently()) {
    pc["title"] = pcPlaying.title;
    pc["artist"] = pcPlaying.artist;
    pc["source"] = pcPlaying.source;
    pc["playing"] = pcPlaying.playing;
  }
  sendJson(200, doc);
}

void handleNews() {
  JsonDocument doc;
  newsToJson(doc.to<JsonObject>());
  sendJson(200, doc);
}

void handleMedia() {
  if (!sameOrigin()) return;
  JsonDocument in;
  if (!readBody(in)) return;
  const char* cmd = in["cmd"] | "";
  if (strcmp(cmd, "play_pause") != 0 && strcmp(cmd, "next") != 0 && strcmp(cmd, "prev") != 0) {
    sendError(400, "Lệnh không hợp lệ");
    return;
  }
  if (!pcConnectedRecently()) {
    sendError(409, "PC chưa kết nối");
    return;
  }
  strlcpy(mediaCmd, cmd, sizeof(mediaCmd));
  mediaSeq++;
  JsonDocument doc;
  doc["ok"] = true;
  doc["seq"] = mediaSeq;
  sendJson(200, doc);
}

void handlePc() {
  if (!sameOrigin()) return;
  JsonDocument in;
  if (!readBody(in)) return;
  JsonVariantConst np = in["np"];
  NowPlaying fresh{};
  if (!np.isNull()) {
    copyField(fresh.title, sizeof(fresh.title), np["title"]);
    copyField(fresh.artist, sizeof(fresh.artist), np["artist"]);
    copyField(fresh.source, sizeof(fresh.source), np["source"]);
    fresh.playing = np["playing"] | false;
  }
  pcPlaying = fresh;
  pcSeenMs = millis();
  pcSeen = true;

  JsonDocument doc;
  doc["v"] = settingsVersion();
  pcSettingsToJson(doc["pc"].to<JsonObject>());
  JsonObject cmd = doc["cmd"].to<JsonObject>();
  cmd["seq"] = mediaSeq;
  cmd["name"] = mediaCmd;
  sendJson(200, doc);
}

void handleDismiss() {
  if (!sameOrigin()) return;
  if (hooks.dismissReminder) hooks.dismissReminder();
  server.send(204);
}

void handleTestReminder() {
  if (!sameOrigin()) return;
  JsonDocument in;
  if (!readBody(in)) return;
  if (hooks.testReminder) hooks.testReminder(in["text"] | "");
  server.send(204);
}

void handleWifiScan() {
  if (!sameOrigin()) return;              // quét làm đứng vòng lặp vài giây: không để trang lạ gọi
  const int n = WiFi.scanNetworks();
  JsonDocument doc;
  JsonArray list = doc["networks"].to<JsonArray>();
  for (int i = 0; i < n && i < MAX_SCAN_RESULTS; i++) {
    JsonObject o = list.add<JsonObject>();
    o["ssid"] = WiFi.SSID(i);
    o["rssi"] = WiFi.RSSI(i);
  }
  WiFi.scanDelete();
  sendJson(200, doc);
}

void handleApTest() {
  if (!sameOrigin()) return;
  if (hooks.testAp) hooks.testAp();
  server.send(204);
}

void handleWifi() {
  if (!sameOrigin()) return;
  JsonDocument in;
  if (!readBody(in)) return;
  const char* ssid = in["ssid"] | "";
  const char* pass = in["pass"] | "";
  if (strlen(ssid) == 0 || strlen(ssid) > 32 || strlen(pass) > 64) {
    sendError(400, "Tên WiFi 1-32 ký tự, mật khẩu tối đa 64 ký tự");
    return;
  }
  saveWifiOverride(ssid, pass);
  server.send(200, "application/json", "{\"ok\":true}");
  restartSoon();
}

void handleReboot() {
  if (!sameOrigin()) return;
  server.send(200, "application/json", "{\"ok\":true}");
  restartSoon();
}

void handleUploadDone() {
  if (!sameOrigin()) return;
  const bool ok = !uploadFailed && !Update.hasError();
  server.send(ok ? 200 : 500, "application/json", ok ? "{\"ok\":true}" : "{\"error\":\"Nạp firmware lỗi\"}");
  if (ok) restartSoon();
}

void handleUploadChunk() {
  HTTPUpload& up = server.upload();
  if (up.status == UPLOAD_FILE_START) {
    uploadAllowed = originAllowed();
    uploadFailed = false;
    if (!uploadAllowed) return;
    if (!Update.begin(UPDATE_SIZE_UNKNOWN)) uploadFailed = true;
    if (hooks.otaProgress) hooks.otaProgress(0);
  } else if (up.status == UPLOAD_FILE_WRITE) {
    if (!uploadAllowed || uploadFailed) return;
    if (Update.write(up.buf, up.currentSize) != up.currentSize) uploadFailed = true;
    const size_t total = server.header("Content-Length").toInt();
    if (hooks.otaProgress && total > 0) hooks.otaProgress((int)(100.0 * up.totalSize / total));
  } else if (up.status == UPLOAD_FILE_END) {
    if (!uploadAllowed || uploadFailed) return;
    if (!Update.end(true)) uploadFailed = true;
    if (hooks.otaProgress) hooks.otaProgress(uploadFailed ? -1 : 100);
  } else if (up.status == UPLOAD_FILE_ABORTED) {
    if (!uploadAllowed) return;
    Update.abort();
    uploadFailed = true;
    if (hooks.otaProgress) hooks.otaProgress(-1);
  }
}

void handleNotFound() {
  if (captive) {                          // điện thoại dò mạng -> bật trang cài đặt
    server.sendHeader("Location", "http://" + WiFi.softAPIP().toString() + "/");
    server.send(302, "text/plain", "");
    return;
  }
  sendError(404, "Không có trang này");
}

}  // namespace

void webBegin(const WebHooks& h) {
  hooks = h;
  const char* headers[] = {"Content-Length", "Origin"};
  server.collectHeaders(headers, 2);
  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/config", HTTP_GET, handleGetConfig);
  server.on("/api/config", HTTP_POST, handlePostConfig);
  server.on("/api/status", HTTP_GET, handleStatus);
  server.on("/api/news", HTTP_GET, handleNews);
  server.on("/api/media", HTTP_POST, handleMedia);
  server.on("/api/pc", HTTP_POST, handlePc);
  server.on("/api/reminder/dismiss", HTTP_POST, handleDismiss);
  server.on("/api/reminder/test", HTTP_POST, handleTestReminder);
  server.on("/api/wifi", HTTP_POST, handleWifi);
  server.on("/api/wifi/scan", HTTP_POST, handleWifiScan);
  server.on("/api/ap/test", HTTP_POST, handleApTest);
  server.on("/api/reboot", HTTP_POST, handleReboot);
  server.on("/update", HTTP_POST, handleUploadDone, handleUploadChunk);
  server.onNotFound(handleNotFound);
  server.begin();
}

void webLoop() { server.handleClient(); }

void webSetCaptive(bool on) { captive = on; }

bool pcConnectedRecently() { return pcSeen && millis() - pcSeenMs < PC_TIMEOUT_MS; }
