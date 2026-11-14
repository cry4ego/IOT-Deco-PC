#include "net.h"
#include <ESPmDNS.h>
#include <esp_ota_ops.h>
#include <Preferences.h>
#include <Update.h>
#include <WebServer.h>
#include <WiFi.h>

// WiFi: ưu tiên mạng đã đặt từ web của DeskMon (lưu trong NVS), không có thì lấy secrets.h của DeskMon.
// Chỉ đọc, không sửa gì của dự án cũ.
#if __has_include("../../../esp32/deskmon_esp32/secrets.h")
#include "../../../esp32/deskmon_esp32/secrets.h"
#endif
#ifndef DESKMON_WIFI_SSID
#define DESKMON_WIFI_SSID ""
#define DESKMON_WIFI_PASS ""
#endif

namespace {

constexpr const char* HOSTNAME = "deskmon";
constexpr uint32_t RESTART_DELAY_MS = 600;

WebServer server(80);
NetHooks hooks{};
bool started = false;
bool uploadAllowed = false, uploadFailed = false;

const char PAGE[] PROGMEM = R"HTML(<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Màn hình phụ</title>
<style>body{font:15px system-ui,sans-serif;max-width:640px;margin:24px auto;padding:0 16px;background:#0b0e14;color:#eef1f6}
.card{background:#141925;border:1px solid #232a3a;border-radius:12px;padding:16px;margin:12px 0}b{color:#38bdf8}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid #232a3a;background:#0f131c;color:#eef1f6}</style></head>
<body><h2>Màn hình phụ ESP32</h2><div class="card" id="st">Đang tải…</div>
<div class="card"><h3>Nạp firmware</h3><input type="file" id="fw" accept=".bin"> <button id="up">Nạp</button>
<p id="msg"></p><p style="color:#8b93a7">Muốn quay lại DeskMon: nạp firmware của DeskMon ở đây, hoặc chạy
<code>pio run -e ota -t upload</code> ở thư mục DeskMon.</p></div>
<script>
const $=s=>document.querySelector(s);
async function poll(){try{const s=await(await fetch("/api/status")).json();
$("#st").innerHTML="";for(const[k,v]of Object.entries(s)){const p=document.createElement("div");p.textContent=k+": "+v;$("#st").append(p)}}catch(e){}}
$("#up").onclick=()=>{const f=$("#fw").files[0];if(!f)return;const d=new FormData();d.append("firmware",f);
$("#msg").textContent="Đang nạp…";fetch("/update",{method:"POST",body:d}).then(r=>$("#msg").textContent=r.ok?"Xong, đang khởi động lại…":"Lỗi "+r.status)};
poll();setInterval(poll,2000);
</script></body></html>)HTML";

// Không có mật khẩu: chỉ chặn trang web lạ trong trình duyệt tự gửi lệnh tới thiết bị (giống DeskMon)
bool originAllowed() {
  const String origin = server.header("Origin");
  return origin.isEmpty() || origin == "http://" + server.hostHeader();
}

void handleStatus() {
  JsonDocument doc;
  if (hooks.status) hooks.status(doc.to<JsonObject>());
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json; charset=utf-8", out);
}

void handleUploadDone() {
  if (!originAllowed()) {
    server.send(403, "text/plain", "Yêu cầu đến từ trang web khác");
    return;
  }
  const bool ok = !uploadFailed && !Update.hasError();
  server.send(ok ? 200 : 500, "application/json", ok ? "{\"ok\":true}" : "{\"error\":\"Nạp firmware lỗi\"}");
  if (!ok) return;
  delay(RESTART_DELAY_MS);
  ESP.restart();
}

// PC nạp DeskMon vào vùng firmware còn lại nhưng màn phụ vẫn chạy: không khởi động lại,
// trả vùng khởi động về firmware đang chạy (Update.end vừa đổi sang vùng mới).
void handleDeskmonDone() {
  if (!originAllowed()) {
    server.send(403, "text/plain", "Yêu cầu đến từ trang web khác");
    return;
  }
  const bool written = !uploadFailed && !Update.hasError();
  if (written) esp_ota_set_boot_partition(esp_ota_get_running_partition());
  const bool deskmon = written && hooks.deskmonWritten && hooks.deskmonWritten();
  server.send(written ? 200 : 500, "application/json",
              String("{\"ok\":") + (written ? "true" : "false") + ",\"deskmon\":" + (deskmon ? "true" : "false") + "}");
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

void startServices() {
  if (MDNS.begin(HOSTNAME)) MDNS.addService("http", "tcp", 80);
  const char* headers[] = {"Content-Length", "Origin"};
  server.collectHeaders(headers, 2);
  server.on("/", HTTP_GET, [] { server.send_P(200, "text/html; charset=utf-8", PAGE); });
  server.on("/api/status", HTTP_GET, handleStatus);
  server.on("/update", HTTP_POST, handleUploadDone, handleUploadChunk);
  server.on("/api/deskmon", HTTP_POST, handleDeskmonDone, handleUploadChunk);
  server.onNotFound([] { server.send(404, "text/plain", "Không có trang này"); });
  server.begin();
  started = true;
  Serial.printf("[wifi] connected %s\n", WiFi.localIP().toString().c_str());
}

}  // namespace

void netBegin(const NetHooks& h) {
  hooks = h;
  String ssid = DESKMON_WIFI_SSID, pass = DESKMON_WIFI_PASS;
  Preferences p;                              // WiFi đặt từ web DeskMon (nếu có)
  if (p.begin("deskmon", true)) {
    const String s = p.getString("wssid", "");
    if (s.length()) {
      ssid = s;
      pass = p.getString("wpass", "");
    }
    p.end();
  }
  if (!ssid.length()) return;
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.setSleep(false);                       // giảm độ trễ khi nhận hình qua WiFi
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid.c_str(), pass.c_str());
}

void netLoop() {
  if (!started && netWifiOk()) startServices();
  if (started) server.handleClient();
}

bool netWifiOk() { return WiFi.status() == WL_CONNECTED; }

String netIp() { return netWifiOk() ? WiFi.localIP().toString() : String(""); }
