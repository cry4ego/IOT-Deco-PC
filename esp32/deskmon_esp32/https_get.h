// Tải qua HTTPS với giới hạn thời gian ở mọi bước. HTTPClient tự nó chỉ giới hạn phần đầu:
// bắt tay TLS mặc định chờ 120 giây, đọc nội dung thì không có hạn, mạng đứng là treo mãi.
#pragma once
#include <Arduino.h>

// true = đã nhận đủ nội dung. false = lỗi, hoặc sink không nhận nữa (ví dụ đã đủ dữ liệu).
// Lỗi kết nối / mã HTTP khác 200 thì ghi mô tả vào err. Mỗi lần gọi cần ~50 KB RAM.
bool httpsGet(const char* url, Stream& sink, char* err, size_t errLen);
