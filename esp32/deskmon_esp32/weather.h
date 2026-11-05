// Thời tiết từ Open-Meteo (miễn phí, không cần API key).
#pragma once
#include <stdint.h>

struct WeatherDay {
  int code;       // mã thời tiết WMO
  float tmax;
  float tmin;
  int rainProb;   // % khả năng mưa
};

struct Weather {
  bool valid;
  float temp;
  float feels;
  int humidity;   // %
  int code;
  bool isDay;
  WeatherDay days[4];   // [0] = hôm nay, [1..3] = 3 ngày tới
};

struct AirQuality {
  bool valid;
  int aqi;        // US AQI
  float pm25;     // µg/m³
};

enum class WxIcon : uint8_t { Sun, Moon, PartSun, PartMoon, Cloud, Fog, Drizzle, Rain, Storm, Snow };

// Gọi khi đã có WiFi. Trả về false nếu lỗi mạng/JSON; `out` chỉ được ghi khi thành công.
bool fetchWeather(float lat, float lon, Weather& out);
bool fetchAirQuality(float lat, float lon, AirQuality& out);

// Mức AQI: 0 tốt, 1 trung bình, 2 kém, 3 xấu, 4 rất xấu, 5 nguy hại
int aqiLevel(int aqi);
const char* aqiText(int level);

const char* weatherText(int wmoCode);
WxIcon weatherIcon(int wmoCode, bool isDay);
