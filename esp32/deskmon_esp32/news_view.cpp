#include "news_view.h"
#include "news.h"
#include "offline_ui.h"

namespace {

using Dev = lgfx::LGFX_Device;

constexpr uint8_t TURN_ITEMS = 3;          // số tin mỗi lượt
constexpr uint32_t PREFETCH_MS = 6000;     // tải ảnh trước: tới giờ đổi tin là có sẵn để vẽ

struct Slot {
  bool active;     // màn đang hiện tin
  bool pending;    // đã xin tin kế tiếp, chờ task nền tải ảnh
  uint8_t shown;   // số tin đã hiện trong lượt
  uint32_t nextAt; // lúc đổi tin / hết lượt / bắt đầu lượt mới
};
Slot slots[2];

bool reached(uint32_t at) { return (int32_t)(millis() - at) >= 0; }

// Trả về true nếu màn vừa hết lượt
bool tickScreen(int i, Dev& d, const struct tm& now, bool want, uint32_t itemMs, uint32_t gapMs) {
  Slot& s = slots[i];
  if (!want) {
    if (s.pending && newsCardReady(i)) {
      newsCardDone(i);
      s.pending = false;
    }
    const bool wasActive = s.active;
    s.active = false;
    s.shown = 0;
    s.nextAt = millis() + gapMs;   // cho phép hiện tin trở lại thì màn thường vẫn được hiện trước
    return wasActive;
  }
  if (s.pending) {
    const NewsCard* card = newsCardReady(i);
    if (!card || !reached(s.nextAt)) return false;
    drawNewsCard(d, *card, now);
    newsCardDone(i);
    s.pending = false;
    s.active = true;
    s.shown++;
    s.nextAt = millis() + itemMs;
    return false;
  }
  if (s.active && gapMs > 0 && s.shown >= TURN_ITEMS) {
    if (!reached(s.nextAt)) return false;
    s.active = false;
    s.shown = 0;
    s.nextAt = millis() + gapMs;
    return true;
  }
  if (reached(s.nextAt - PREFETCH_MS)) {
    s.pending = newsRequestCard(i, d.width(), newsImageHeight(d.width()));
  }
  return false;
}

}  // namespace

void newsViewReset(uint32_t gapMs) {
  for (Slot& s : slots) {
    s.active = false;
    s.shown = 0;
    s.nextAt = millis() + gapMs;
  }
}

uint8_t newsViewTick(Dev& lyr, Dev& sta, const struct tm& now, uint8_t mask, uint32_t itemMs, uint32_t gapMs) {
  uint8_t released = 0;
  if (tickScreen(0, lyr, now, mask & 1, itemMs, gapMs)) released |= 1;
  if (tickScreen(1, sta, now, mask & 2, itemMs, gapMs)) released |= 2;
  return released;
}

bool newsViewActive(int screen) { return slots[screen].active; }

void newsViewRelease() {
  for (int i = 0; i < 2; i++) {
    if (slots[i].pending && newsCardReady(i)) {
      newsCardDone(i);
      slots[i].pending = false;
    }
  }
}
