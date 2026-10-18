# AORUS RTX 30-series LCD protocol

What Gigabyte's software sends to the LCD Edge View panel of the RTX 30-series AORUS cards,
from static reverse engineering of AORUS Engine 2.1.2, verified on an RTX 3080 (subsystem
`1458:403E`, panel firmware 0x20). Function names (`FUN_…`) refer to Ghidra's auto-naming.
No Gigabyte binaries or decompiled code are part of this repository.

Source: AORUS Engine 2.1.2 (`vga_utility_aorus_setup_v2.1.2.exe`, Wayback copy, sha256 4e692d1b…)
→ `UpdPack.exe` (7z SFX) → InstallShield `setup.exe` (`ISSetupStream` overlay, per-file XOR/nibble
obfuscation + zlib)
→ `RGB Fusion.msi` → `LIB/N30Setup.exe` (Inno Setup, "AORUS LCD Panel Setting", 2021)
→ `Panel.exe` + `GvDisplay.dll`. Decompiled with Ghidra 12.1 headless.
RGB Fusion 2.0's `VGAUI.dll` launches `\GIGABYTE\AORUS LCD Panel Setting\Panel.exe`.

## Transport (Windows)
`Panel.exe` → `GvWriteI2C/GvReadI2C(card, req)` (GvDisplay.dll) → vendor backend →
32-bit **`nvapi.dll`** `NvAPI_I2CWriteEx` (0x283AC65A) / `NvAPI_I2CReadEx` (0x4D7B0709).

Gigabyte request struct → `NV_I2C_INFO_V3` (version 0x3002C):

| req offset | value used by Panel.exe | NV_I2C_INFO field |
|---|---|---|
| 0x000 u8  | **0xC2** (= 7-bit **0x61**) | i2cDevAddress |
| 0x004 u32 | 0 | regAddrSize (no register address) |
| 0x008 [256] | command frame / chunk | pbData (also passed as pbI2cRegAddress, unused with size 0) |
| 0x108 u32 | 0x100 | cbSize |
| 0x10C u32 | 400 | i2cSpeed (deprecated field); i2cSpeedKhz = 0 (default) |
| 0x110 u8  | **1** | portId, bIsPortIdSet = 1, bIsDDCPort = 0 |

Query (GvReadI2C): WriteEx of the 256-byte frame, then ReadEx of N bytes (4 or 8).

⇒ Same address and port as the RTX 5090 tools (c-drew, albancreton): **0x61 on internal port 1**.

## Frames
Every command is `[op] CB 55 AC 38 [params…]`, zero-padded to 256 bytes (as for the 5090).
Opcodes used by Panel.exe: D6, D8, DE, DF, EB, ED, F4 (queries); E1, E3, E5, EA, AA, F1, F2, F3, 66.

| op | use in Panel.exe |
|---|---|
| D6 | firmware version, read 4 → byte[1] is the version; behaviour switches at > 0x11 |
| D8 | "Double Status", read 4 → three values; lets the upload skip the flash erase |
| DE / DF | mode / overlay read-back (as on the 5090) |
| EB | overlay template style read, param = kind, read 8 → R G B enabled |
| ED | overlay template position read, param = kind, read 8 → X u16, Y u16 (BE) |
| F4 | indexed list read, read 8 per index (param byte = index 1…) |
| E5 | set mode, byte 5 = mode |
| F2 | upload marker, byte 5: 1 = BEGIN, 2 = END |
| F1 | upload header (layout below) |
| 66 … AB CD EF | abort an upload |

### Upload (FUN_00406bb0 "Start Sending Image")
1. Sleep 2 s, `F2 … 01` (BEGIN), sleep 0.5 s
2. `F1 CB 55 AC 38` + `fb_addr` (u32 BE, bytes 5–8) + flag (9) + chunk count (u32 BE, 10–13)
   + nframes (u16 BE, 14–15) + delay (16, capped 0xFF) + erase mode (17: 1 = 4 KB, 2 = 64 KB for
   payloads ≥ 0x5000) + bool (18)
   - type 1: fb_addr **0x01F25800**, flag 1; type 2: fb_addr **0x01F00000**, flag 1; else 0, flag 2
     (5090: image 0x01300000, text 0x01320000, gif 0)
   - chunk count = len/256 + 1
3. Wait for the erase (400 ms per erase unit), 1 s, then 256-byte chunks with 5 ms pauses;
   retry a failed chunk, re-init GvDisplay after 5 failures
4. `F2 … 02` (END)

### Payload (the uploaded file is `assets\<static|animation>.bin`, built by `imger.exe`, .NET)
Panel.exe runs `imger.exe <src> W H x y w h <out> depth bpp NONE|RLE`; imger crops/scales to 320×170,
writes .bmp/.bak.bmp previews and the upload file `<out>.bin` (SaveZipData):
`[count u16 LE]` + per frame `[end_offset-1 u32, w u16, h u16, fmt u16]` (LE) + per-frame blobs,
blobs = top-down little-endian RGB565 (LockBits Format16bppRgb565), fmt 1 = NONE (raw), 3 = RLE.
For GIFs the first frame is stored twice. **Identical to c-drew's `frame_table` + pixels**; a still
image is 108 812 bytes → 426 chunks.

### Upload type per page (Panel.exe)
| page | E5 byte | F1 type | fb_addr | F1 flag |
|---|---|---|---|---|
| image (mode 3) | 4 | 1 | 0x01F25800 | 1 |
| text (mode 4) | 5 | 2 | 0x01F00000 | 1 |
| gif (mode 5) | 6 | 0 | 0 (nframes + delay in header) | 2 |
Modes: 0–2 logo animations ("Faith1–3", E5 1–3), 6 pet (E5 7), 9 carousel (E5 10).
After an upload: E5 (mode+1), then AA (save). Selecting a page in the UI sends E5 first.
The GIF path queries D8 first (fw > 0x11) and skips the flash erase when D8 reports a free buffer.

Panel: 320×170. Default assets: "Faith" logo animations, pet.bmp, animation = 148 RLE frames.

## Stats overlay (no flash writes)
The panel draws GPU stats on top of its content itself; the host only chooses widgets and feeds values.

| op | layout after the magic |
|---|---|
| E1 | 8 widget flags (1 = on) in order temp, clock, usage, fan, vram_clock, vram, fps, power, then the rotation interval in s; all 0 = overlay off |
| E3 | sensor feed, big-endian: temp u8, clock u16, usage u8, fan u16, vram_clock u16, vram u8, fps u16, power u16 (MonitorService.exe; same as on the 5090) |
| EA | template write: kind u8, R G B, 0 u16, 320 u16, X u16, Y u16, enabled u8 (Panel.exe FUN_004067f0) |

Template kinds 1 and 2 are stored per mode. E1/EA take effect immediately; AA persists them.

### Verified on hardware (2026-10-18, panel firmware 0x20)
D6 → `d6 20 01 02`; status: on, mode 1, all 8 overlay widgets, rotating every 4 s.
Still-image upload, mode switching, E1 overlay, E3 live feed and EA template writes work as described.

## Other files in the package
- `Updater/*/AP`: ARM Cortex-M firmware for the panel's MCU, flashed by `FWUpgrade.exe`. **Never run it.**
- `MonitorService.exe`: the live-sensor feed (E3, below).
