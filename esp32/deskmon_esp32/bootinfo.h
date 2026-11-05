// Lý do ESP32 khởi động lại (sụt nguồn, lỗi phần mềm...) + bộ đếm lưu trong NVS,
// hiện trên web để biết board có tự reset lúc không ai nhìn hay không.
#pragma once
#include <esp_system.h>
#include <stdint.h>

struct BootInfo {
  esp_reset_reason_t reason;   // lý do của lần khởi động này
  uint32_t brownouts;          // số lần sụt nguồn kể từ khi nạp firmware này
  uint32_t crashes;            // số lần lỗi phần mềm / watchdog kể từ khi nạp firmware này
};

// Gọi đầu setup(), trước khi bật WiFi (lúc đó dòng tiêu thụ còn thấp, ghi NVS an toàn).
// Nạp firmware khác phiên bản -> bộ đếm về 0.
void bootInfoBegin(const char* fwVersion);
const BootInfo& bootInfo();
const char* resetReasonText(esp_reset_reason_t r);
