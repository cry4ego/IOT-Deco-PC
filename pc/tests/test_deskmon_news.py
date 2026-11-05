"""Kiểm thử phần tin tức phía PC: đọc RSS, xoay vòng tin, lịch hiện tin, vẽ thẻ tin, cài đặt từ web."""
import datetime as dt
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deskmon  # noqa: E402
from deskmon_news import NewsFeed, NewsItem, NewsRenderer, NewsTurn, parse_rss  # noqa: E402
from deskmon_remote import PcSettings, RemoteSync  # noqa: E402

VNEXPRESS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Thời sự - VnExpress RSS</title>
<image><url>https://s.vnecdn.net/logo.png</url><title>Logo</title></image>
<item>
  <title>Cháy trường học ở Hà Nội, hàng trăm học sinh sơ tán</title>
  <description><![CDATA[<a href="https://vnexpress.net/a.html"><img src="https://i1.vnecdn.net/a.jpg?w=1200&h=0&s=x"></a></br>Đám cháy &amp; khói bốc cao lúc 9h.]]></description>
  <pubDate>Thu, 05 Nov 2026 13:45:46 +0700</pubDate>
  <enclosure type="image/jpeg" length="1200" url="https://i1.vnecdn.net/a.jpg?w=1200&amp;h=0&amp;s=x"/>
</item>
<item>
  <title>Bài không có ảnh</title>
  <description>Chỉ có chữ</description>
</item>
<item>
  <title></title>
  <description>Không có tiêu đề thì bỏ</description>
</item>
</channel></rss>""".encode()

TUOITRE_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title><![CDATA[Tiêu đề trong CDATA]]></title><description><![CDATA[Tóm tắt]]></description>
<enclosure url="https://cdn2.tuoitre.vn/a.jpg" length="0" type="image/jpeg" /></item>
</channel></rss>""".encode()


def item(title="Tin", world=False, image=""):
    return NewsItem(title=title, summary="Tóm tắt", image_url=image, world=world)


def test_parse_rss_reads_title_summary_and_image_url():
    items = parse_rss(VNEXPRESS_RSS, world=False)

    assert items[0].title == "Cháy trường học ở Hà Nội, hàng trăm học sinh sơ tán"
    assert items[0].summary == "Đám cháy & khói bốc cao lúc 9h."
    assert items[0].image_url == "https://i1.vnecdn.net/a.jpg?w=1200&h=0&s=x"
    assert items[0].world is False


def test_parse_rss_skips_items_without_title_and_keeps_items_without_image():
    items = parse_rss(VNEXPRESS_RSS, world=True)

    assert [i.title for i in items] == ["Cháy trường học ở Hà Nội, hàng trăm học sinh sơ tán", "Bài không có ảnh"]
    assert items[1].image_url == ""
    assert all(i.world for i in items)


def test_parse_rss_reads_cdata_titles():
    items = parse_rss(TUOITRE_RSS, world=False)

    assert items[0].title == "Tiêu đề trong CDATA"
    assert items[0].image_url == "https://cdn2.tuoitre.vn/a.jpg"


def test_parse_rss_composes_detached_vietnamese_marks():
    decomposed = unicodedata.normalize("NFD", "Nghị sĩ Cộng hòa")
    xml = f"<rss><channel><item><title>{decomposed}</title><description>{decomposed}</description></item></channel></rss>"

    parsed = parse_rss(xml.encode(), world=True)[0]

    assert parsed.title == "Nghị sĩ Cộng hòa"
    assert parsed.summary == "Nghị sĩ Cộng hòa"


def test_parse_rss_limits_items():
    assert len(parse_rss(VNEXPRESS_RSS, world=False, limit=1)) == 1


def test_feed_rotation_alternates_domestic_and_world_then_wraps():
    feed = NewsFeed()
    feed.set_items([item("D1"), item("D2")], [item("W1", world=True)])

    assert [feed.next_item().title for _ in range(4)] == ["D1", "W1", "D2", "D1"]


def test_feed_rotation_returns_none_without_items():
    assert NewsFeed().next_item() is None


def test_feed_restarts_rotation_when_newest_item_changes():
    feed = NewsFeed()
    feed.set_items([item("D1"), item("D2")], [])
    feed.next_item()

    feed.set_items([item("D0"), item("D1")], [])

    assert feed.next_item().title == "D0"


def test_feed_keeps_rotation_when_refresh_brings_nothing_new():
    feed = NewsFeed()
    feed.set_items([item("D1"), item("D2")], [])
    feed.next_item()

    feed.set_items([item("D1"), item("D2")], [])

    assert feed.next_item().title == "D2"


def turn_tick(turn, now, want=True, item_sec=10, gap_sec=60, source=None):
    source = source or iter(f"T{i}" for i in range(100))
    return turn.tick(now, want, item_sec, gap_sec, lambda: item(next(source)))


def test_turn_waits_for_gap_before_first_item():
    turn = NewsTurn()
    source = iter(["A", "B"])

    assert turn_tick(turn, 100.0, want=False) is None        # chưa được phép: đặt mốc chờ
    assert turn_tick(turn, 159.0, source=source) is None
    assert turn_tick(turn, 160.0, source=source).title == "A"


def test_turn_shows_three_items_then_returns_screen_for_gap():
    turn = NewsTurn()
    source = iter(["A", "B", "C", "D"])

    shown = [turn_tick(turn, t, source=source) for t in (0.0, 5.0, 10.0, 20.0)]
    assert [i.title for i in shown] == ["A", "A", "B", "C"]

    assert turn_tick(turn, 30.0, source=source) is None      # hết lượt
    assert turn_tick(turn, 89.0, source=source) is None      # đang nghỉ
    assert turn_tick(turn, 90.0, source=source).title == "D"


def test_turn_with_zero_gap_never_returns_screen():
    turn = NewsTurn()
    source = iter(f"T{i}" for i in range(10))

    titles = [turn_tick(turn, t * 10.0, gap_sec=0, source=source).title for t in range(5)]

    assert titles == ["T0", "T1", "T2", "T3", "T4"]


def test_turn_stops_immediately_when_not_wanted():
    turn = NewsTurn()
    source = iter(["A", "B"])
    assert turn_tick(turn, 0.0, source=source).title == "A"

    assert turn_tick(turn, 1.0, want=False, source=source) is None


def test_turn_retries_when_no_news_available():
    turn = NewsTurn()

    assert turn.tick(0.0, True, 10, 60, lambda: None) is None
    assert turn.tick(1.0, True, 10, 60, lambda: item("A")).title == "A"


class FakeFonts:
    def get(self, size, bold=False, text=""):
        return ImageFont.load_default(size)


def test_renderer_draws_card_at_requested_size_with_and_without_image():
    renderer = NewsRenderer(FakeFonts())
    now = dt.datetime(2026, 11, 5, 14, 30)
    long_title = "Tiêu đề rất dài " * 12

    with_image = renderer.render((320, 240), item(long_title), Image.new("RGB", (480, 300), (200, 0, 0)), now)
    text_only = renderer.render((240, 240), item(long_title, world=True), None, now)

    assert with_image.size == (320, 240)
    assert text_only.size == (240, 240)
    assert with_image.getpixel((160, 80)) == (200, 0, 0)     # ảnh nằm dưới đầu thẻ


class FakeFeed:
    def next_item(self):
        return item("Tin mới")

    def image(self, url):
        return None


def make_app():
    app = deskmon.App.__new__(deskmon.App)
    app.news = FakeFeed()
    app.news_renderer = NewsRenderer(FakeFonts())
    app.news_turns = {deskmon.SCREEN_LYRICS: NewsTurn(), deskmon.SCREEN_STATS: NewsTurn()}
    return app


def news_settings(online):
    return PcSettings(news_on=True, news_online=online, news_gap_sec=0)


def test_news_only_shows_on_screens_chosen_on_web():
    app = make_app()
    now = dt.datetime(2026, 11, 5, 14, 30)

    only_small = news_settings(2)
    assert app.news_frame(deskmon.SCREEN_LYRICS, only_small, now, allowed=True) is None
    assert app.news_frame(deskmon.SCREEN_STATS, only_small, now, allowed=True).size == (240, 240)

    both = news_settings(3)
    assert make_app().news_frame(deskmon.SCREEN_LYRICS, both, now, allowed=True).size == (320, 240)


def test_news_gives_way_when_screen_has_priority_content():
    app = make_app()
    now = dt.datetime(2026, 11, 5, 14, 30)

    assert app.news_frame(deskmon.SCREEN_LYRICS, news_settings(3), now, allowed=False) is None


def test_news_stays_hidden_when_turned_off_on_web():
    app = make_app()
    off = PcSettings(news_on=False, news_online=3, news_gap_sec=0)

    assert app.news_frame(deskmon.SCREEN_STATS, off, dt.datetime(2026, 11, 5), allowed=True) is None


def test_remote_sync_keeps_known_ip_through_brief_timeouts():
    sync = RemoteSync("deskmon.local", lambda: None, lambda name: None)
    sync._ip = "192.168.1.5"

    def timed_out():
        raise TimeoutError("timed out")

    sync._poll = timed_out
    sync._step()
    sync._step()
    assert sync._ip == "192.168.1.5"

    sync._step()                                             # lỗi lặp lại: có thể ESP32 đã đổi IP
    assert sync._ip is None


def test_remote_sync_failure_count_resets_after_success():
    sync = RemoteSync("deskmon.local", lambda: None, lambda name: None)
    sync._ip = "192.168.1.5"

    def timed_out():
        raise TimeoutError("timed out")

    sync._poll = timed_out
    sync._step()
    sync._step()
    sync._poll = lambda: None
    sync._step()
    sync._poll = timed_out
    sync._step()

    assert sync._ip == "192.168.1.5"


def test_pc_settings_reads_news_fields_from_web():
    s = PcSettings.from_json({"newsOn": True, "newsOnline": 2, "newsDomestic": False, "newsWorld": True,
                              "newsItemSec": 7, "newsGapSec": 0, "newsRefreshMin": 3})

    assert s.news_on is True
    assert s.news_online == 2
    assert s.news_domestic is False
    assert s.news_world is True
    assert s.news_item_sec == 7
    assert s.news_gap_sec == 0
    assert s.news_refresh_min == 3


def test_pc_settings_news_is_off_until_web_says_otherwise():
    assert PcSettings().news_on is False
