// Giao thức PC -> ESP32 của màn hình phụ (little-endian). Phải khớp monitor/pc/link.py.
//
//   'M' 'P' | type u8 | screen u8 | x u16 | y u16 | len u32 | crc32 u32 | payload (len byte)
//   screen: 0 = màn 2.0" (màn hình phụ), 1 = màn 1.54" (trang thông số do PC vẽ)
//
// PC gửi các vùng ảnh đã đổi của một khung hình, rồi một gói FRAME. Hết khung ESP32 trả 1 byte:
//   ACK       vẽ xong cả khung
//   NAK       có gói hỏng (sai CRC / giải mã lỗi): PC gửi lại toàn màn
//   NEED_FULL ESP32 vừa tự vẽ (màn chờ / trang trạng thái): PC gửi lại toàn màn
// HELLO chỉ để bắt tay (ESP32 trả ACK). Mỗi lúc chỉ có 1 khung đang gửi, nên bộ đệm nhận đủ dùng.
#pragma once
#include <stdint.h>

constexpr uint8_t MAGIC0 = 'M', MAGIC1 = 'P';
constexpr size_t HEADER_LEN = 16;
enum : uint8_t { P_HELLO = 0, P_JPEG = 1, P_QOI = 2, P_FRAME = 3 };
constexpr uint8_t ACK = 0x06, NAK = 0x15, NEED_FULL = 0x07;
constexpr uint32_t MAX_PAYLOAD = 60000;    // PC tự chia vùng lớn hơn

constexpr uint16_t SCREEN_W = 320, SCREEN_H = 240;
