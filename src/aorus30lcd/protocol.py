"""Frames for the RTX 30-series AORUS LCD Edge View (panel firmware 0x20).

Every command is `[op] CB 55 AC 38 [params…]`, zero-padded to 256 bytes, written to 0x61 on the
GPU's internal I2C port 1. Layouts mirror what Gigabyte's "AORUS LCD Panel Setting" (Panel.exe,
AORUS Engine 2.1.2) and MonitorService.exe send; see docs/PROTOCOL.md. Pure functions, no I/O.
"""
import struct

W, H = 320, 170
MAGIC = bytes([0xCB, 0x55, 0xAC, 0x38])
ADDR = 0x61
EXPECTED_FW = 0x20

# F1 targets (Panel.exe FUN_00406bb0): type 1 = image, 2 = text, 0 = animation (live buffer)
FB_IMAGE, FB_TEXT, FB_ANIM = 0x01F25800, 0x01F00000, 0x00000000
OP_GET_FW, OP_DOUBLE_STATUS, OP_GET_MODE, OP_GET_DISPLAY = 0xD6, 0xD8, 0xDE, 0xDF
OP_SET_MODE, OP_SAVE, OP_HEADER, OP_MARK = 0xE5, 0xAA, 0xF1, 0xF2
OP_SET_DISPLAY, OP_SENSOR_FEED = 0xE1, 0xE3
OP_SET_TEMPLATE, OP_GET_TEMPLATE, OP_GET_TEMPLATE_POS = 0xEA, 0xEB, 0xED

# Overlay slots, in E1 flag order and E3 field order (MonitorService.exe / Panel.exe)
WIDGETS = ("temp", "clock", "usage", "fan", "vram_clock", "vram", "fps", "power")
U16_WIDGETS = {"clock", "fan", "vram_clock", "fps", "power"}

# Modes as shown in Panel.exe; the E5 byte is mode + 1
MODES = {0: "logo animation 1", 1: "logo animation 2", 2: "logo animation 3", 3: "image",
         4: "text", 5: "gif", 6: "pet", 9: "carousel"}
MODE_IMAGE = 3


def frame(op, tail=b""):
    return bytes([op]) + MAGIC + bytes(tail) + bytes(256 - 5 - len(tail))


def set_mode(mode):
    return frame(OP_SET_MODE, bytes([mode + 1]))


def set_display(widgets, interval):
    """E1 [8 x on/off][interval s]; no widgets = overlay off."""
    flags = bytes(1 if w in widgets else 0 for w in WIDGETS)
    return frame(OP_SET_DISPLAY, flags + bytes([max(0, min(255, interval)) if widgets else 0]))


def sensor_feed(values):
    """E3 [temp u8][clock u16][usage u8][fan u16][vram_clock u16][vram u8][fps u16][power u16], BE."""
    tail = b""
    for w in WIDGETS:
        v = int(round(values.get(w, 0) or 0))
        tail += (max(0, min(0xFFFF, v)).to_bytes(2, "big") if w in U16_WIDGETS
                 else bytes([max(0, min(0xFF, v))]))
    return frame(OP_SENSOR_FEED, tail)


def set_template(kind, color, pos, enabled=True):
    """EA [kind][R G B][0 u16][320 u16][X u16][Y u16][enabled], as Panel.exe FUN_004067f0 sends it."""
    tail = bytes([kind, *color])
    for v in (0, W, *pos):
        tail += max(0, min(0xFFFF, int(v))).to_bytes(2, "big")
    return frame(OP_SET_TEMPLATE, tail + bytes([1 if enabled else 0]))


def parse_template(style, pos):
    """Replies to EB [kind] (R G B enabled) and ED [kind] (X u16, Y u16, BE)."""
    return {"color": tuple(style[:3]), "enabled": bool(style[3]),
            "pos": (int.from_bytes(pos[0:2], "big"), int.from_bytes(pos[2:4], "big"))}


def rgb565(img):
    """Top-down little-endian RGB565, as imger.exe gets it from LockBits(Format16bppRgb565)."""
    px = img.convert("RGB").tobytes()
    out = bytearray(W * H * 2)
    for i in range(W * H):
        r, g, b = px[3 * i], px[3 * i + 1], px[3 * i + 2]
        struct.pack_into("<H", out, 2 * i, ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    return bytes(out)


def bin_payload(frames, fmt=1):
    """imger.exe SaveZipData: [count u16] + per frame [end-1 u32, w u16, h u16, fmt u16] + blobs.
    fmt 1 = NONE (raw RGB565), 3 = RLE."""
    out = bytearray(len(frames).to_bytes(2, "little"))
    end = 2 + 10 * len(frames)
    for blob in frames:
        end += len(blob)
        out += struct.pack("<IHHH", end - 1, W, H, fmt)
    return bytes(out) + b"".join(frames)


def upload_frames(payload, fb, flag, nframes=0, delay=0, erase=None):
    """[(frame, pause_after_s)] exactly in Panel.exe's order and pacing."""
    chunks = len(payload) // 256 + 1
    if erase is None:
        erase = 2 if len(payload) > 0x4FFF else 1          # Panel.exe: 64 KB blocks for >= 0x5000
    unit = 0x10000 if erase == 2 else 0x1000
    erases = (len(payload) + unit - 1) // unit
    header = (frame(OP_HEADER, fb.to_bytes(4, "big") + bytes([flag]) + chunks.to_bytes(4, "big")
                    + nframes.to_bytes(2, "big") + bytes([min(delay, 0xFF), erase, 0])))
    padded = payload + bytes(chunks * 256 - len(payload))
    out = [(frame(OP_MARK, b"\x01"), 0.5), (header, 1.0 + erases * 0.6)]   # Panel.exe: 0.4 s/erase + 1 s
    out += [(padded[i:i + 256], 0.005) for i in range(0, len(padded), 256)]
    out[-1] = (out[-1][0], 0.5)
    out.append((frame(OP_MARK, b"\x02"), 0.5))
    return out
