#include "weather.h"
#include <Arduino.h>
#include <ArduinoJson.h>
#include "https_get.h"

namespace {

class StringSink : public Stream {
 public:
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t* data, size_t n) override {
    body.concat((const char*)data, n);
    return n;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  String body;
};

bool getJson(const char* url, JsonDocument& doc) {
  StringSink sink;
  char error[96] = "";
  if (!httpsGet(url, sink, error, sizeof(error))) {
    Serial.printf("[weather] %s\n", error);
    return false;
  }
  DeserializationError err = deserializeJson(doc, sink.body);
  if (err) {
    Serial.printf("[weather] JSON: %s\n", err.c_str());
    return false;
  }
  return true;
}

}  // namespace

bool fetchAirQuality(float lat, float lon, AirQuality& out) {
  char url[200];
  snprintf(url, sizeof(url),
           "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=%.4f&longitude=%.4f"
           "&current=us_aqi,pm2_5&timezone=Asia%%2FHo_Chi_Minh",
           lat, lon);
  JsonDocument doc;
  if (!getJson(url, doc)) return false;
  JsonObject cur = doc["current"];
  if (cur.isNull() || !cur["us_aqi"].is<int>()) return false;
  out = AirQuality{true, cur["us_aqi"].as<int>(), cur["pm2_5"] | NAN};
  return true;
}

int aqiLevel(int aqi) {
  if (aqi <= 50) return 0;
  if (aqi <= 100) return 1;
  if (aqi <= 150) return 2;
  if (aqi <= 200) return 3;
  if (aqi <= 300) return 4;
  return 5;
}

const char* aqiText(int level) {
  static const char* const TEXT[6] = {"Tốt", "Trung bình", "Kém", "Xấu", "Rất xấu", "Nguy hại"};
  return TEXT[level < 0 ? 0 : (level > 5 ? 5 : level)];
}

bool fetchWeather(float lat, float lon, Weather& out) {
  char url[360];
  snprintf(url, sizeof(url),
           "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
           "&timezone=Asia%%2FHo_Chi_Minh&forecast_days=4",
           lat, lon);

  JsonDocument doc;
  if (!getJson(url, doc)) return false;
  JsonObject cur = doc["current"];
  JsonObject daily = doc["daily"];
  if (cur.isNull() || daily.isNull()) return false;

  Weather w{};
  w.temp = cur["temperature_2m"] | NAN;
  w.feels = cur["apparent_temperature"] | NAN;
  w.humidity = cur["relative_humidity_2m"] | -1;
  w.code = cur["weather_code"] | -1;
  w.isDay = (cur["is_day"] | 1) == 1;
  for (int i = 0; i < 4; i++) {
    w.days[i].code = daily["weather_code"][i] | -1;
    w.days[i].tmax = daily["temperature_2m_max"][i] | NAN;
    w.days[i].tmin = daily["temperature_2m_min"][i] | NAN;
    w.days[i].rainProb = daily["precipitation_probability_max"][i] | -1;
  }
  w.valid = !isnan(w.temp) && w.code >= 0;
  if (!w.valid) return false;
  out = w;
  return true;
}

const char* weatherText(int c) {
  switch (c) {
    case 0: return "Trời quang";
    case 1: return "Ít mây";
    case 2: return "Có mây";
    case 3: return "Nhiều mây";
    case 45: case 48: return "Sương mù";
    case 51: case 53: case 55: return "Mưa phùn";
    case 56: case 57: return "Mưa phùn lạnh";
    case 61: return "Mưa nhỏ";
    case 63: return "Mưa vừa";
    case 65: return "Mưa to";
    case 66: case 67: return "Mưa lạnh";
    case 71: case 73: case 75: case 77: return "Tuyết";
    case 80: return "Mưa rào nhẹ";
    case 81: return "Mưa rào";
    case 82: return "Mưa rào rất to";
    case 85: case 86: return "Mưa tuyết";
    case 95: return "Dông";
    case 96: case 99: return "Dông, mưa đá";
    default: return "--";
  }
}

WxIcon weatherIcon(int c, bool isDay) {
  if (c == 0) return isDay ? WxIcon::Sun : WxIcon::Moon;
  if (c == 1 || c == 2) return isDay ? WxIcon::PartSun : WxIcon::PartMoon;
  if (c == 3) return WxIcon::Cloud;
  if (c == 45 || c == 48) return WxIcon::Fog;
  if (c >= 51 && c <= 57) return WxIcon::Drizzle;
  if ((c >= 61 && c <= 67) || (c >= 80 && c <= 82)) return WxIcon::Rain;
  if ((c >= 71 && c <= 77) || c == 85 || c == 86) return WxIcon::Snow;
  if (c >= 95) return WxIcon::Storm;
  return WxIcon::Cloud;
}
