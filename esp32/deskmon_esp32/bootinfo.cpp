#include "bootinfo.h"
#include <Preferences.h>

namespace {

constexpr const char* NVS_NS = "boot";
BootInfo info{ESP_RST_UNKNOWN, 0, 0};

bool isCrash(esp_reset_reason_t r) {
  return r == ESP_RST_PANIC || r == ESP_RST_INT_WDT || r == ESP_RST_TASK_WDT || r == ESP_RST_WDT;
}

}  // namespace

void bootInfoBegin(const char* fwVersion) {
  info.reason = esp_reset_reason();
  Preferences p;
  if (!p.begin(NVS_NS, false)) return;
  if (p.getString("fw", "") != fwVersion) {
    p.clear();
    p.putString("fw", fwVersion);
  }
  info.brownouts = p.getUInt("brownouts", 0);
  info.crashes = p.getUInt("crashes", 0);
  if (info.reason == ESP_RST_BROWNOUT) p.putUInt("brownouts", ++info.brownouts);
  else if (isCrash(info.reason)) p.putUInt("crashes", ++info.crashes);
  p.end();
}

const BootInfo& bootInfo() { return info; }

const char* resetReasonText(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON:  return "Cắm điện";
    case ESP_RST_EXT:      return "Nút reset";
    case ESP_RST_SW:       return "Khởi động lại (web / nạp firmware)";
    case ESP_RST_PANIC:    return "Lỗi phần mềm";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:      return "Treo (watchdog)";
    case ESP_RST_BROWNOUT: return "Sụt nguồn";
    case ESP_RST_DEEPSLEEP: return "Thức dậy";
    default:               return "Không rõ";
  }
}
