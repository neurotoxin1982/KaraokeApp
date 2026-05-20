/**
 * CDG (CD+Graphics) player — Web Audio API for sample-accurate sync.
 *
 * Timing model:
 *   Both CDG and MP3 share the same AudioContext.currentTime clock.
 *   The source is scheduled at a future AudioContext time (startAt).
 *   CDG position = audioCtx.currentTime - startAt
 *   This gives sub-millisecond sync with zero drift.
 */

const CDG_PACKET_SIZE     = 24;
const CDG_PACKETS_PER_SEC = 75;
const CDG_WIDTH  = 300;
const CDG_HEIGHT = 216;
const CDG_CMD    = 0x09;

const CDG_INSTR = {
  MEMORY_PRESET:   1,  BORDER_PRESET:   2,
  TILE_BLOCK:      6,  TILE_BLOCK_XOR: 38,
  SCROLL_PRESET:  20,  SCROLL_COPY:    21,
  LOAD_CTAB_LOW:  31,  LOAD_CTAB_HIGH: 32,
};

// Adjustable from the queue page (seconds). Positive = CDG runs ahead.
let cdgSyncOffset = 0;

// ─────────────────────────────────────────────────────────────────────────────

class CdgPlayer {
  constructor(canvas) {
    this.canvas = canvas;
    canvas.width  = CDG_WIDTH;
    canvas.height = CDG_HEIGHT;
    this.ctx = canvas.getContext('2d');

    this._imageData  = this.ctx.createImageData(CDG_WIDTH, CDG_HEIGHT);
    this._pixels     = new Uint8Array(CDG_WIDTH * CDG_HEIGHT);
    this._colorTable = new Array(16).fill(0);
    this._bgColor    = 0;

    this._cdgData     = null;
    this._packetIndex = 0;
    this._rafId       = null;
    this._stopped     = false;
    this._paused      = false;

    // AudioContext shared with the audio source
    this._audioCtx  = null;
    this._startAt   = 0; // AudioContext time when song starts
  }

  // ── Public API ────────────────────────────────────────────────────────────

  loadFromBuffer(buffer) {
    this._cdgData = new Uint8Array(buffer);
    this._hardReset();
  }

  /**
   * Begin rendering. Call this AFTER scheduling audio.start(startAt).
   * @param {AudioContext} audioCtx
   * @param {number} startAt  - AudioContext.currentTime of the audio start
   */
  start(audioCtx, startAt) {
    this._audioCtx = audioCtx;
    this._startAt  = startAt;
    this._stopped  = false;
    this._paused   = false;
    if (!this._rafId) this._renderLoop();
  }

  pause() {
    this._paused   = true;
    this._pausedPos = this._position();
  }

  resume(audioCtx, startAt) {
    this._audioCtx = audioCtx;
    this._startAt  = startAt;
    this._paused   = false;
    if (!this._rafId) this._renderLoop();
  }

  stop() {
    this._stopped = true;
    if (this._rafId) { cancelAnimationFrame(this._rafId); this._rafId = null; }
  }

  /**
   * Jump to a new position after a seek.
   * @param {number} seekPos   - new position in seconds
   * @param {AudioContext} audioCtx
   * @param {number} startAt  - AudioContext.currentTime of the NEW audio start
   */
  seekTo(seekPos, audioCtx, startAt) {
    this._audioCtx = audioCtx;
    this._startAt  = startAt;
    // Replay all CDG packets from 0 → seekPos
    this._hardReset();
    const target = Math.floor(seekPos * CDG_PACKETS_PER_SEC);
    for (let i = 0; i <= target; i++) {
      const off = i * CDG_PACKET_SIZE;
      if (off + CDG_PACKET_SIZE > this._cdgData.length) break;
      this._processPacket(off);
    }
    this._packetIndex = target + 1;
    this._paint();
  }

  // ── Internals ─────────────────────────────────────────────────────────────

  _hardReset() {
    this._packetIndex = 0;
    this._pixels.fill(0);
    this._colorTable.fill(0);
    this._bgColor = 0;
  }

  _position() {
    if (!this._audioCtx) return 0;
    return Math.max(0, this._audioCtx.currentTime - this._startAt + cdgSyncOffset);
  }

  _renderLoop() {
    if (this._stopped) return;
    this._rafId = requestAnimationFrame(() => this._renderLoop());
    if (this._paused || !this._cdgData || !this._audioCtx) return;

    const pos    = this._position();
    const target = Math.floor(pos * CDG_PACKETS_PER_SEC);

    if (target < this._packetIndex - 1) {
      // Backward (sync offset changed): replay from 0
      this._hardReset();
    }

    while (this._packetIndex <= target) {
      const off = this._packetIndex * CDG_PACKET_SIZE;
      if (off + CDG_PACKET_SIZE > this._cdgData.length) break;
      this._processPacket(off);
      this._packetIndex++;
    }

    this._paint();
  }

  // ── CDG instruction handlers ──────────────────────────────────────────────

  _processPacket(offset) {
    const d = this._cdgData;
    if ((d[offset] & 0x3F) !== CDG_CMD) return;
    const instr = d[offset + 1] & 0x3F;
    const data  = d.subarray(offset + 4, offset + 20);
    switch (instr) {
      case CDG_INSTR.MEMORY_PRESET:   this._memoryPreset(data);      break;
      case CDG_INSTR.BORDER_PRESET:   this._borderPreset(data);      break;
      case CDG_INSTR.TILE_BLOCK:      this._tileBlock(data, false);  break;
      case CDG_INSTR.TILE_BLOCK_XOR:  this._tileBlock(data, true);   break;
      case CDG_INSTR.SCROLL_PRESET:   this._scroll(data, false);     break;
      case CDG_INSTR.SCROLL_COPY:     this._scroll(data, true);      break;
      case CDG_INSTR.LOAD_CTAB_LOW:   this._loadColorTable(data, 0); break;
      case CDG_INSTR.LOAD_CTAB_HIGH:  this._loadColorTable(data, 8); break;
    }
  }

  _loadColorTable(data, base) {
    for (let i = 0; i < 8; i++) {
      const hi = data[i * 2]     & 0x3F;
      const lo = data[i * 2 + 1] & 0x3F;
      const r  = ((hi >> 2) & 0x0F) * 17;
      const g  = (((hi & 3) << 2) | ((lo >> 4) & 3)) * 17;
      const b  = (lo & 0x0F) * 17;
      this._colorTable[base + i] = (0xFF << 24) | (b << 16) | (g << 8) | r;
    }
  }

  _memoryPreset(data) {
    if ((data[1] & 0x0F) === 0) {
      this._bgColor = data[0] & 0x0F;
      this._pixels.fill(this._bgColor);
    }
  }

  _borderPreset(data) {
    const c = data[0] & 0x0F;
    for (let y = 0; y < CDG_HEIGHT; y++)
      for (let x = 0; x < CDG_WIDTH; x++)
        if (x < 6 || x >= CDG_WIDTH - 6 || y < 12 || y >= CDG_HEIGHT - 12)
          this._pixels[y * CDG_WIDTH + x] = c;
  }

  _tileBlock(data, xor) {
    const c0 = data[0] & 0x0F, c1 = data[1] & 0x0F;
    const tx = (data[3] & 0x3F) * 6;
    const ty = (data[2] & 0x1F) * 12;
    if (tx + 6 > CDG_WIDTH || ty + 12 > CDG_HEIGHT) return;
    for (let r = 0; r < 12; r++) {
      const row = data[4 + r] & 0x3F;
      for (let c = 0; c < 6; c++) {
        const bit = (row >> (5 - c)) & 1;
        const idx = (ty + r) * CDG_WIDTH + (tx + c);
        this._pixels[idx] = xor
          ? this._pixels[idx] ^ (bit ? c1 : c0)
          : (bit ? c1 : c0);
      }
    }
  }

  _scroll(data, copy) {
    const hScroll = data[1] & 0x3F, vScroll = data[2] & 0x3F;
    const color   = data[0] & 0x0F;
    let dx = 0, dy = 0;
    const hCmd = (hScroll >> 4) & 3, vCmd = (vScroll >> 4) & 3;
    if (hCmd === 1) dx =  (hScroll & 7);
    if (hCmd === 2) dx = -(CDG_WIDTH  - (hScroll & 7));
    if (vCmd === 1) dy =  (vScroll & 15);
    if (vCmd === 2) dy = -(CDG_HEIGHT - (vScroll & 15));
    const old = this._pixels.slice();
    const W = CDG_WIDTH, H = CDG_HEIGHT;
    for (let y = 0; y < H; y++)
      for (let x = 0; x < W; x++)
        this._pixels[y * W + x] = copy
          ? old[((y - dy + H) % H) * W + ((x - dx + W) % W)]
          : color;
  }

  _paint() {
    const px = this._imageData.data;
    for (let i = 0; i < this._pixels.length; i++) {
      const rgba = this._colorTable[this._pixels[i]] || 0;
      const b = i * 4;
      px[b]   =  rgba        & 0xFF;
      px[b+1] = (rgba >>  8) & 0xFF;
      px[b+2] = (rgba >> 16) & 0xFF;
      px[b+3] = 255;
    }
    this.ctx.putImageData(this._imageData, 0, 0);
  }
}
