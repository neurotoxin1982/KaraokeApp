/**
 * CDG (CD+Graphics) player — Canvas-based renderer.
 *
 * Sync strategy:
 *  - audio.currentTime is the source of truth but updates only ~50-100 Hz.
 *  - We interpolate between updates using performance.now() for smooth 60fps.
 *  - CDG rendering starts only after the 'playing' event fires (audio confirmed running).
 *  - cdgSyncOffset (global, seconds) lets the user trim A/V lag at runtime.
 */

const CDG_PACKET_SIZE    = 24;
const CDG_PACKETS_PER_SEC = 75;
const CDG_WIDTH  = 300;
const CDG_HEIGHT = 216;
const CDG_CMD    = 0x09;

const CDG_INSTR = {
  MEMORY_PRESET:   1,
  BORDER_PRESET:   2,
  TILE_BLOCK:      6,
  SCROLL_PRESET:  20,
  SCROLL_COPY:    21,
  DEF_TRANSPARENT:30,
  LOAD_CTAB_LOW:  31,
  LOAD_CTAB_HIGH: 32,
  TILE_BLOCK_XOR: 38,
};

// Adjustable by the queue page via BroadcastChannel.
// Positive = CDG runs ahead (delay audio visually); Negative = CDG waits.
let cdgSyncOffset = 0;

// ─────────────────────────────────────────────────────────────────────────────

class CdgPlayer {
  constructor(canvas, audioElement) {
    this.canvas = canvas;
    this.audio  = audioElement;

    canvas.width  = CDG_WIDTH;
    canvas.height = CDG_HEIGHT;
    this.ctx = canvas.getContext('2d');

    this._imageData = this.ctx.createImageData(CDG_WIDTH, CDG_HEIGHT);
    this._pixels    = new Uint8Array(CDG_WIDTH * CDG_HEIGHT);
    this._colorTable = new Array(16).fill(0);
    this._bgColor   = 0;

    this._cdgData     = null;
    this._packetIndex = 0;
    this._rafId       = null;
    this._stopped     = false;
    this._paused      = false;

    // Interpolation state
    this._refAudioTime = 0;
    this._refWallTime  = 0;
  }

  // ── Load & start ────────────────────────────────────────────────────────

  loadFromBuffer(buffer) {
    this._cdgData = new Uint8Array(buffer);
    this._hardReset();

    const onPlaying = () => {
      // Capture the exact moment audio starts playing.
      this._refAudioTime = this.audio.currentTime;
      this._refWallTime  = performance.now();
      if (!this._rafId) this._renderLoop();
    };

    if (!this.audio.paused) {
      onPlaying();
    } else {
      this.audio.addEventListener('playing', onPlaying, { once: true });
    }

    // Re-sync reference whenever currentTime jumps (seek / rate change).
    this.audio.addEventListener('seeked', () => {
      this._refAudioTime = this.audio.currentTime;
      this._refWallTime  = performance.now();
      // Re-process all CDG packets up to the new position.
      this._hardReset();
      const target = Math.floor(this.audio.currentTime * CDG_PACKETS_PER_SEC);
      for (let i = 0; i <= target && i * CDG_PACKET_SIZE + CDG_PACKET_SIZE <= this._cdgData.length; i++) {
        this._processPacket(i * CDG_PACKET_SIZE);
      }
      this._packetIndex = target + 1;
      this._paint();
    });
  }

  // ── Controls ─────────────────────────────────────────────────────────────

  stop() {
    this._stopped = true;
    if (this._rafId) { cancelAnimationFrame(this._rafId); this._rafId = null; }
  }

  pause()  { this._paused = true; }
  resume() {
    this._paused = false;
    // Re-anchor wall clock reference so we don't jump on resume.
    this._refAudioTime = this.audio.currentTime;
    this._refWallTime  = performance.now();
  }

  // ── Internal ─────────────────────────────────────────────────────────────

  _hardReset() {
    this._packetIndex = 0;
    this._pixels.fill(0);
    this._colorTable.fill(0);
    this._bgColor = 0;
    this._stopped = false;
    this._paused  = false;
  }

  /** Smooth interpolated playback position in seconds. */
  _position() {
    const audioNow = this.audio.currentTime;
    const wallNow  = performance.now();

    // Re-anchor when audio time changes by more than 1ms (avoids drift).
    if (Math.abs(audioNow - this._refAudioTime) > 0.001) {
      this._refAudioTime = audioNow;
      this._refWallTime  = wallNow;
    }

    const interpolated = this._refAudioTime + (wallNow - this._refWallTime) / 1000;
    return Math.max(0, interpolated + cdgSyncOffset);
  }

  _renderLoop() {
    if (this._stopped) return;
    this._rafId = requestAnimationFrame(() => this._renderLoop());
    if (this._paused || !this._cdgData || this.audio.paused) return;

    const pos          = this._position();
    const targetPacket = Math.floor(pos * CDG_PACKETS_PER_SEC);

    // If we somehow got ahead (e.g. after a backward sync adjustment), re-process.
    if (targetPacket < this._packetIndex - 1) {
      this._hardReset();
    }

    while (this._packetIndex <= targetPacket) {
      const offset = this._packetIndex * CDG_PACKET_SIZE;
      if (offset + CDG_PACKET_SIZE > this._cdgData.length) break;
      this._processPacket(offset);
      this._packetIndex++;
    }

    this._paint();
  }

  // ── CDG instruction processing ────────────────────────────────────────────

  _processPacket(offset) {
    const d    = this._cdgData;
    const cmd  = d[offset]     & 0x3F;
    if (cmd !== CDG_CMD) return;
    const instr = d[offset + 1] & 0x3F;
    const data  = d.subarray(offset + 4, offset + 20);

    switch (instr) {
      case CDG_INSTR.MEMORY_PRESET:    this._memoryPreset(data);       break;
      case CDG_INSTR.BORDER_PRESET:    this._borderPreset(data);       break;
      case CDG_INSTR.TILE_BLOCK:       this._tileBlock(data, false);   break;
      case CDG_INSTR.TILE_BLOCK_XOR:   this._tileBlock(data, true);    break;
      case CDG_INSTR.SCROLL_PRESET:    this._scroll(data, false);      break;
      case CDG_INSTR.SCROLL_COPY:      this._scroll(data, true);       break;
      case CDG_INSTR.DEF_TRANSPARENT:  /* ignored */                   break;
      case CDG_INSTR.LOAD_CTAB_LOW:    this._loadColorTable(data, 0);  break;
      case CDG_INSTR.LOAD_CTAB_HIGH:   this._loadColorTable(data, 8);  break;
    }
  }

  _loadColorTable(data, base) {
    for (let i = 0; i < 8; i++) {
      const hi = data[i * 2]     & 0x3F;
      const lo = data[i * 2 + 1] & 0x3F;
      const r4 = (hi >> 2) & 0x0F;
      const g4 = ((hi & 0x03) << 2) | ((lo >> 4) & 0x03);
      const b4 = lo & 0x0F;
      this._colorTable[base + i] = (0xFF << 24) | ((b4 * 17) << 16) | ((g4 * 17) << 8) | (r4 * 17);
    }
  }

  _memoryPreset(data) {
    const color  = data[0] & 0x0F;
    const repeat = data[1] & 0x0F;
    if (repeat === 0) { this._bgColor = color; this._pixels.fill(color); }
  }

  _borderPreset(data) {
    const color = data[0] & 0x0F;
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
    const row    = data[2] & 0x1F;
    const col    = data[3] & 0x3F;
    const tileX  = col * 6;
    const tileY  = row * 12;
    if (tileX + 6 > CDG_WIDTH || tileY + 12 > CDG_HEIGHT) return;

    for (let r = 0; r < 12; r++) {
      const rowByte = data[4 + r] & 0x3F;
      for (let c = 0; c < 6; c++) {
        const bit = (rowByte >> (5 - c)) & 0x01;
        const idx = (tileY + r) * CDG_WIDTH + (tileX + c);
        this._pixels[idx] = xor
          ? this._pixels[idx] ^ (bit ? color1 : color0)
          : (bit ? color1 : color0);
      }
    }
  }

  _scroll(data, copy) {
    const hScroll = data[1] & 0x3F;
    const vScroll = data[2] & 0x3F;
    const color   = data[0] & 0x0F;

    const hCmd = (hScroll & 0x30) >> 4;
    const vCmd = (vScroll & 0x30) >> 4;
    const hOff =  hScroll & 0x07;
    const vOff =  vScroll & 0x0F;

    let dx = 0, dy = 0;
    if (hCmd === 1) dx = hOff; else if (hCmd === 2) dx = -(CDG_WIDTH  - hOff);
    if (vCmd === 1) dy = vOff; else if (vCmd === 2) dy = -(CDG_HEIGHT - vOff);

    const old = this._pixels.slice();
    const W = CDG_WIDTH, H = CDG_HEIGHT;

    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const sx = (x - dx + W) % W;
        const sy = (y - dy + H) % H;
        this._pixels[y * W + x] = copy ? old[sy * W + sx] : color;
      }
    }
  }

  _paint() {
    const px = this._imageData.data;
    for (let i = 0; i < this._pixels.length; i++) {
      const rgba = this._colorTable[this._pixels[i]] || 0;
      const b    = i * 4;
      px[b]     =  rgba        & 0xFF;  // R
      px[b + 1] = (rgba >>  8) & 0xFF;  // G
      px[b + 2] = (rgba >> 16) & 0xFF;  // B
      px[b + 3] = 255;
    }
    this.ctx.putImageData(this._imageData, 0, 0);
  }
}
