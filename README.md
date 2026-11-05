# DeskMon: màn hình lời nhạc + theo dõi PC cho ESP32

- **Màn 2.0" (GMT020-02, nằm ngang 320×240):** hiện lời bài hát đang phát trên **Spotify** hoặc **YouTube** (Chrome/Edge/Firefox), chạy theo thời gian thực. Có ảnh bìa, tên bài, ca sĩ và thanh tiến trình. Khi không phát nhạc, màn này hiện đồng hồ.
- **Màn 1.54" (ST7789, 240×240):** CPU %, GPU %, RAM, VRAM, nhiệt độ CPU/GPU và tốc độ mạng. Ban đêm (23:00–07:00) màn tự giảm độ sáng.

Mọi việc nặng do PC làm: lấy dữ liệu, tìm lời, vẽ giao diện có dấu tiếng Việt cùng chữ Hàn, Nhật, Trung. PC chỉ gửi **phần hình thay đổi** sang ESP32 qua cáp USB (hoặc WiFi). Băng thông khoảng 4–30 KB/s.

**Khi PC tắt** (cần WiFi): ESP32 tự làm đồng hồ để bàn.
- **Màn 2.0":** thứ/ngày, giờ lớn (đồng bộ NTP, chọn được 3 cỡ chữ), âm lịch và năm can chi (hoặc tên ngày lễ nếu hôm nay là ngày lễ), lịch cả tuần kèm ngày âm, nhiệt độ hiện tại.
- **Màn 1.54":** luân phiên 3 trang:
  - **thời tiết** (Open-Meteo: hiện tại, 3 ngày tới, khả năng mưa) kèm **chất lượng không khí AQI**;
  - **lịch tháng** có ngày âm;
  - **sắp tới**: đếm ngược tới các ngày lễ Việt Nam và sự kiện riêng.

  Ban đêm màn tự giảm sáng.

**Khi PC bật:** nếu bài hát không có lời, màn 2.0" hiện ảnh bìa lớn cùng tên bài và ca sĩ. Màn 1.54" có thêm trang **ổ đĩa & mạng** và **nhấp nháy đỏ** khi CPU/GPU vượt ngưỡng nhiệt.

**Tin tức** (cả khi PC bật lẫn PC tắt): tin trong nước và thế giới từ VnExpress, kèm ảnh bài báo, tự tải tin mới. Chọn trên web hiện ở màn nào, riêng cho lúc PC bật và lúc PC tắt, hoặc tắt hẳn.

**Web cấu hình `http://deskmon.local`** (điện thoại hoặc máy tính cùng WiFi, chạy cả khi PC tắt, không cần mật khẩu): chỉnh mọi cài đặt, thêm sự kiện và **nhắc việc**, **điều khiển nhạc** (phát/dừng, bài trước/sau), đổi WiFi, **nạp firmware qua WiFi**. Xem [mục 5](#5-web-cấu-hình-httpdeskmonlocal).

**Không vào được WiFi:** sau 20 giây ESP32 tự phát WiFi `DeskMon-Setup`. Quét mã QR trên màn 1.54" bằng điện thoại, trang chọn WiFi sẽ tự mở.

```
deskmon/
├── platformio.ini                 ← build/nạp bằng PlatformIO (VS Code), env "ota" = nạp qua WiFi
├── esp32/
│   ├── deskmon_esp32/
│   │   ├── deskmon_esp32.ino      ← firmware chính (Arduino IDE mở file này)
│   │   ├── secrets.example.h      ← mẫu WiFi → chép thành secrets.h
│   │   ├── settings.*             ← cài đặt người dùng (lưu NVS)
│   │   ├── webui.*  web_page.h    ← web cấu hình + API
│   │   ├── wifi_ap.*              ← WiFi cài đặt khi không vào được mạng
│   │   ├── offline_ui.*           ← giao diện khi PC tắt, nhắc việc, thẻ tin
│   │   ├── news.*  news_view.*    ← tải tin + ảnh ở task nền, lịch hiện tin khi PC tắt
│   │   ├── weather.*              ← thời tiết + AQI Open-Meteo
│   │   ├── lunar.*  events.*      ← âm lịch, ngày lễ, đếm ngược
│   │   └── fonts_vi.h             ← phông tiếng Việt (sinh tự động)
│   └── tools/make_fonts.py        ← sinh lại fonts_vi.h từ Segoe UI
└── pc/
    ├── deskmon.py          ← chạy trên PC
    ├── deskmon_remote.py   ← đồng bộ với web cấu hình (cài đặt + lệnh nhạc)
    ├── deskmon_news.py     ← tải tin + ảnh, vẽ thẻ tin khi PC bật
    ├── deskmon_text.py     ← đo và ngắt chữ dùng chung
    ├── tests/              ← kiểm thử: cd pc, rồi python -m pytest tests
    ├── requirements.txt
    ├── chay_deskmon.bat    ← chạy có cửa sổ log
    └── chay_an.bat         ← chạy ẩn / tự khởi động
```

---

## 1. Nối dây

Mỗi màn dùng **một bus SPI riêng**, nên không phải chia dây. Cả hai màn cấp nguồn **3.3V** từ chân 3V3 của ESP32.

**Màn 2.0" (7 chân: GND VCC SCL SDA RST DC CS)**

| Màn | ESP32 |
|---|---|
| GND | GND |
| VCC | 3V3 |
| SCL | GPIO 18 |
| SDA | GPIO 23 |
| RST | GPIO 17 |
| DC  | GPIO 16 |
| CS  | GPIO 5 |

**Màn 1.54" (8 chân: GND VCC SCL SDA RST DC CS BL)**

| Màn | ESP32 |
|---|---|
| GND | GND |
| VCC | 3V3 |
| SCL | GPIO 14 |
| SDA | GPIO 13 |
| RST | GPIO 33 |
| DC  | GPIO 27 |
| CS  | GPIO 26 |
| BL  | GPIO 32 |

> Chữ "SCL/SDA" trên hai màn này là chân **SPI** (SCK/MOSI), không phải I2C.




 > GPIO 25 và 34 được để trống cho dự án quạt (MOSFET + biến trở) sau này.
>
> **Nguồn:** 3V3 của board ESP32 thừa sức nuôi cả hai màn. Chỉ nối VCC vào 5V nếu mặt sau màn có IC ổn áp (con 3 chân, thường ghi `662K`). Màn không có ổn áp sẽ hỏng khi nhận 5V.

---

## 2. Nạp firmware cho ESP32

1. Cài **Arduino IDE 2.x**.
2. Vào *Boards Manager*, cài **esp32 by Espressif Systems** (đã thử biên dịch với bản 3.3.9).
3. Vào *Library Manager*, cài **LovyanGFX** (by lovyan03) và **ArduinoJson** (by Benoit Blanchon, bản 7.x).
4. Mở `esp32/deskmon_esp32/deskmon_esp32.ino` và chọn board **ESP32 Dev Module**.
5. *(Không bắt buộc)* chép `secrets.example.h` thành `secrets.h` (cùng thư mục), rồi điền tên và mật khẩu WiFi. ESP32 chỉ bắt được WiFi **2.4 GHz**. Đừng chia sẻ file `secrets.h`. Bỏ qua bước này thì chọn WiFi từ điện thoại sau khi nạp xong, xem [WiFi cài đặt](#wifi-cài-đặt).
6. Bấm Upload. Nếu không nạp được, giữ nút **BOOT** trên ESP32 lúc IDE hiện "Connecting...".

**Hoặc dùng PlatformIO trong VS Code:** mở thư mục `deskmon/`, bấm ✓ (Build) rồi → (Upload) ở thanh trạng thái. Thư viện được cài tự động. Dự án đang dùng `espressif32@6.13.0` (Arduino core 2.0.x). Muốn dùng core 3.x thì xem ghi chú ở đầu `platformio.ini`.

**Nạp qua WiFi (không cần cắm cáp):** lần đầu phải nạp qua USB, để ESP32 nhận bảng phân vùng có 2 vùng app. Từ lần sau, chọn một trong hai cách:
- Trong VS Code, chạy:
  ```
  pio run -e ota -t upload
  ```
  Hoặc chọn môi trường `ota` ở thanh trạng thái rồi bấm Upload.
- Trên web cấu hình: chọn file `.pio/build/esp32dev/firmware.bin` ở mục **Cập nhật firmware**.

Sau khi nạp xong:
- Nếu có WiFi, màn 2.0" hiện "Đang kết nối Wi-Fi..." rồi chuyển sang đồng hồ, màn 1.54" hiện thời tiết và lịch.
- Nếu chưa cài hoặc không vào được WiFi, sau 20 giây màn 2.0" hiện các bước cài đặt, màn 1.54" hiện mã QR.

Nơi lấy thời tiết, cỡ đồng hồ, độ sáng và các tuỳ chọn khác chỉnh trên **web cấu hình**, không cần sửa code.

**Chỉnh nếu hình hiển thị sai** (các `#define` ở đầu file):

| Hiện tượng | Sửa |
|---|---|
| Hình lộn ngược trên màn 2.0" | `LYR_ROTATION` 1 → 3 |
| Hình xoay sai trên màn 1.54" | `STA_ROTATION` thử 0/1/2/3 |
| Nền trắng, màu bị âm bản | `LYR_INVERT` / `STA_INVERT` true → false |
| 3 chấm R G B hiện sai màu (đỏ ↔ xanh dương) | `..._RGB_ORDER` false → true |
| Màn 1.54" bị lệch / có sọc rác một cạnh | đổi `STA_ROTATION` (thư viện tự bù offset) |
| Màn 1.54" chỉ sáng đèn nền, không hiện hình | kiểm tra lại 5 dây SCL/SDA/CS/DC/RST, sau đó thử `STA_SPI_MODE` 0 → 3 |
| Hình bị nhiễu, sọc | giảm `SPI_FREQ` xuống `27000000` hoặc `20000000`, dùng dây ngắn |

---

## 3. Cài phần chạy trên PC (Windows 10/11)

1. Cài **Python 3.10+** từ python.org, nhớ tick *Add python.exe to PATH*.
2. Mở terminal trong thư mục `pc/` và chạy:
   ```
   pip install -r requirements.txt
   ```
3. **Thử trước bằng dữ liệu mẫu** (cắm ESP32 vào USB):
   ```
   python deskmon.py --demo
   ```
   Màn 2.0" sẽ chạy lời mẫu, màn 1.54" có số liệu giả chạy lên xuống. Script tự tìm cổng COM. Nếu không tìm thấy, thêm `--port COM5` (xem số COM trong *Device Manager*).
4. Chạy thật:
   ```
   python deskmon.py
   ```
   Bật một bài trên Spotify hoặc YouTube là lời sẽ hiện sau 1–2 giây.

### LibreHardwareMonitor (để có nhiệt độ CPU/GPU, tải GPU, VRAM)

Windows không cho Python đọc nhiệt độ trực tiếp, nên cần thêm **LibreHardwareMonitor** (miễn phí):

1. Tải bản mới nhất ở github.com/LibreHardwareMonitor/LibreHardwareMonitor (mục Releases), giải nén.
2. Chạy `LibreHardwareMonitor.exe` bằng **Run as administrator**.
3. Vào menu **Options → Remote Web Server → Run** (cổng mặc định 8085).
4. Bật thêm **Options → Run On Windows Startup**, **Start Minimized** và **Minimize To Tray**. Hoặc tạo một tác vụ trong Task Scheduler chạy LHM với quyền cao nhất khi đăng nhập. Máy này đang dùng tác vụ tên `LibreHardwareMonitor`.

Nếu chưa có LHM, màn 1.54" vẫn hiện CPU % và RAM. Các ô GPU/VRAM sẽ ghi "cần LHM".

### Tự chạy cùng Windows

Nhấn `Win + R`, gõ `shell:startup`, rồi tạo **shortcut** của file `chay_an.bat` vào thư mục vừa mở. Log được ghi ra `pc/deskmon.log`.

---

## 4. Tuỳ chọn dòng lệnh

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--port COM5` | Chỉ dùng cổng COM này. Mặc định: tự tìm cổng COM, không thấy thì gửi qua WiFi tới `deskmon.local` |
| `--baud 921600` | Phải khớp `SERIAL_BAUD` trong firmware. Truyền lỗi thì giảm cả hai xuống 460800 |
| `--host deskmon.local` | Chỉ dùng WiFi, kể cả khi có cắm USB (hoặc dùng IP của ESP32 trong trang quản lý router) |
| `--lyrics-lead 0.35` | Lời hiện sớm hơn bao nhiêu giây. Tăng nếu lời bị trễ, giảm nếu lời chạy trước |
| `--brightness 220` | Độ sáng màn 1.54" (0–255) |
| `--night "23:00-07:00"` / `--night-brightness 40` | Giảm sáng ban đêm (`--night ""` để tắt) |
| `--remote-host deskmon.local` | Địa chỉ web cấu hình trên ESP32 (mặc định: `--host` hoặc `deskmon.local`) |
| `--no-remote` | Không đồng bộ với web cấu hình |
| `--preview out` | Không cần ESP32, chỉ lưu `out/lyrics.png` và `out/stats.png` để xem trước |
| `-v` | Ghi log chi tiết |

Khi đọc được web cấu hình, các giá trị **lời hiện sớm**, **độ sáng** và **khung giờ đêm** trên web sẽ ghi đè các tuỳ chọn dòng lệnh tương ứng.

---

## 5. Web cấu hình (`http://deskmon.local`)

Mở bằng điện thoại hoặc máy tính cùng mạng WiFi. Web chạy ngay trên ESP32 nên **vẫn dùng được khi PC tắt**. Mọi cài đặt lưu trong bộ nhớ flash của ESP32, khởi động lại không mất.

Web **không có mật khẩu**: ai ở cùng mạng WiFi cũng đổi được cài đặt, đổi WiFi và nạp được firmware. Chỉ yêu cầu gửi từ một trang web khác đang mở trong trình duyệt là bị chặn. Đừng đưa thiết bị ra mạng công cộng.

| Mục | Chỉnh được |
|---|---|
| Điều khiển nhạc | Phát/Dừng, Bài trước/Bài sau cho ứng dụng đang phát trên PC (cần `deskmon.py` đang chạy) |
| Đồng hồ & màn hình | Cỡ đồng hồ Nhỏ/Vừa/Lớn, độ sáng ngày/đêm, khung giờ đêm, thời gian đổi trang màn 1.54" |
| Thời tiết & không khí | Chọn tỉnh/thành hoặc toạ độ tuỳ chỉnh, tần suất cập nhật, bật/tắt AQI |
| Tin tức | Bật/tắt, chọn màn hiển thị riêng cho lúc PC tắt và PC bật (2.0", 1.54", cả hai hoặc không hiện), tin trong nước/thế giới, thời gian mỗi tin, thời gian nghỉ giữa các lượt, tần suất tải tin mới |
| Sự kiện đếm ngược | Tối đa 8 sự kiện riêng, dương lịch hoặc âm lịch. Ngày lễ Việt Nam có sẵn |
| Nhắc việc | Tối đa 8 lời nhắc (giờ, các ngày trong tuần, nội dung). Đến giờ, màn 2.0" hiện lời nhắc, màn 1.54" nhấp nháy trong 5 phút. Tắt bằng nút **BOOT** trên ESP32 hoặc trên web. Nút "Thử" để xem trước |
| Máy tính | Lời hiện sớm, ngưỡng cảnh báo nóng CPU/GPU, trang ổ đĩa & mạng, thời gian đổi trang |
| WiFi | Quét và đổi mạng mà không cần nạp lại code, phát thử WiFi cài đặt |
| Cập nhật firmware | Nạp `firmware.bin` từ trình duyệt |

### WiFi cài đặt

Khi chưa cài WiFi, hoặc 20 giây liền không vào được mạng nhà, ESP32 tự phát WiFi **`DeskMon-Setup`**. Mật khẩu của WiFi này được tạo ngẫu nhiên một lần, chỉ hiện trên màn hình.

1. Dùng điện thoại quét mã QR trên màn 1.54", hoặc nối vào `DeskMon-Setup` và nhập mật khẩu trên màn.
2. Trang cài đặt tự mở (không thì vào `http://192.168.4.1`).
3. Chọn WiFi nhà, nhập mật khẩu rồi bấm **Lưu**. ESP32 tự khởi động lại.

Trong lúc phát, nếu không có ai đang nối vào, ESP32 cứ 30 giây thử lại WiFi nhà một lần. Nhờ vậy mất điện xong, router bật chậm hơn ESP32 thì nó vẫn tự vào lại mạng và tắt WiFi cài đặt. Nút **Phát thử** trên web giúp xem trước các bước này trong 3 phút mà không mất mạng.

**Thêm tiện ích mới:**
1. Thêm trường vào `struct Settings` (`settings.h`).
2. Đọc/ghi trường đó trong `settings.cpp`.
3. Thêm ô nhập trong `web_page.h`, trong hai hàm `render()` và `collect()`.

Cài đặt mà PC cần thì thêm vào `pcSettingsToJson()` và `PcSettings` (`pc/deskmon_remote.py`).

API chính (JSON):

| API | Dùng để |
|---|---|
| `GET/POST /api/config` | Đọc / lưu cài đặt |
| `GET /api/status` | Trạng thái thiết bị |
| `GET /api/news` | Tin ESP32 đã tải (khi PC tắt), lỗi tải gần nhất |
| `POST /api/media` | Lệnh nhạc |
| `POST /api/pc` | `deskmon.py` đồng bộ |
| `POST /api/reminder/test`, `POST /api/reminder/dismiss` | Thử / tắt nhắc việc |
| `POST /api/wifi/scan`, `POST /api/wifi`, `POST /api/ap/test` | Quét WiFi, đổi WiFi, phát thử WiFi cài đặt |
| `POST /api/reboot`, `POST /update` | Khởi động lại, nạp firmware |

---

## 6. Cách hoạt động

- **USB hay WiFi:** board cắm USB vào PC thì `deskmon.py` gửi hình qua USB. Board cắm cốc sạc thì nó tự gửi qua WiFi, vẫn đủ lời nhạc, thông số PC, cảnh báo nóng. Trên web, ô "Chế độ" ghi rõ đang nối qua USB hay WiFi.

  | | PC bật | PC tắt |
  |---|---|---|
  | Lời nhạc, ảnh bìa, điều khiển nhạc | ✔ (USB hoặc WiFi) | — |
  | CPU, GPU, RAM, ổ đĩa, mạng, cảnh báo nóng | ✔ (USB hoặc WiFi) | — |
  | Đồng hồ, thời tiết, AQI, lịch âm, đếm ngược, nhắc việc, web cấu hình | ✔ (web, nhắc việc) | ✔ (cần WiFi) |
- **Tin tức:** lấy từ RSS của VnExpress (Thời sự, Thế giới), mỗi mục 10 bài mới nhất. Mỗi lượt hiện 3 tin, rồi màn trở về nội dung thường trong thời gian nghỉ đặt trên web (đặt 0 để chỉ hiện tin).
  - **PC tắt:** ESP32 tự tải ở task nền. Ảnh gốc quá lớn nên phải qua dịch vụ miễn phí `wsrv.nl` để thu nhỏ và đổi sang JPEG. Dịch vụ đó lỗi thì thẻ tin chỉ có chữ.
  - **PC bật:** `deskmon.py` tải trực tiếp từ VnExpress và vẽ thẻ tin. Đang phát nhạc thì màn 2.0" ưu tiên lời nhạc, tin hiện sau khi dừng nhạc. PC quá nóng thì màn 1.54" ưu tiên thông số.
- **Nhạc đang phát:** đọc từ *Windows Media Session*, tức chính cái bảng nhạc hiện ra khi bấm phím âm lượng. Nhờ vậy dùng được Spotify (app desktop) và YouTube trên trình duyệt mà **không cần đăng nhập hay API key**. Nếu cả hai cùng phát, Spotify được ưu tiên.
- **Lời bài hát:** lấy từ **LRCLIB** (lrclib.net), một kho lời đồng bộ miễn phí. Với video YouTube, script tự làm gọn tiêu đề, bỏ các chữ như "Official MV", "Lyrics" hay "| …" để đoán tên bài và ca sĩ. Lời đã tìm được lưu cache ở `%LOCALAPPDATA%\DeskMon\lyrics_cache.json`.
- **Không có lời đồng bộ:** nếu bài chỉ có lời thường, màn hình ước lượng dòng theo thời gian và ghi "lời chưa đồng bộ". Nếu không có lời nào (hoặc đang tìm lời), màn 2.0" chuyển sang bố cục **ảnh bìa lớn + tên bài + ca sĩ**.
- **Cảnh báo nóng:** khi CPU hoặc GPU vượt ngưỡng đặt trên web (mặc định 85 °C), màn 1.54" ghim trang chính, viền đỏ nhấp nháy và dòng đầu ghi nhiệt độ.
- **Trang ổ đĩa & mạng:** màn 1.54" luân phiên với trang chính, hiện dung lượng tối đa 3 ổ cố định và biểu đồ tốc độ mạng 60 giây gần nhất.
- **PC tắt hoặc ngủ:** sau 8 giây ESP32 tự chuyển sang chế độ đồng hồ, thời tiết và lịch, rồi tự vẽ mọi thứ.
  - Giờ lấy qua NTP. Nếu không có WiFi thì dùng giờ PC đã gửi trước đó.
  - Thời tiết và AQI cập nhật theo tần suất đặt trên web. Nếu lỗi thì thử lại sau 1 phút.
  - Âm lịch và ngày lễ được tính ngay trên ESP32 theo thuật toán Hồ Ngọc Đức, múi giờ +7.

  Khi PC chạy lại, màn tự vẽ lại giao diện của PC.

## 7. Xử lý sự cố

- **Không mở được `http://deskmon.local`:** điện thoại/PC phải cùng mạng WiFi với ESP32. Một số điện thoại Android không phân giải được `.local`. Khi đó dùng IP của ESP32 (xem trong trang quản lý router, hoặc trong log Serial lúc khởi động: `[wifi] connected 192.168.x.x`).
- **Không thấy tin tức:** kiểm tra mục Tin tức trên web đã bật và chọn đúng màn. Khi PC tắt, mở `http://deskmon.local/api/news` để xem số bài đã tải và lỗi gần nhất (`lastError`). Khi PC bật mà đang phát nhạc, màn 2.0" hiện lời nhạc chứ không hiện tin.
- **Màn tự tắt rồi hiện lại liên tục:** xem ô "Khởi động" / "Lỗi" / "Sụt nguồn" trên web (đếm từ lần nạp firmware gần nhất). "Sụt nguồn" → đổi cốc sạc ≥ 1 A hoặc dây USB ngắn, dày hơn. "Lỗi phần mềm" → cắm USB, tắt `deskmon.py`, mở Serial Monitor (`pio device monitor`) để xem dòng `Guru Meditation` / `Backtrace`.
- **Cắm USB mà không thấy cổng COM, dù board vẫn sáng:** dây chỉ có lõi sạc, không có đường dữ liệu. Dây đó vẫn dùng được với cốc sạc, nhưng muốn nạp code hay chạy qua USB thì phải dùng dây dữ liệu.

- **"Không thấy ESP32 qua USB, cũng không vào được deskmon.local:7777":** với USB, cài driver **CP210x** hoặc **CH340** (tuỳ chip USB trên board ESP32) hoặc dùng `--port COMx`. Với WiFi, kiểm tra `http://deskmon.local` có mở được không.
- **"Không bắt tay được với ESP32":** đóng Serial Monitor của Arduino IDE (nó chiếm cổng COM), kiểm tra `--baud` có khớp firmware không, rồi rút cắm lại USB.
- **Không hiện bài đang phát:** kiểm tra bảng nhạc của Windows (bấm phím âm lượng) có hiện bài không. Với Chrome, YouTube phải đang phát trong tab.
- **Lời lệch vài trăm ms:** chỉnh "Lời hiện sớm hơn" trên web (hoặc `--lyrics-lead` nếu không dùng web).
- **Lời sai bài (hay gặp với YouTube):** do tiêu đề video lạ. Chạy `-v` để xem script đã tìm với tên gì.
- **Nhãn "YouTube":** mọi nguồn phát từ trình duyệt đều hiện nhãn này, kể cả SoundCloud.
