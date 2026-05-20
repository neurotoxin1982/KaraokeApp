/**
 * CDG (CD+Graphics) player — Canvas-based renderer.
 *
 * CDG format spec:
 *  - 24 bytes per packet at 75 packets/second (aligned with CD audio sectors)
 *  - Byte 0: command (6-bit masked, must equal 0x09 for SC data)
 *  - Byte 1: instruction (6-bit masked)
 *  - Bytes 2-3: parityQ (ignored)
 *  - Bytes 4-19: data (16 bytes)
 *  - Bytes 20-23: parityP (ignored)
 *  - Canvas: 300 × 216 pixels. Tiles: 6 wide × 12 tall. Grid: 50 col × 18 row.
 */

const CDG_PACKET_SIZE = 24;
const CDG_PACKETS_PER_SEC = 75;
const CDG_WIDTH = 300;
const CDG_HEIGHT = 216;
const CDG_CMD = 0x09;

const CDG_INSTR = {
  MEMORY_PRESET: 1,
  BORDER_PRESET: 2,
  TILE_BLOCK: 6,
  SCROLL_PRESET: 20,
  SCROLL_COPY: 21,
  DEF_TRANSPARENT: 30,
  LOAD_CTAB_LOW: 31,
  LOAD_CTAB_HIGH: 32,
  TILE_BLOCK_XOR: 38,
};

// Global sync offset in seconds (negative = CDG waits, positive = CDG rushes ahead).
// Exposed so the player page can adjust it via queue page controls.
let cdgSyncOffset = 0;

class CdgPlayer {
  constructor(canvas, cdgUrl, audioElement) {
    this.canvas = canvas;
    this.cdgUrl = cdgUrl;
    this.audio = audioElement;

    canvas.width = CDG_WIDTH;
    canvas.height = CDG_HEIGHT;
    this.ctx = canvas.getContext('2d');

    this._imageData = this.ctx.createImageData(CDG_WIDTH, CDG_HEIGHT);
    this._pixels = new Uint8Array(CDG_WIDTH * CDG_HEIGHT);
    this._colorTable = new Array(16).fill(0);
    this._bgColor = 0;
    this._borderColor = 0;
    this._transparentColor = -1;

    this._cdgData = null;
    this._packetIndex = 0;
    this._rafId = null;
    this._stopped = false;
    this._paused = false;

    // Try to measure audio output latency once
    this._outputLatency = 0;
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      this._outputLatency = (ctx.outputLatency || ctx.baseLatency || 0);
      ctx.close();
    } catch (_) {}
  }

  async load() {
    const resp = await fetch(this.cdgUrl);
    const buf = await resp.arrayBuffer();
    this.loadFromBuffer(buf);
  }

  loadFromBuffer(buffer) {
    this._cdgData = new Uint8Array(buffer);
    this._reset();
    this._renderLoop();
  }

  _reset() {
    this._packetIndex = 0;
    this._pixels.fill(0);
    this._colorTable.fill(0);
    this._bgColor = 0;
    this._stopped = false;
    this._paused = false;
  }

  stop() {
    this._stopped = true;
    if (this._rafId) { cancelAnimationFrame(this._rafId); this._rafId = null; }
  }

  pause() { this._paused = true; }
  resume() { this._paused = false; }

  _renderLoop() {
    if (this._stopped) return;
    this._rafId = requestAnimationFrame(() => this._renderLoop());
    if (this._paused || !this._cdgData) return;

    // Subtract output latency and add user sync offset.
    // outputLatency: audio is heard this many seconds AFTER currentTime was set.
    // cdgSyncOffset: user-adjustable fine-tuning (negative slows CDG down).
    const adjusted = Math.max(0,
      this.audio.currentTime - this._outputLatency + cdgSyncOffset
    );
    const targetPacket = Math.floor(adjusted * CDG_PACKETS_PER_SEC);
    while (this._packetIndex <= targetPacket) {
      const offset = this._packetIndex * CDG_PACKET_SIZE;
      if (offset + CDG_PACKET_SIZE > this._cdgData.length) break;
      this._processPacket(offset);
      this._packetIndex++;
    }
    this._paint();
  }

  _processPacket(offset) {
    const d = this._cdgData;
    const cmd = d[offset] & 0x3F;
    if (cmd !== CDG_CMD) return;
    const instr = d[offset + 1] & 0x3F;
    const data = d.subarray(offset + 4, offset + 20);

    switch (instr) {
      case CDG_INSTR.MEMORY_PRESET: this._memoryPreset(data); break;
      case CDG_INSTR.BORDER_PRESET: this._borderPreset(data); break;
      case CDG_INSTR.TILE_BLOCK: this._tileBlock(data, false); break;
      case CDG_INSTR.TILE_BLOCK_XOR: this._tileBlock(data, true); break;
      case CDG_INSTR.SCROLL_PRESET: this._scroll(data, false); break;
      case CDG_INSTR.SCROLL_COPY: this._scroll(data, true); break;
      case CDG_INSTR.DEF_TRANSPARENT: this._transparentColor = data[0] & 0x0F; break;
      case CDG_INSTR.LOAD_CTAB_LOW: this._loadColorTable(data, 0); break;
      case CDG_INSTR.LOAD_CTAB_HIGH: this._loadColorTable(data, 8); break;
    }
  }

  _loadColorTable(data, base) {
    for (let i = 0; i < 8; i++) {
      const hi = data[i * 2] & 0x3F;
      const lo = data[i * 2 + 1] & 0x3F;
      const r4 = (hi >> 2) & 0x0F;
      const g4 = ((hi & 0x03) << 2) | ((lo >> 4) & 0x03);
      const b4 = lo & 0x0F;
      // Scale 4-bit to 8-bit (0xF -> 0xFF)
      const r = r4 * 17;
      const g = g4 * 17;
      const b = b4 * 17;
      this._colorTable[base + i] = (0xFF << 24) | (b << 16) | (g << 8) | r;
    }
  }

  _memoryPreset(data) {
    const color = data[0] & 0x0F;
    const repeat = data[1] & 0x0F;
    if (repeat === 0) {
      this._bgColor = color;
      this._pixels.fill(color);
    }
  }

  _borderPreset(data) {
    const color = data[0] & 0x0F;
    this._borderColor = color;
    for (let y = 0; y < CDG_HEIGHT; y++) {
      for (let x = 0; x < CDG_WIDTH; x++) {
        if (x < 6 || x >= CDG_WIDTH - 6 || y < 12 || y >= CDG_HEIGHT - 12) {
          this._pixels[y * CDG_WIDTH + x] = color;
        }
      }
    }
  }

  _tileBlock(data, xor) {
    const color0 = data[0] & 0x0F;
    const color1 = data[1] & 0x0F;
    const row = data[2] & 0x1F;
    const col = data[3] & 0x3F;
    const tileX = col * 6;
    const tileY = row * 12;

    if (tileX + 6 > CDG_WIDTH || tileY + 12 > CDG_HEIGHT) return;

    for (let r = 0; r < 12; r++) {
      const rowByte = data[4 + r] & 0x3F;
      for (let c = 0; c < 6; c++) {
        const bit = (rowByte >> (5 - c)) & 0x01;
        const px = tileX + c;
        const py = tileY + r;
        const idx = py * CDG_WIDTH + px;
        if (xor) {
          this._pixels[idx] ^= bit ? color1 : color0;
        } else {
          this._pixels[idx] = bit ? color1 : color0;
        }
      }
    }
  }

  _scroll(data, copy) {
    const color = data[0] & 0x0F;
    const hScroll = data[1] & 0x3F;
    const vScroll = data[2] & 0x3F;

    const hSCmd = (hScroll & 0x30) >> 4; // 0=none, 1=right, 2=left
    const hSOff = hScroll & 0x07;
    const vSCmd = (vScroll & 0x30) >> 4; // 0=none, 1=down, 2=up
    const vSOff = vScroll & 0x0F;

    const old = this._pixels.slice();
    const W = CDG_WIDTH, H = CDG_HEIGHT;

    let dx = 0, dy = 0;
    if (hSCmd === 1) dx = hSOff;
    else if (hSCmd === 2) dx = -(W - hSOff);
    if (vSCmd === 1) dy = vSOff;
    else if (vSCmd === 2) dy = -(H - vSOff);

    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const srcX = (x - dx + W) % W;
        const srcY = (y - dy + H) % H;
        this._pixels[y * W + x] = copy ? old[srcY * W + srcX] : color;
      }
    }

    if (copy) {
      // wrap-around edge fill done by the modulo above
    } else {
      // fill the exposed strip with color (already done by default)
    }
  }

  _paint() {
    const px = this._imageData.data;
    const W = CDG_WIDTH;
    for (let i = 0; i < this._pixels.length; i++) {
      const rgba = this._colorTable[this._pixels[i]] || 0;
      const base = i * 4;
      px[base]     = rgba & 0xFF;         // R
      px[base + 1] = (rgba >> 8) & 0xFF;  // G
      px[base + 2] = (rgba >> 16) & 0xFF; // B
      px[base + 3] = 255;                 // A
    }
    this.ctx.putImageData(this._imageData, 0, 0);
  }
}
