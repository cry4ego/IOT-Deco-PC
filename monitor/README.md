# Màn hình phụ 320×240: màn 2.0" + ESP32

Biến màn 2.0" của DeskMon thành **màn hình thứ 3 thật của Windows**: kéo cửa sổ sang, rê chuột, xem video, mọi thứ hiện trên màn nhỏ.

Đây là **dự án riêng**. Code DeskMon ở thư mục gốc (`esp32/`, `pc/`) giữ nguyên, không sửa dòng nào. Firmware ở đây chỉ *đọc* lại `secrets.h` (WiFi) và `fonts_vi.h` (phông) của DeskMon.

## Cách hoạt động

```
Windows ──(driver màn hình ảo 640×480)──► man_phu.py chụp màn ảo + con trỏ chuột
        ──► thu nhỏ 320×240, chỉ lấy vùng thay đổi, nén JPEG/QOI, gắn CRC
        ──► USB 2 Mbaud hoặc WiFi ──► ESP32 kiểm CRC, giải mã, vẽ lên màn 2.0"
```

Màn 1.54" khi PC bật: các trang luân phiên do PC vẽ
- **Hiệu năng PC** (10 giây): CPU, GPU, RAM, VRAM, nhiệt độ, tốc độ mạng. Dùng lại bộ đọc LibreHardwareMonitor và mẫu vẽ của DeskMon (`pc/deskmon.py`, chỉ import).
- **Claude** (8 giây): mức dùng Claude, giờ hồi token, giới hạn tuần (`pc/claude_usage.py`). Chi tiết ở mục bên dưới.
- **Màn hình phụ** (5 giây): số khung hình mỗi giây, KB/s, đường truyền, độ phân giải màn ảo.

### Trang Claude

- **Nội dung:**
  - vòng % của phiên 5 giờ: xanh dưới 50%, vàng dưới 80%, đỏ từ 80%;
  - giờ hồi và thời gian còn lại;
  - thanh "Tuần này", dấu ▲ chỉ phần tuần đã trôi qua (thanh màu nằm trái ▲ nghĩa là đang dùng chậm hơn thời gian);
  - giới hạn riêng theo model (ví dụ Fable), nếu tài khoản có.
- **Phiên ≥ 80%:** trang Claude hiện lâu gấp đôi và có nhãn đỏ "sắp hết". Nếu cứ dùng đều như từ đầu phiên mà sẽ hết trước giờ hồi, trang hiện thêm dòng "Theo tốc độ này: hết lúc …" (chỉ hiện khi phiên từ 50% trở lên).
- **Tới giờ hồi:** màn hiện "Đã hồi token" 30 giây, rồi chương trình hỏi lại số liệu mỗi phút trong 10 phút sau đó.
- **Cách lấy số liệu:** giống bản Kindle dashboard trong `D:\Đồ Decô`.
  - Đọc token đăng nhập Claude Code ở `%USERPROFILE%\.claude\.credentials.json`, rồi 5 phút một lần hỏi `api.anthropic.com/api/oauth/usage`.
  - Token chỉ gửi tới Anthropic: không ghi log, không gửi sang ESP32.
  - Đây là địa chỉ nội bộ, chưa được công bố, có thể ngừng hoạt động bất cứ lúc nào. Khi lỗi, trang Claude tự ẩn (log ghi "Không lấy được mức dùng Claude").
  - Token hết hạn thì mở Claude Code là tự làm mới. Số liệu cũ quá 15 phút thì góc trên hiện "lúc HH:MM" màu vàng.
- **Không muốn có trang này:** chạy `man_phu.py --no-claude`.

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

  Cáp USB thực tế chở được khoảng 147 KB/s (chip CH340). WiFi chậm hơn (khoảng 90 KB/s) và đôi khi khựng 1–3 giây. Từ bản 0.4.0, khi đang dùng WiFi, PC chờ trả lời tới 6 giây và ESP32 chờ dữ liệu tới 5 giây, vì qua WiFi dữ liệu chỉ đến chậm chứ không mất. Nhờ vậy những cú khựng đó chỉ làm hình đứng một chút, không cắt kết nối nữa. Trước đây chỉ chờ 3 giây (ESP32 chờ 1 giây), nên cứ khoảng 26 giây lại mất kết nối một lần.
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

**Chạy ẩn mà không chết lặng lẽ** (từ 14/11, sau hai lần chương trình treo cứng lúc 13:22 và 15:19):
- Lỗi treo cũ: WiFi khựng làm luồng gửi gặp lỗi đúng lúc hàng đợi còn khung, luồng chính thì chờ chỗ trống mãi. Hình ngừng, 90 giây sau ESP32 về DeskMon, còn chương trình treo nên không nạp lại màn phụ. Đã sửa: không còn chỗ chờ nào không có giới hạn.
- **Watchdog:** vòng chính đứng quá 45 giây vì bất cứ lý do gì thì chương trình ghi chỗ bị kẹt vào `man_phu.log` (dòng "Vòng chính đứng quá…") rồi tự chạy lại. 45 giây ngắn hơn 90 giây ESP32 chờ, nên màn phụ không kịp về DeskMon.
- Lỗi lạ khác chỉ được ghi vào log ("Lỗi không lường trước") rồi chạy tiếp, không tắt hẳn.
- Chỉ một bản chạy một lúc. Mở thêm bản nữa thì bản mới tự thoát, log ghi "đang chạy ở một bản khác".
- Muốn tắt hẳn: `taskkill /IM pythonw.exe /F` (cũng tắt các chương trình pythonw khác nếu có).

## Bảng điều khiển (`bang_dieu_khien.py`)

Cửa sổ nhỏ mở cùng Windows (shortcut `Bang dieu khien man phu.lnk` trong `shell:startup` và trên Desktop). Nhìn một cái là biết màn phụ đang ổn hay hỏng ở đâu:

- **Dòng kết luận** (xanh / vàng / đỏ) kèm việc nên làm. Ví dụ: "Màn phụ đang chạy tốt", "Đang nạp lại firmware màn phụ (đừng rút điện board)", "Nạp firmware lỗi, tự thử lại sau 30 giây", "Windows chưa nhận màn hình ảo", "Chương trình màn phụ không chạy".
- **Từng phần:** chương trình màn phụ, màn hình ảo của Windows, đường truyền (khung/giây, KB/s), cáp USB, firmware trên board, DeskMon dự phòng, sóng WiFi của board, LibreHardwareMonitor, lỗi gần nhất.
- **Nút bấm:** Chạy màn phụ, Khởi động lại, Dừng, Mở log, Trang board, Cài đặt màn hình.
- **Nhật ký:** những dòng log cuối, lỗi tô đỏ, cảnh báo tô vàng.

Chương trình màn phụ ghi trạng thái của nó vào `pc/man_phu_status.json` vài giây một lần. Bảng điều khiển đọc file đó, nên biết được cả lúc chương trình đang nạp firmware hay đang kẹt. Mở bằng tay: bấm đúp `pc\bang_dieu_khien.bat` hoặc shortcut trên Desktop. Mỗi lúc chỉ mở được một cửa sổ.

## 4. PC bật: màn phụ. PC tắt: DeskMon

ESP32 có hai vùng firmware: một vùng giữ DeskMon, một vùng giữ màn phụ.

- **PC tắt / ngủ:** 90 giây không có hình, firmware màn phụ tự khởi động sang DeskMon. Màn lại là đồng hồ, thời tiết, tin tức như cũ.
- **PC bật:** `man_phu.py` thấy ESP32 đang chạy DeskMon thì tự nạp lại firmware màn phụ qua WiFi (khoảng 20 giây), rồi hiện hình. Từ lúc Windows mở chương trình tới lúc có hình mất khoảng 1 phút. Chưa hỏi được ESP32 (mới bật WiFi, mDNS chưa trả lời) thì 10 giây sau hỏi lại. Nạp lỗi (ví dụ đứt giữa chừng lúc board vừa bật) thì thử lại sau 30 giây, rồi 1 phút, 2 phút, sau đó 5 phút một lần.
- Tên `deskmon.local` (mDNS) trên Windows thỉnh thoảng không trả lời, nên chương trình nối lại bằng IP của lần kết nối trước.
- **Có cắm cáp USB dữ liệu:** không cần WiFi. Bắt tay qua USB lỗi hai lần (board đang chạy DeskMon) thì chương trình dùng esptool (`pc/usb_flash.py`):
  1. đọc otadata và 16 KB đầu hai vùng để biết vùng nào là DeskMon;
  2. ghi màn phụ vào vùng **không** chứa DeskMon, ghi xong hẳn mới đổi vùng khởi động.

  Thời gian đo được ngày 15/11:
  - vùng màn phụ còn tốt thì chỉ cần đổi vùng khởi động, mất 6 giây (có hình 24 giây sau khi chương trình chạy);
  - vùng màn phụ hỏng thì phải ghi lại cả firmware, mất 19 giây.

  Đây là cách sửa lỗi "Không bắt tay được với ESP32": một lần nạp qua WiFi bị ngắt giữa chừng làm vùng màn phụ hỏng, còn web của DeskMon thì cắt mọi kết nối, nên không nạp lại qua WiFi được.
- **Đừng dừng hay khởi động lại chương trình lúc đang nạp.** Bảng điều khiển sẽ hỏi lại trước khi làm vậy, và ghi mỗi lần bấm nút vào `man_phu.log`.

Cần: ESP32 vào được WiFi nhà, và `monitor\.pio\build\esp32dev\firmware.bin` có sẵn (build ở thư mục `monitor/` là có).

**Giữ DeskMon ở vùng còn lại:** nạp qua WiFi luôn ghi vào vùng *không chạy*. Vì vậy nạp màn phụ qua WiFi trong lúc màn phụ đang chạy sẽ ghi đè lên DeskMon. Đây là nguyên nhân lỗi "tắt PC mà không về DeskMon" ở bản 0.3.0. Từ bản 0.4.0:
- Lúc khởi động, firmware màn phụ dò xem vùng còn lại có DeskMon không (`deskmonSlot` trong `/api/status`). Không có thì nó **không** chuyển sang vùng đó, và màn 1.54" ghi "Chưa có DeskMon dự phòng".
- `man_phu.py` thấy `deskmonSlot: false` thì tự nạp DeskMon (`D:\deskmon\.pio\build\ota\firmware.bin`) vào vùng còn lại qua `/api/deskmon`, mất khoảng 20 giây. Màn phụ vẫn chạy, không khởi động lại.
- Muốn nạp cả hai qua cáp USB một lần (DeskMon vào vùng 2, màn phụ vào vùng 1), chạy hai lệnh sau trong thư mục `monitor/`:
  ```
  python %USERPROFILE%\.platformio\packages\tool-esptoolpy\esptool.py --chip esp32 --port COM4 --baud 921600 write_flash 0x1F0000 ..\.pio\build\ota\firmware.bin
  pio run -t upload --upload-port COM4
  ```

**Nguồn cho board:** nếu board chỉ cắm vào cổng USB của PC, nhiều máy tắt luôn nguồn USB khi tắt PC, board tắt theo và không hiện DeskMon được. Muốn DeskMon chạy lúc tắt PC thì cấp nguồn riêng (cốc sạc), hoặc bật tuỳ chọn giữ nguồn USB khi tắt máy trong BIOS (thường tên *ErP* phải để Disabled).

## 5. Quay lại hẳn DeskMon

1. Ở thư mục gốc `D:\deskmon` chạy `pio run -e ota -t upload` (firmware màn phụ vẫn nhận nạp qua WiFi như DeskMon).
2. Trong `shell:startup`: xoá `Man hinh phu.lnk`, đổi `DeskMon.lnk.tat` về lại `DeskMon.lnk`.

Cài đặt của DeskMon (tin tức, nhắc việc, WiFi…) vẫn còn nguyên trong ESP32, vì firmware màn phụ không đụng vào.

## Trang web

`http://deskmon.local`: trạng thái (khung/giây, KB/s, `waitPct` = % thời gian chờ dữ liệu, `drawPct` = % thời gian giải mã + vẽ, số lỗi CRC) và nạp firmware.

## Giao thức

Xem `esp32/man_phu/protocol.h`. Mỗi gói: `'M' 'P' | loại | màn (0 = 2.0", 1 = 1.54") | x | y | độ dài | CRC32 | dữ liệu`. Hết mỗi khung, ESP32 trả 1 byte: ACK (đã vẽ xong), NAK (gói hỏng, PC gửi lại toàn màn), NEED_FULL (vừa hiện màn chờ). PC gửi trước tối đa 4 khung (không quá 40 KB) trong lúc ESP32 đang vẽ.

## Kiểm thử

```
cd monitor\pc
python -m pytest tests
```
