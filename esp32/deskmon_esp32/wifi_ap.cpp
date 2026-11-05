#include "wifi_ap.h"
#include <DNSServer.h>
#include <WiFi.h>
#include "settings.h"
#include "webui.h"

namespace {

constexpr const char* AP_SSID = "DeskMon-Setup";
constexpr uint16_t DNS_PORT = 53;

DNSServer dns;
bool active = false;
String password;

}  // namespace

void apStart() {
  if (active) return;
  if (password.isEmpty()) password = apPassword();
  WiFi.mode(WIFI_AP_STA);
  WiFi.setAutoReconnect(false);   // tự nối lại sẽ nhảy kênh liên tục, điện thoại đang nối vào AP bị rớt
  WiFi.softAP(AP_SSID, password.c_str());
  dns.setErrorReplyCode(DNSReplyCode::NoError);
  dns.start(DNS_PORT, "*", WiFi.softAPIP());
  webSetCaptive(true);
  active = true;
  Serial.printf("[wifi] AP %s %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
}

void apStop() {
  if (!active) return;
  dns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  webSetCaptive(false);
  active = false;
  Serial.println("[wifi] AP off");
}

void apLoop() {
  if (active) dns.processNextRequest();
}

bool apActive() { return active; }
const char* apSsid() { return AP_SSID; }
const char* apPass() { return password.c_str(); }
