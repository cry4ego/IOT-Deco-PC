// Lịch hiện tin khi PC tắt: mỗi màn có lượt riêng. Hết lượt, màn trở về nội dung thường
// (đồng hồ, thời tiết...) trong gapMs rồi đến lượt mới; gapMs = 0 thì chỉ hiện tin.
#pragma once
#ifndef LGFX_USE_V1
#define LGFX_USE_V1
#endif
#include <LovyanGFX.hpp>
#include <time.h>

// Gọi khi vào chế độ offline: nội dung thường hiện trước, gapMs sau mới đến tin.
void newsViewReset(uint32_t gapMs);

// mask: màn được hiện tin (bit 0 = 2.0", bit 1 = 1.54"). Trả về bit các màn vừa hết lượt, cần vẽ lại.
uint8_t newsViewTick(lgfx::LGFX_Device& lyr, lgfx::LGFX_Device& sta, const struct tm& now, uint8_t mask,
                     uint32_t itemMs, uint32_t gapMs);

bool newsViewActive(int screen);           // 0 = 2.0", 1 = 1.54": màn đang hiện tin

// PC đang vẽ màn: trả lại bộ nhớ ảnh của các tin đã tải sẵn mà không còn dùng
void newsViewRelease();
