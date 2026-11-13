# Màn hình phụ 320×240: màn 2.0" + ESP32

Biến màn 2.0" của DeskMon thành **màn hình thứ 3 thật của Windows**: kéo cửa sổ sang, rê chuột, xem video, mọi thứ hiện trên màn nhỏ.

Đây là **dự án riêng**. Code DeskMon ở thư mục gốc (`esp32/`, `pc/`) giữ nguyên, không sửa dòng nào. Firmware ở đây chỉ *đọc* lại `secrets.h` (WiFi) và `fonts_vi.h` (phông) của DeskMon.

## Cách hoạt động

```
Windows ──(driver màn hình ảo 640×480)──► man_phu.py chụp màn ảo + con trỏ chuột
        ──► thu nhỏ 320×240, chỉ lấy vùng thay đổi, nén JPEG/QOI, gắn CRC
        ──► USB 2 Mbaud hoặc WiFi ──► ESP32 kiểm CRC, giải mã, vẽ lên màn 2.0"
```

Màn 1.54" khi PC bật: 2 trang luân phiên do PC vẽ
- **Hiệu năng PC** (10 giây): CPU, GPU, RAM, VRAM, nhiệt độ, tốc độ mạng. Dùng lại bộ đọc LibreHardwareMonitor và mẫu vẽ của DeskMon (`pc/deskmon.py`, chỉ import).
- **Màn hình phụ** (5 giây): số khung hình mỗi giây, KB/s, đường truyền, độ phân giải màn ảo.

PC không nối thì ESP32 tự hiện trạng thái kết nối, IP. Muốn để ESP32 tự hiện như vậy cả khi PC bật: chạy `man_phu.py --no-side`.

## Giới hạn cần biết

- **Màn không có cảm ứng.** Bạn tương tác bằng chuột của PC, giống mọi màn hình khác.
- **Độ phân giải:** Windows (và driver) không cho màn nhỏ hơn 640×480, nên hình được thu nhỏ một nửa. Chữ cỡ thường rất nhỏ. Hợp với video, ảnh, đồng hồ, biểu đồ, nhạc, hoặc cửa sổ phóng to chữ (Ctrl + lăn chuột).
- **Tốc độ đo được qua cáp USB** (firmware 0.3.0):

  | Nội dung | Bản đầu (0.1) | Bây giờ |
  |---|---|---|
  | Cả màn đổi (video, cuộn trang) | 4,7 khung/giây | 13,3 khung/giây |
  | Thay đổi nhỏ (rê chuột, gõ chữ) | 25 khung/giây | 30 (giới hạn `--fps`) |

  Đã tăng tốc bằng cách:
  - gửi ở luồng riêng: vừa gửi khung này vừa chụp và nén khung sau;
  - gửi trước tối đa 4 khung (không quá 40 KB) trong lúc ESP32 đang vẽ;
  - nén JPEG nhẹ hơn khi cả màn đổi (chất lượng 70, màu 4:2:0), vùng nhỏ vẫn giữ nét;
  - SPI màn 2.0" chạy 80 MHz;
  - ESP32 thôi tự vẽ màn 1.54" khi PC đã vẽ.

  Cáp USB thực tế chở được khoảng 147 KB/s (chip CH340). WiFi chậm hơn (khoảng 90 KB/s) và đôi khi chập chờn.
- **Nếu màn 2.0" bị nhiễu màu hoặc sọc** (dây cắm dài không chịu nổi SPI 80 MHz): trong `esp32/man_phu/man_phu.ino` đổi `b.freq_write = 80000000;` ở màn 2.0" về `40000000`, rồi nạp lại.

## 1. Cài driver màn hình ảo (bạn tự cài, cần quyền admin)

Dùng **Virtual Display Driver** (mã nguồn mở, có chữ ký số của SignPath Foundation): <https://github.com/VirtualDrivers/Virtual-Display-Driver/releases>

1. Tải bản mới nhất ở trang Releases (ứng dụng *Virtual Driver Control*), giải nén, chạy, bấm **Install**. Nếu báo thiếu `vcruntime140.dll` thì cài *Microsoft Visual C++ Redistributable*.
2. Mở `C:\VirtualDisplayDriver\vdd_settings.xml`, đặt:
   - `<monitors><count>1</count></monitors>` (chỉ 1 màn ảo);
   - trong `<resolutions>` có một mục 640×480:
     ```xml
     <resolution><width>640</width><height>480</height><refresh_rate>60</refresh_rate></resolution>
     ```
3. Khởi động lại driver (trong ứng dụng: *Reload Driver*, hoặc tắt/bật nó trong Device Manager).
4. **Settings → System → Display:** chọn màn mới, đặt độ phân giải **640×480**, tỉ lệ **100%**, kéo nó vào đúng vị trí so với 2 màn MSI.

Lưu ý từ chính tác giả driver:
- Bản phát hành đang là bản thử nghiệm (pre-release).
- Trước khi cập nhật driver card AMD, nên gỡ Virtual Display Driver, cập nhật xong mới cài lại; nếu không, có thể bị màn đen lúc khởi động.
- Nếu lỡ bị màn đen: bấm **Win + P** rồi chọn lại chế độ hiển thị, hoặc vào Safe Mode gỡ driver trong Device Manager.
- Gỡ hẳn: Settings → Apps → *Virtual Display Driver* → Uninstall.

## 2. Nạp firmware

Mở thư mục `monitor/` trong VS Code (hoặc dùng terminal ở `monitor/`):

```
pio run -e ota -t upload      # qua WiFi (ESP32 đang chạy DeskMon hay màn phụ đều nhận)
pio run -t upload             # qua cáp USB
```

## 3. Chạy trên PC

```
cd monitor\pc
python man_phu.py --list            # xem các màn, màn ảo phải có tên card "Virtual Display..."
python man_phu.py                   # tự tìm màn ảo; tự chọn USB, không có thì WiFi
python man_phu.py --region 0,0,640,480   # thử khi chưa cài driver: phản chiếu 1 góc màn chính
```

Tuỳ chọn khác: `--monitor N` (chọn màn theo số trong `--list`), `--port COM3`, `--host deskmon.local` (chỉ WiFi), `--fps 30` (trần khung hình), `--no-side` (không vẽ màn 1.54"), `--lhm-url` (web server của LibreHardwareMonitor).

**Tự chạy cùng Windows:** trong `shell:startup` (`Win + R`, gõ `shell:startup`) có shortcut `Man hinh phu.lnk` trỏ tới `monitor\pc\chay_man_phu.bat`. Shortcut DeskMon cũ được đổi tên thành `DeskMon.lnk.tat` để không chạy (hai chương trình cùng dùng một ESP32).

## 4. PC bật: màn phụ. PC tắt: DeskMon

ESP32 có hai vùng firmware: một vùng giữ DeskMon, một vùng giữ màn phụ.

- **PC tắt / ngủ:** 90 giây không có hình, firmware màn phụ tự khởi động sang DeskMon. Màn lại là đồng hồ, thời tiết, tin tức như cũ.
- **PC bật:** `man_phu.py` thấy ESP32 đang chạy DeskMon thì tự nạp lại firmware màn phụ qua WiFi (khoảng 20 giây), rồi hiện hình. Từ lúc Windows mở chương trình tới lúc có hình mất khoảng 1 phút.

Cần: ESP32 vào được WiFi nhà, và `monitor\.pio\build\esp32dev\firmware.bin` có sẵn (build ở thư mục `monitor/` là có).

**Nguồn cho board:** nếu board chỉ cắm vào cổng USB của PC, nhiều máy tắt luôn nguồn USB khi tắt PC, board tắt theo và không hiện DeskMon được. Muốn DeskMon chạy lúc tắt PC thì cấp nguồn riêng (cốc sạc), hoặc bật tuỳ chọn giữ nguồn USB khi tắt máy trong BIOS (thường tên *ErP* phải để Disabled).

## 5. Quay lại hẳn DeskMon

1. Ở thư mục gốc `D:\deskmon` chạy `pio run -e ota -t upload` (firmware màn phụ vẫn nhận nạp qua WiFi như DeskMon).
2. Trong `shell:startup`: xoá `Man hinh phu.lnk`, đổi `DeskMon.lnk.tat` về lại `DeskMon.lnk`.

Cài đặt của DeskMon (tin tức, nhắc việc, WiFi…) vẫn còn nguyên trong ESP32, vì firmware màn phụ không đụng vào.

## Trang web

`http://deskmon.local`: trạng thái (khung/giây, KB/s, `waitPct` = % thời gian chờ dữ liệu, `drawPct` = % thời gian giải mã + vẽ, số lỗi CRC) và nạp firmware.

## Giao thức

Xem `esp32/man_phu/protocol.h`. Mỗi gói: `'M' 'P' | loại | x | y | độ dài | CRC32 | dữ liệu`. Hết mỗi khung, ESP32 trả 1 byte: ACK (đã vẽ xong), NAK (gói hỏng, PC gửi lại toàn màn), NEED_FULL (vừa hiện màn chờ). PC gửi trước 1 khung trong lúc ESP32 đang vẽ khung trước.

## Kiểm thử

```
cd monitor\pc
python -m pytest tests
```
