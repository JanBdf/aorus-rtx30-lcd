"""Frame layouts, pinned to what Panel.exe / MonitorService.exe send (docs/PROTOCOL.md).
Nothing here touches a GPU."""
import pytest
from PIL import Image

from aorus30lcd import cli
from aorus30lcd import protocol as P


def test_frame_is_256_bytes_with_magic():
    f = P.frame(0xD6)
    assert len(f) == 256
    assert f[:5] == bytes([0xD6, 0xCB, 0x55, 0xAC, 0x38])
    assert f[5:] == bytes(251)


def test_set_mode_sends_mode_plus_one():
    assert P.set_mode(3)[:6] == bytes([0xE5, 0xCB, 0x55, 0xAC, 0x38, 4])


def test_set_display():
    f = P.set_display(["temp", "power"], 4)
    assert f[0] == 0xE1
    assert f[5:14] == bytes([1, 0, 0, 0, 0, 0, 0, 1, 4])
    assert P.set_display([], 4)[5:14] == bytes(9)          # off: no flags, no interval


def test_sensor_feed_layout_and_clamping():
    f = P.sensor_feed({"temp": 42, "clock": 1890, "usage": 300, "fan": 1500, "vram_clock": 9501,
                       "vram": 37, "fps": 0, "power": 70000})
    assert f[0] == 0xE3
    tail = f[5:5 + 15]
    assert tail[0] == 42
    assert int.from_bytes(tail[1:3], "big") == 1890
    assert tail[3] == 255                                     # usage clamped to u8
    assert int.from_bytes(tail[4:6], "big") == 1500
    assert int.from_bytes(tail[6:8], "big") == 9501
    assert tail[8] == 37
    assert int.from_bytes(tail[9:11], "big") == 0
    assert int.from_bytes(tail[11:13], "big") == 0xFFFF       # power clamped to u16
    assert f[18:] == bytes(256 - 18)


def test_set_template():
    f = P.set_template(2, (255, 0, 16), (10, 20), enabled=True)
    assert f[:5] == bytes([0xEA]) + P.MAGIC
    assert f[5:20] == bytes([2, 255, 0, 16, 0, 0, 0x01, 0x40, 0, 10, 0, 20, 1, 0, 0])


def test_parse_template():
    t = P.parse_template(bytes([1, 2, 3, 1, 0, 0, 0, 0]), bytes([0, 10, 1, 0, 0, 0, 0, 0]))
    assert t == {"color": (1, 2, 3), "enabled": True, "pos": (10, 256)}


def test_rgb565_little_endian():
    img = Image.new("RGB", (P.W, P.H), (255, 0, 0))
    img.putpixel((1, 0), (0, 255, 0))
    img.putpixel((0, 1), (0, 0, 255))
    px = P.rgb565(img)
    assert len(px) == P.W * P.H * 2
    assert px[0:2] == bytes([0x00, 0xF8])                    # red, top-left first
    assert px[2:4] == bytes([0xE0, 0x07])                    # green
    assert px[2 * P.W:2 * P.W + 2] == bytes([0x1F, 0x00])    # blue, start of row 2 (top-down)


def test_still_image_payload_and_upload():
    payload = P.bin_payload([P.rgb565(Image.new("RGB", (P.W, P.H)))])
    assert len(payload) == 108_812
    assert payload[:12] == bytes([1, 0]) + (108_811).to_bytes(4, "little") + bytes([0x40, 1, 0xAA, 0, 1, 0])
    frames = P.upload_frames(payload, P.FB_IMAGE, 1)
    assert len(frames) == 426 + 3                            # BEGIN, header, 426 chunks, END
    begin, header, end = frames[0][0], frames[1][0], frames[-1][0]
    assert begin[:6] == bytes([0xF2]) + P.MAGIC + b"\x01"
    assert end[:6] == bytes([0xF2]) + P.MAGIC + b"\x02"
    assert header[:5] == bytes([0xF1]) + P.MAGIC
    assert header[5:9] == bytes([0x01, 0xF2, 0x58, 0x00])    # fb 0x01F25800, big-endian
    assert header[9] == 1                                    # flag
    assert int.from_bytes(header[10:14], "big") == 426
    assert header[14:19] == bytes([0, 0, 0, 2, 0])           # nframes, delay, 64 KB erase, bool
    assert all(len(f) == 256 for f, _ in frames)
    assert b"".join(f for f, _ in frames[2:-1])[:len(payload)] == payload


@pytest.mark.parametrize("size,erase", [(0x4FFF, 1), (0x5000, 2)])
def test_erase_mode_threshold(size, erase):
    assert P.upload_frames(bytes(size), P.FB_IMAGE, 1)[1][0][17] == erase


def test_dry_run_image(tmp_path, capsys):
    src = tmp_path / "card.png"
    cli.testcard().save(src)
    cli.main(["--dry-run", "image", str(src), "--save-bin", str(tmp_path / "out.bin")])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("W 0x61 [256] d6 cb 55 ac 38")  # firmware check first
    assert "426 chunks to 0x01f25800" in out[2]
    assert out[-2].startswith("W 0x61 [256] aa cb 55 ac 38")  # save
    assert (tmp_path / "out.bin").stat().st_size == 108_812


def test_bad_widget_rejected():
    with pytest.raises(SystemExit):
        cli.main(["--dry-run", "overlay", "nonsense"])
