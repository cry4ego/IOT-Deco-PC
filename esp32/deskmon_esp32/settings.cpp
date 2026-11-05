#include "settings.h"
#include <Preferences.h>
#include <math.h>

namespace {

constexpr const char* NVS_NS = "deskmon";
constexpr size_t AP_PASS_LEN = 8;
constexpr char AP_PASS_CHARS[] = "23456789abcdefghjkmnpqrstuvwxyz";   // bỏ 0/o, 1/l/i cho dễ gõ
Settings current;
uint32_t version = 0;

double clampNum(JsonVariantConst v, double lo, double hi, double fallback) {
  if (!v.is<double>()) return fallback;
  double x = v.as<double>();
  if (isnan(x)) return fallback;
  return x < lo ? lo : (x > hi ? hi : x);
}

// Chép chuỗi UTF-8, cắt ở ranh giới ký tự để không làm hỏng chữ có dấu
void utf8Copy(char* dst, size_t n, JsonVariantConst v) {
  const char* s = v.is<const char*>() ? v.as<const char*>() : "";
  size_t len = strlcpy(dst, s, n);
  if (len < n) return;
  len = n - 1;
  while (len > 0 && ((uint8_t)dst[len] & 0xC0) == 0x80) len--;   // dst[len] là byte tiếp nối
  dst[len] = '\0';
}

Settings parse(JsonVariantConst in, const Settings& base) {
  Settings s = base;

  JsonVariantConst c = in["clock"];
  if (!c.isNull()) s.clockSize = (uint8_t)clampNum(c["size"], 0, 2, s.clockSize);

  JsonVariantConst d = in["display"];
  if (!d.isNull()) {
    s.brightDay = (uint8_t)clampNum(d["brightDay"], 5, 255, s.brightDay);
    s.brightNight = (uint8_t)clampNum(d["brightNight"], 0, 255, s.brightNight);
    s.nightFrom = (uint8_t)clampNum(d["nightFrom"], 0, 23, s.nightFrom);
    s.nightTo = (uint8_t)clampNum(d["nightTo"], 0, 23, s.nightTo);
    s.pageSec = (uint8_t)clampNum(d["pageSec"], 5, 120, s.pageSec);
  }

  JsonVariantConst w = in["weather"];
  if (!w.isNull()) {
    if (w["place"].is<const char*>()) utf8Copy(s.place, sizeof(s.place), w["place"]);
    s.lat = (float)clampNum(w["lat"], -90, 90, s.lat);
    s.lon = (float)clampNum(w["lon"], -180, 180, s.lon);
    s.weatherMin = (uint8_t)clampNum(w["refreshMin"], 5, 120, s.weatherMin);
    if (w["aqi"].is<bool>()) s.aqi = w["aqi"].as<bool>();
  }

  JsonArrayConst events = in["events"];
  if (!events.isNull()) {
    s.eventCount = 0;
    for (JsonVariantConst e : events) {
      if (s.eventCount >= MAX_EVENTS) break;
      EventItem item{};
      utf8Copy(item.name, sizeof(item.name), e["name"]);
      if (!item.name[0]) continue;
      item.lunar = e["lunar"] | false;
      item.day = (uint8_t)clampNum(e["day"], 1, item.lunar ? 30 : 31, 1);
      item.month = (uint8_t)clampNum(e["month"], 1, 12, 1);
      s.events[s.eventCount++] = item;
    }
  }

  JsonArrayConst reminders = in["reminders"];
  if (!reminders.isNull()) {
    s.reminderCount = 0;
    for (JsonVariantConst r : reminders) {
      if (s.reminderCount >= MAX_REMINDERS) break;
      Reminder item{};
      int hh = -1, mm = -1;
      const char* t = r["time"] | "";
      if (sscanf(t, "%d:%d", &hh, &mm) != 2 || hh < 0 || hh > 23 || mm < 0 || mm > 59) continue;
      item.hour = (uint8_t)hh;
      item.minute = (uint8_t)mm;
      item.days = (uint8_t)clampNum(r["days"], 0, 127, 127);
      if (item.days == 0) item.days = 127;
      item.on = r["on"] | true;
      utf8Copy(item.text, sizeof(item.text), r["text"]);
      s.reminders[s.reminderCount++] = item;
    }
  }

  JsonVariantConst pc = in["pc"];
  if (!pc.isNull()) {
    s.lyricsLead = (float)clampNum(pc["lyricsLead"], -2, 3, s.lyricsLead);
    s.hotCpu = (uint8_t)clampNum(pc["hotCpu"], 50, 110, s.hotCpu);
    s.hotGpu = (uint8_t)clampNum(pc["hotGpu"], 50, 110, s.hotGpu);
    if (pc["extraPage"].is<bool>()) s.pcExtraPage = pc["extraPage"].as<bool>();
    s.pcPageSec = (uint8_t)clampNum(pc["pageSec"], 5, 120, s.pcPageSec);
  }

  JsonVariantConst n = in["news"];
  if (!n.isNull()) {
    if (n["on"].is<bool>()) s.newsOn = n["on"].as<bool>();
    s.newsOffline = (uint8_t)clampNum(n["offline"], 0, 3, s.newsOffline);
    s.newsOnline = (uint8_t)clampNum(n["online"], 0, 3, s.newsOnline);
    if (n["domestic"].is<bool>()) s.newsDomestic = n["domestic"].as<bool>();
    if (n["world"].is<bool>()) s.newsWorld = n["world"].as<bool>();
    s.newsItemSec = (uint8_t)clampNum(n["itemSec"], 3, 120, s.newsItemSec);
    s.newsGapSec = (uint8_t)clampNum(n["gapSec"], 0, 250, s.newsGapSec);
    s.newsRefreshMin = (uint8_t)clampNum(n["refreshMin"], 1, 120, s.newsRefreshMin);
  }
  return s;
}

void persist() {
  JsonDocument doc;
  settingsToJson(doc.to<JsonObject>());
  String raw;
  serializeJson(doc, raw);
  Preferences p;
  p.begin(NVS_NS, false);
  p.putString("cfg", raw);
  p.putUInt("ver", version);
  p.end();
}

}  // namespace

void settingsBegin() {
  Preferences p;
  String raw;
  if (p.begin(NVS_NS, true)) {
    raw = p.getString("cfg", "");
    version = p.getUInt("ver", 0);
    p.end();
  }
  if (raw.length() == 0) return;
  JsonDocument doc;
  if (deserializeJson(doc, raw)) return;   // hỏng -> dùng mặc định
  current = parse(doc.as<JsonVariantConst>(), Settings{});
}

const Settings& settings() { return current; }
uint32_t settingsVersion() { return version; }

bool settingsUpdate(JsonVariantConst in, String& err) {
  if (!in.is<JsonObjectConst>()) {
    err = "Dữ liệu phải là JSON object";
    return false;
  }
  current = parse(in, current);
  version++;
  persist();
  return true;
}

void settingsToJson(JsonObject out) {
  const Settings& s = current;
  out["clock"]["size"] = s.clockSize;

  JsonObject d = out["display"].to<JsonObject>();
  d["brightDay"] = s.brightDay;
  d["brightNight"] = s.brightNight;
  d["nightFrom"] = s.nightFrom;
  d["nightTo"] = s.nightTo;
  d["pageSec"] = s.pageSec;

  JsonObject w = out["weather"].to<JsonObject>();
  w["place"] = s.place;
  w["lat"] = s.lat;
  w["lon"] = s.lon;
  w["refreshMin"] = s.weatherMin;
  w["aqi"] = s.aqi;

  JsonArray events = out["events"].to<JsonArray>();
  for (int i = 0; i < s.eventCount; i++) {
    JsonObject e = events.add<JsonObject>();
    e["name"] = s.events[i].name;
    e["day"] = s.events[i].day;
    e["month"] = s.events[i].month;
    e["lunar"] = s.events[i].lunar;
  }

  JsonArray reminders = out["reminders"].to<JsonArray>();
  for (int i = 0; i < s.reminderCount; i++) {
    const Reminder& r = s.reminders[i];
    JsonObject o = reminders.add<JsonObject>();
    char t[6];
    snprintf(t, sizeof(t), "%02u:%02u", r.hour, r.minute);
    o["time"] = t;
    o["days"] = r.days;
    o["on"] = r.on;
    o["text"] = r.text;
  }

  JsonObject n = out["news"].to<JsonObject>();
  n["on"] = s.newsOn;
  n["offline"] = s.newsOffline;
  n["online"] = s.newsOnline;
  n["domestic"] = s.newsDomestic;
  n["world"] = s.newsWorld;
  n["itemSec"] = s.newsItemSec;
  n["gapSec"] = s.newsGapSec;
  n["refreshMin"] = s.newsRefreshMin;

  pcSettingsToJson(out["pc"].to<JsonObject>());
}

void pcSettingsToJson(JsonObject pc) {
  const Settings& s = current;
  pc["lyricsLead"] = s.lyricsLead;
  pc["hotCpu"] = s.hotCpu;
  pc["hotGpu"] = s.hotGpu;
  pc["extraPage"] = s.pcExtraPage;
  pc["pageSec"] = s.pcPageSec;
  pc["brightDay"] = s.brightDay;
  pc["brightNight"] = s.brightNight;
  pc["nightFrom"] = s.nightFrom;
  pc["nightTo"] = s.nightTo;
  pc["newsOn"] = s.newsOn;
  pc["newsOnline"] = s.newsOnline;
  pc["newsDomestic"] = s.newsDomestic;
  pc["newsWorld"] = s.newsWorld;
  pc["newsItemSec"] = s.newsItemSec;
  pc["newsGapSec"] = s.newsGapSec;
  pc["newsRefreshMin"] = s.newsRefreshMin;
}

bool wifiOverride(String& ssid, String& pass) {
  Preferences p;
  if (!p.begin(NVS_NS, true)) return false;
  ssid = p.getString("wssid", "");
  pass = p.getString("wpass", "");
  p.end();
  return ssid.length() > 0;
}

void saveWifiOverride(const char* ssid, const char* pass) {
  Preferences p;
  p.begin(NVS_NS, false);
  p.putString("wssid", ssid);
  p.putString("wpass", pass);
  p.end();
}

String apPassword() {
  Preferences p;
  p.begin(NVS_NS, false);
  String pass = p.getString("appass", "");
  if (pass.length() != AP_PASS_LEN) {
    pass = "";
    for (size_t i = 0; i < AP_PASS_LEN; i++) pass += AP_PASS_CHARS[esp_random() % (sizeof(AP_PASS_CHARS) - 1)];
    p.putString("appass", pass);
  }
  p.end();
  return pass;
}
