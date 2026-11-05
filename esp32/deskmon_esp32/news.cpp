#include "news.h"
#include <WiFi.h>
#include <atomic>
#include "https_get.h"

namespace {

constexpr int FEEDS = 2;                   // 0 = trong nước, 1 = thế giới
constexpr int PER_FEED = 10;
constexpr int TOTAL = FEEDS * PER_FEED;
constexpr const char* FEED_URL[FEEDS] = {"https://vnexpress.net/rss/thoi-su.rss",
                                         "https://vnexpress.net/rss/the-gioi.rss"};
// Ảnh gốc rộng 1200 px, ESP32 không thu nhỏ nổi: nhờ wsrv.nl đổi sang JPEG thường, đúng cỡ màn.
constexpr const char* IMAGE_PROXY = "https://wsrv.nl/?url=%s&w=%d&h=%d&fit=cover&output=jpg&q=60";

constexpr uint32_t RETRY_MS = 60000;
constexpr size_t RSS_BUF = 3072;           // đủ chứa 1 <item> (~1,2 KB)
constexpr size_t JPEG_MAX = 16 * 1024;     // ảnh qua wsrv.nl thường dưới 10 KB
constexpr int IMAGE_ATTEMPTS = 2;
constexpr uint32_t IMAGE_RETRY_MS = 500;
constexpr uint32_t TASK_STACK = 12288;
constexpr uint32_t TASK_IDLE_MS = 100;

enum : uint8_t { CARD_IDLE, CARD_PENDING, CARD_READY };

struct CardSlot {
  NewsCard card;
  int w, h;
  std::atomic<uint8_t> state;              // PENDING: task nền giữ card. IDLE/READY: loop() giữ
};

SemaphoreHandle_t storeLock = nullptr;     // bảo vệ store, counts, cursor, cfg
SemaphoreHandle_t netLock = nullptr;
TaskHandle_t taskHandle = nullptr;
NewsItem* store = nullptr;                 // TOTAL phần tử, xếp xen kẽ trong nước / thế giới
int counts[FEEDS] = {0, 0};
uint32_t cursor = 0;
NewsConfig cfg{false, true, true, 5};
bool refreshAsked = false;
uint32_t lastOkMs = 0;
CardSlot slots[2];
char lastError[96] = "";                   // lỗi tải gần nhất, xem ở /api/news
uint32_t imagesOk = 0, imagesFailed = 0;

struct Lock {
  SemaphoreHandle_t m;
  explicit Lock(SemaphoreHandle_t mutex) : m(mutex) { xSemaphoreTake(m, portMAX_DELAY); }
  ~Lock() { xSemaphoreGive(m); }
};

// ---------------------------------------------------------------- làm sạch chữ
// Ghi có giới hạn: gộp khoảng trắng liên tiếp, bỏ khoảng trắng đầu/cuối.
struct TextOut {
  char* dst;
  size_t cap;
  size_t len;
  bool pendingSpace;

  void raw(char c) {
    if (len + 1 < cap) dst[len++] = c;
  }
  void put(char c) {
    if (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
      pendingSpace = len > 0;
      return;
    }
    if (pendingSpace) raw(' ');
    pendingSpace = false;
    raw(c);
  }
  void text(const char* s) {
    while (*s) put(*s++);
  }
  // Bị đầy thì có thể cắt giữa một chữ có dấu: bỏ nốt ký tự dở
  void finish() {
    size_t i = len;
    while (i > 0 && ((uint8_t)dst[i - 1] & 0xC0) == 0x80) i--;
    if (i > 0) {
      const uint8_t lead = (uint8_t)dst[i - 1];
      const size_t need = lead < 0x80 ? 1 : (lead >> 5) == 6 ? 2 : (lead >> 4) == 14 ? 3 : 4;
      if (len - (i - 1) < need) len = i - 1;
    } else {
      len = 0;
    }
    dst[len] = '\0';
  }
};

// Phông VLW không có các ký tự này: đổi sang ký tự gần giống
const char* fontReplacement(uint32_t cp) {
  switch (cp) {
    case 0xA0: return " ";
    case 0x2018: case 0x2019: return "'";
    case 0x201C: case 0x201D: return "\"";
    case 0x2014: return "–";
    case 0x2026: return "...";
    default: return nullptr;
  }
}

void putCodepoint(TextOut& out, uint32_t cp) {
  if (const char* r = fontReplacement(cp)) {
    out.text(r);
    return;
  }
  const bool strayMark = cp >= 0x0300 && cp < 0x0370;
  if (cp == 0 || strayMark || (cp >= 0x2000 && cp != 0x2013)) return;   // phông không có: dấu lẻ, biểu tượng, emoji
  if (cp < 0x80) {
    out.put((char)cp);
  } else if (cp < 0x800) {
    out.put((char)(0xC0 | (cp >> 6)));
    out.put((char)(0x80 | (cp & 0x3F)));
  } else {
    out.put((char)(0xE0 | (cp >> 12)));
    out.put((char)(0x80 | ((cp >> 6) & 0x3F)));
    out.put((char)(0x80 | (cp & 0x3F)));
  }
}

// Đọc 1 ký tự UTF-8, trả về số byte đã dùng (byte hỏng tính là 1, cp = 0xFFFD)
size_t decodeUtf8(const char* s, size_t n, uint32_t& cp) {
  const uint8_t c = (uint8_t)s[0];
  const size_t need = c < 0x80 ? 1 : (c >> 5) == 6 ? 2 : (c >> 4) == 14 ? 3 : (c >> 3) == 30 ? 4 : 0;
  if (need == 0 || need > n) {
    cp = 0xFFFD;
    return 1;
  }
  cp = need == 1 ? c : c & (0xFF >> (need + 1));
  for (size_t i = 1; i < need; i++) {
    if (((uint8_t)s[i] & 0xC0) != 0x80) {
      cp = 0xFFFD;
      return 1;
    }
    cp = (cp << 6) | ((uint8_t)s[i] & 0x3F);
  }
  return need;
}

// Một số bài dùng dấu rời ("ho" + dấu huyền) mà phông chỉ có chữ đã ghép: ghép lại.
// Thứ tự dấu thanh: huyền, sắc, hỏi, ngã, nặng.
constexpr uint16_t TONE_MARKS[5] = {0x0300, 0x0301, 0x0309, 0x0303, 0x0323};
const struct { uint16_t base; uint16_t toned[5]; } TONED[] = {
    {0x0061, {0x00E0, 0x00E1, 0x1EA3, 0x00E3, 0x1EA1}},  // a
    {0x0103, {0x1EB1, 0x1EAF, 0x1EB3, 0x1EB5, 0x1EB7}},  // ă
    {0x00E2, {0x1EA7, 0x1EA5, 0x1EA9, 0x1EAB, 0x1EAD}},  // â
    {0x0065, {0x00E8, 0x00E9, 0x1EBB, 0x1EBD, 0x1EB9}},  // e
    {0x00EA, {0x1EC1, 0x1EBF, 0x1EC3, 0x1EC5, 0x1EC7}},  // ê
    {0x0069, {0x00EC, 0x00ED, 0x1EC9, 0x0129, 0x1ECB}},  // i
    {0x006F, {0x00F2, 0x00F3, 0x1ECF, 0x00F5, 0x1ECD}},  // o
    {0x00F4, {0x1ED3, 0x1ED1, 0x1ED5, 0x1ED7, 0x1ED9}},  // ô
    {0x01A1, {0x1EDD, 0x1EDB, 0x1EDF, 0x1EE1, 0x1EE3}},  // ơ
    {0x0075, {0x00F9, 0x00FA, 0x1EE7, 0x0169, 0x1EE5}},  // u
    {0x01B0, {0x1EEB, 0x1EE9, 0x1EED, 0x1EEF, 0x1EF1}},  // ư
    {0x0079, {0x1EF3, 0x00FD, 0x1EF7, 0x1EF9, 0x1EF5}},  // y
    {0x0041, {0x00C0, 0x00C1, 0x1EA2, 0x00C3, 0x1EA0}},  // A
    {0x0102, {0x1EB0, 0x1EAE, 0x1EB2, 0x1EB4, 0x1EB6}},  // Ă
    {0x00C2, {0x1EA6, 0x1EA4, 0x1EA8, 0x1EAA, 0x1EAC}},  // Â
    {0x0045, {0x00C8, 0x00C9, 0x1EBA, 0x1EBC, 0x1EB8}},  // E
    {0x00CA, {0x1EC0, 0x1EBE, 0x1EC2, 0x1EC4, 0x1EC6}},  // Ê
    {0x0049, {0x00CC, 0x00CD, 0x1EC8, 0x0128, 0x1ECA}},  // I
    {0x004F, {0x00D2, 0x00D3, 0x1ECE, 0x00D5, 0x1ECC}},  // O
    {0x00D4, {0x1ED2, 0x1ED0, 0x1ED4, 0x1ED6, 0x1ED8}},  // Ô
    {0x01A0, {0x1EDC, 0x1EDA, 0x1EDE, 0x1EE0, 0x1EE2}},  // Ơ
    {0x0055, {0x00D9, 0x00DA, 0x1EE6, 0x0168, 0x1EE4}},  // U
    {0x01AF, {0x1EEA, 0x1EE8, 0x1EEC, 0x1EEE, 0x1EF0}},  // Ư
    {0x0059, {0x1EF2, 0x00DD, 0x1EF6, 0x1EF8, 0x1EF4}},  // Y
};

int toneIndex(uint32_t mark) {
  for (int i = 0; i < 5; i++) {
    if (TONE_MARKS[i] == mark) return i;
  }
  return -1;
}

bool isVowelMark(uint32_t mark) { return mark == 0x0302 || mark == 0x0306 || mark == 0x031B; }

uint32_t applyVowelMark(uint32_t c, uint32_t mark) {
  static const struct { uint16_t base, mark, result; } MARKED[] = {
      {'a', 0x0302, 0x00E2}, {'A', 0x0302, 0x00C2}, {'e', 0x0302, 0x00EA}, {'E', 0x0302, 0x00CA},
      {'o', 0x0302, 0x00F4}, {'O', 0x0302, 0x00D4}, {'a', 0x0306, 0x0103}, {'A', 0x0306, 0x0102},
      {'o', 0x031B, 0x01A1}, {'O', 0x031B, 0x01A0}, {'u', 0x031B, 0x01B0}, {'U', 0x031B, 0x01AF}};
  for (const auto& m : MARKED) {
    if (m.base == c && m.mark == mark) return m.result;
  }
  return c;
}

uint32_t applyTone(uint32_t c, int tone) {
  for (const auto& row : TONED) {
    if (row.base == c) return row.toned[tone];
  }
  return c;
}

// s trỏ vào '&', entity dài len byte (tính cả ';'). Trả về false nếu không nhận ra.
bool putEntity(TextOut& out, const char* s, size_t len) {
  static const struct { const char* name; char c; } NAMED[] = {
      {"&amp;", '&'}, {"&lt;", '<'}, {"&gt;", '>'}, {"&quot;", '"'}, {"&apos;", '\''}, {"&nbsp;", ' '}};
  for (const auto& e : NAMED) {
    if (strlen(e.name) == len && strncmp(s, e.name, len) == 0) {
      out.put(e.c);
      return true;
    }
  }
  if (len < 4 || s[1] != '#') return false;
  const bool hex = s[2] == 'x' || s[2] == 'X';
  const uint32_t cp = strtoul(s + (hex ? 3 : 2), nullptr, hex ? 16 : 10);
  if (cp == 0 || cp > 0x10FFFF) return false;
  putCodepoint(out, cp);
  return true;
}

// Chép chữ từ RSS: bỏ thẻ HTML, giải mã &amp;..., đổi ký tự phông không có.
void cleanText(const char* s, size_t n, char* dst, size_t cap) {
  constexpr size_t ENTITY_MAX = 10;
  TextOut out{dst, cap, 0, false};
  for (size_t i = 0; i < n;) {
    if (s[i] == '<') {
      const char* end = (const char*)memchr(s + i, '>', n - i);
      if (!end) break;
      i = (size_t)(end - s) + 1;
      out.put(' ');
      continue;
    }
    if (s[i] == '&') {
      const char* semi = (const char*)memchr(s + i, ';', min(n - i, ENTITY_MAX));
      if (semi && putEntity(out, s + i, (size_t)(semi - (s + i)) + 1)) {
        i = (size_t)(semi - s) + 1;
        continue;
      }
    }
    uint32_t cp;
    i += decodeUtf8(s + i, n - i, cp);
    uint32_t vowelMark = 0;                 // dấu rời ngay sau chữ: ghép dấu mũ/móc trước, dấu thanh sau
    int tone = -1;
    while (i < n) {
      uint32_t mark;
      const size_t used = decodeUtf8(s + i, n - i, mark);
      if (isVowelMark(mark)) vowelMark = mark;
      else if (toneIndex(mark) >= 0) tone = toneIndex(mark);
      else break;
      i += used;
    }
    if (vowelMark) cp = applyVowelMark(cp, vowelMark);
    if (tone >= 0) cp = applyTone(cp, tone);
    if (cp != 0xFFFD) putCodepoint(out, cp);
  }
  out.finish();
}

// ---------------------------------------------------------------- đọc RSS
// Nội dung giữa <tag>...</tag> của một item, đã bỏ bọc CDATA
bool tagBody(const char* xml, const char* tag, const char*& body, size_t& len) {
  char open[24], close[24];
  snprintf(open, sizeof(open), "<%s>", tag);
  snprintf(close, sizeof(close), "</%s>", tag);
  const char* a = strstr(xml, open);
  if (!a) return false;
  a += strlen(open);
  const char* b = strstr(a, close);
  if (!b) return false;
  if (strncmp(a, "<![CDATA[", 9) == 0) {
    a += 9;
    const char* c = strstr(a, "]]>");
    if (c && c < b) b = c;
  }
  body = a;
  len = (size_t)(b - a);
  return true;
}

// Giá trị thuộc tính, ví dụ key = url=" -> chuỗi tới dấu " kế tiếp
bool attrValue(const char* from, const char* key, const char*& value, size_t& len) {
  const char* a = strstr(from, key);
  if (!a) return false;
  a += strlen(key);
  const char* b = strchr(a, '"');
  if (!b) return false;
  value = a;
  len = (size_t)(b - a);
  return true;
}

void parseImageUrl(const char* xml, char* dst, size_t cap) {
  dst[0] = '\0';
  const char* value = nullptr;
  size_t len = 0;
  const char* enclosure = strstr(xml, "<enclosure");
  const char* img = strstr(xml, "<img ");
  const bool found = (enclosure && attrValue(enclosure, "url=\"", value, len)) ||
                     (img && attrValue(img, "src=\"", value, len));
  if (!found || len >= cap || strncmp(value, "http", 4) != 0) return;   // URL bị cắt thì vô dụng
  cleanText(value, len, dst, cap);
}

bool parseItem(const char* xml, bool world, NewsItem& out) {
  const char* body = nullptr;
  size_t len = 0;
  if (!tagBody(xml, "title", body, len)) return false;
  cleanText(body, len, out.title, sizeof(out.title));
  if (!out.title[0]) return false;
  out.summary[0] = '\0';
  if (tagBody(xml, "description", body, len)) cleanText(body, len, out.summary, sizeof(out.summary));
  parseImageUrl(xml, out.image, sizeof(out.image));
  out.world = world;
  return true;
}

// Nhận RSS theo từng đoạn, cắt ra từng <item>. Đủ số tin thì trả 0 để HTTPClient dừng tải.
class RssSink : public Stream {
 public:
  RssSink(int feed, char* buf, size_t cap) : feed_(feed), buf_(buf), cap_(cap) {}

  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t* data, size_t n) override {
    size_t done = 0;
    while (done < n) {
      if (filled_ >= PER_FEED) return done;
      const size_t room = cap_ - 1 - len_;
      const size_t take = min(room, n - done);
      memcpy(buf_ + len_, data + done, take);
      len_ += take;
      buf_[len_] = '\0';
      done += take;
      drain();
    }
    return done;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }

  int filled() const { return filled_; }
  bool sawNewFirst() const { return newFirst_; }

 private:
  void drain() {
    while (char* end = strstr(buf_, "</item>")) {
      *end = '\0';
      if (const char* start = strstr(buf_, "<item>")) onItem(start);
      const size_t used = (size_t)(end - buf_) + 7;
      len_ -= used;
      memmove(buf_, end + 7, len_ + 1);
    }
    if (const char* start = strstr(buf_, "<item>")) {
      len_ -= (size_t)(start - buf_);
      memmove(buf_, start, len_ + 1);
    }
    if (len_ >= cap_ - 1) {                 // không có <item> vừa bộ đệm: bỏ, giữ đuôi phòng thẻ bị cắt
      constexpr size_t TAIL = 7;
      memmove(buf_, buf_ + len_ - TAIL, TAIL + 1);
      len_ = TAIL;
    }
  }

  void onItem(const char* xml) {
    if (filled_ >= PER_FEED) return;
    NewsItem item;
    if (!parseItem(xml, feed_ == 1, item)) return;
    Lock lock(storeLock);
    NewsItem& slot = store[filled_ * FEEDS + feed_];
    if (filled_ == 0 && strcmp(slot.title, item.title) != 0) newFirst_ = true;
    slot = item;
    filled_++;
  }

  int feed_;
  char* buf_;
  size_t cap_;
  size_t len_ = 0;
  int filled_ = 0;
  bool newFirst_ = false;
};

// Ảnh vào bộ nhớ. Chỉ cấp phát khi dữ liệu bắt đầu về: lúc đó TLS đã chiếm xong các khối lớn nó cần.
// Cấp trước thì dễ làm vỡ khối lớn, TLS hết bộ nhớ dù RAM trống vẫn còn nhiều.
class MemSink : public Stream {
 public:
  explicit MemSink(size_t cap) : cap_(cap) {}
  ~MemSink() override { free(buf_); }
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t* data, size_t n) override {
    if (!buf_) buf_ = (uint8_t*)malloc(cap_);
    if (!buf_ || n > cap_ - len_) return 0;
    memcpy(buf_ + len_, data, n);
    len_ += n;
    return n;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  bool isJpeg() const { return buf_ && len_ > 2 && buf_[0] == 0xFF && buf_[1] == 0xD8; }
  // Gọi sau khi đã đóng kết nối: TLS trả RAM rồi nên khối vừa khít này không nằm giữa khối trống lớn,
  // lượt tải kế tiếp (màn còn lại) vẫn đủ chỗ liền nhau cho TLS.
  uint8_t* take(size_t& len) {
    uint8_t* compact = (uint8_t*)malloc(len_);
    if (compact) {
      memcpy(compact, buf_, len_);
      free(buf_);
    } else {
      compact = buf_;
    }
    buf_ = nullptr;
    len = len_;
    return compact;
  }

 private:
  uint8_t* buf_ = nullptr;
  size_t cap_;
  size_t len_ = 0;
};

// ---------------------------------------------------------------- tải qua mạng (task nền)
void setLastError(const char* text) {
  Lock lock(storeLock);
  strlcpy(lastError, text, sizeof(lastError));
}

bool httpGet(const char* url, Stream& sink) {
  Lock net(netLock);
  char error[sizeof(lastError)] = "";
  const bool complete = httpsGet(url, sink, error, sizeof(error));
  if (error[0]) setLastError(error);
  return complete;
}

bool fetchFeed(int feed, bool& fresh) {
  char* buf = (char*)malloc(RSS_BUF);
  if (!buf) return false;
  buf[0] = '\0';
  RssSink sink(feed, buf, RSS_BUF);
  const bool complete = httpGet(FEED_URL[feed], sink);
  free(buf);
  if (sink.filled() == 0) return false;
  Lock lock(storeLock);
  counts[feed] = sink.filled();
  if (sink.sawNewFirst()) fresh = true;
  return complete || sink.filled() >= PER_FEED;   // tải dở thì giữ phần có được, 1 phút sau thử lại
}

bool refreshFeeds(const NewsConfig& c) {
  bool ok = true, fresh = false;
  const bool enabled[FEEDS] = {c.domestic, c.world};
  for (int feed = 0; feed < FEEDS; feed++) {
    if (enabled[feed]) {
      ok = fetchFeed(feed, fresh) && ok;
    } else {
      Lock lock(storeLock);
      counts[feed] = 0;
    }
  }
  Lock lock(storeLock);
  if (fresh) cursor = 0;                    // có tin mới -> đọc lại từ tin mới nhất
  if (ok) lastOkMs = millis();
  return ok;
}

void percentEncode(const char* src, char* dst, size_t cap) {
  static const char HEX_DIGITS[] = "0123456789ABCDEF";
  size_t o = 0;
  for (; *src && o + 4 < cap; src++) {
    const uint8_t c = (uint8_t)*src;
    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
      dst[o++] = (char)c;
    } else {
      dst[o++] = '%';
      dst[o++] = HEX_DIGITS[c >> 4];
      dst[o++] = HEX_DIGITS[c & 0x0F];
    }
  }
  dst[o] = '\0';
}

void fetchImage(CardSlot& slot) {
  const NewsItem& item = slot.card.item;
  if (!item.image[0]) return;
  char encoded[sizeof(item.image) * 3];
  percentEncode(item.image, encoded, sizeof(encoded));
  char url[sizeof(encoded) + 80];
  snprintf(url, sizeof(url), IMAGE_PROXY, encoded, slot.w, slot.h);
  // Lỗi mạng thoáng qua khá hay gặp: thử lại một lần trước khi đành hiện thẻ chỉ có chữ
  for (int attempt = 0; attempt < IMAGE_ATTEMPTS; attempt++) {
    MemSink sink(JPEG_MAX);
    if (httpGet(url, sink) && sink.isJpeg()) {
      imagesOk++;
      slot.card.jpg = sink.take(slot.card.jpgLen);
      return;
    }
    vTaskDelay(pdMS_TO_TICKS(IMAGE_RETRY_MS));
  }
  imagesFailed++;
}

void newsTask(void*) {
  uint32_t lastTryMs = 0;
  bool lastOk = false, tried = false;
  for (;;) {
    for (CardSlot& slot : slots) {
      if (slot.state == CARD_PENDING) {
        fetchImage(slot);
        slot.state = CARD_READY;
      }
    }
    NewsConfig c;
    bool asked;
    {
      Lock lock(storeLock);
      c = cfg;
      asked = refreshAsked;
    }
    const uint32_t waitMs = lastOk ? c.refreshMin * 60000UL : RETRY_MS;
    if (c.wanted && (asked || !tried || millis() - lastTryMs >= waitMs)) {
      {
        Lock lock(storeLock);
        refreshAsked = false;
      }
      lastOk = refreshFeeds(c);
      lastTryMs = millis();
      tried = true;
    }
    vTaskDelay(pdMS_TO_TICKS(TASK_IDLE_MS));
  }
}

}  // namespace

// ---------------------------------------------------------------- API
void newsBegin() {
  storeLock = xSemaphoreCreateMutex();
  netLock = xSemaphoreCreateMutex();
}

void newsConfigure(const NewsConfig& next) {
  {
    Lock lock(storeLock);
    if (next.domestic != cfg.domestic || next.world != cfg.world) refreshAsked = true;
    cfg = next;
  }
  if (!next.wanted || taskHandle) return;
  store = (NewsItem*)calloc(TOTAL, sizeof(NewsItem));
  if (!store) return;
  // Lõi 0 cùng WiFi: chờ mạng và bắt tay TLS không làm chậm vẽ màn hình ở lõi 1
  if (xTaskCreatePinnedToCore(newsTask, "news", TASK_STACK, nullptr, 1, &taskHandle, 0) != pdPASS) {
    free(store);
    store = nullptr;
    taskHandle = nullptr;
  }
}

int newsCount() {
  Lock lock(storeLock);
  return counts[0] + counts[1];
}

bool newsRequestCard(int screen, int imgW, int imgH) {
  CardSlot& slot = slots[screen];
  if (slot.state != CARD_IDLE || !store) return false;
  Lock lock(storeLock);
  for (int tries = 0; tries < TOTAL; tries++) {
    const int idx = cursor++ % TOTAL;
    if (idx / FEEDS >= counts[idx % FEEDS]) continue;
    slot.card.item = store[idx];
    slot.card.jpg = nullptr;
    slot.card.jpgLen = 0;
    slot.w = imgW;
    slot.h = imgH;
    slot.state = CARD_PENDING;
    return true;
  }
  return false;
}

const NewsCard* newsCardReady(int screen) {
  return slots[screen].state == CARD_READY ? &slots[screen].card : nullptr;
}

void newsCardDone(int screen) {
  CardSlot& slot = slots[screen];
  if (slot.state != CARD_READY) return;
  free(slot.card.jpg);
  slot.card.jpg = nullptr;
  slot.state = CARD_IDLE;
}

bool newsNetTryLock() { return xSemaphoreTake(netLock, 0) == pdTRUE; }
void newsNetUnlock() { xSemaphoreGive(netLock); }

void newsToJson(JsonObject out) {
  out["stackFree"] = taskHandle ? uxTaskGetStackHighWaterMark(taskHandle) : 0;
  out["heapMin"] = ESP.getMinFreeHeap();
  out["heapMaxBlock"] = ESP.getMaxAllocHeap();
  out["imagesOk"] = imagesOk;
  out["imagesFailed"] = imagesFailed;
  Lock lock(storeLock);
  out["lastError"] = (const char*)lastError;   // chép chuỗi, không giữ con trỏ sau khi nhả khoá
  out["count"] = counts[0] + counts[1];
  if (lastOkMs) out["ageSec"] = (millis() - lastOkMs) / 1000;
  JsonArray items = out["items"].to<JsonArray>();
  if (!store) return;
  for (int idx = 0; idx < TOTAL; idx++) {
    if (idx / FEEDS >= counts[idx % FEEDS]) continue;
    JsonObject o = items.add<JsonObject>();
    o["title"] = store[idx].title;
    o["summary"] = store[idx].summary;
    o["world"] = store[idx].world;
    o["image"] = store[idx].image[0] != '\0';
  }
}
