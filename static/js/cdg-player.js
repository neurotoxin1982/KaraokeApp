/**
 * CDG Player — Web Audio API timing + follower mode for popout sync.
 *
 * Two modes:
 *  1. start(audioCtx, startAt)   — Main page: uses AudioContext clock.
 *     Position = currentTime - startAt - outputLatency (fixes heard-vs-scheduled gap).
 *  2. startFollower(getPosFn)    — Popout: position provided by caller (BroadcastChannel).
 */

const CDG_PACKET_SIZE     = 24;
const CDG_PACKETS_PER_SEC = 75;
const CDG_WIDTH  = 300;
const CDG_HEIGHT = 216;
const CDG_CMD    = 0x09;

const CDG_INSTR = {
  MEMORY_PRESET: 1,  BORDER_PRESET: 2,
  TILE_BLOCK:    6,  TILE_BLOCK_XOR: 38,
  SCROLL_PRESET: 20, SCROLL_COPY:   21,
  LOAD_CTAB_LOW: 31, LOAD_CTAB_HIGH: 32,
};

let cdgSyncOffset = 0; // seconds, adjustable from UI

class CdgPlayer {
  constructor(canvas) {
    this.canvas = canvas;
    canvas.width = CDG_WIDTH; canvas.height = CDG_HEIGHT;
    this.ctx = canvas.getContext('2d');
    this._img    = this.ctx.createImageData(CDG_WIDTH, CDG_HEIGHT);
    this._pixels = new Uint8Array(CDG_WIDTH * CDG_HEIGHT);
    this._clut   = new Array(16).fill(0);
    this._bgColor = 0;

    this._data   = null;
    this._pktIdx = 0;
    this._rafId  = null;
    this._stopped = false;
    this._paused  = false;

    // Mode 1: AudioContext
    this._audioCtx = null;
    this._startAt  = 0;
    this._outputLatency = 0;

    // Mode 2: Follower
    this._follower = false;
    this._getPos   = null;
  }

  loadFromBuffer(buffer) {
    this._data = new Uint8Array(buffer);
    this._hardReset();
  }

  /** Mode 1 — AudioContext timing with output latency compensation. */
  start(audioCtx, startAt) {
    this._audioCtx      = audioCtx;
    this._startAt       = startAt;
    this._outputLatency = audioCtx.outputLatency || audioCtx.baseLatency || 0;
    this._follower      = false;
    this._stopped       = false;
    this._paused        = false;
    if (!this._rafId) this._renderLoop();
  }

  /** Mode 2 — Follower: position supplied by caller (e.g. BroadcastChannel). */
  startFollower(getPosFn) {
    this._follower = true;
    this._getPos   = getPosFn;
    this._stopped  = false;
    this._paused   = false;
    if (!this._rafId) this._renderLoop();
  }

  pause()  { this._paused = true; }
  resume(audioCtx, startAt) {
    this._audioCtx = audioCtx; this._startAt = startAt;
    this._outputLatency = audioCtx.outputLatency || audioCtx.baseLatency || 0;
    this._paused = false;
    if (!this._rafId) this._renderLoop();
  }

  stop() {
    this._stopped = true;
    if (this._rafId) { cancelAnimationFrame(this._rafId); this._rafId = null; }
  }

  seekTo(pos) {
    this._hardReset();
    const target = Math.floor(pos * CDG_PACKETS_PER_SEC);
    for (let i = 0; i <= target; i++) {
      const off = i * CDG_PACKET_SIZE;
      if (off + CDG_PACKET_SIZE > this._data.length) break;
      this._processPacket(off);
    }
    this._pktIdx = target + 1;
    this._paint();
  }

  _hardReset() {
    this._pktIdx = 0;
    this._pixels.fill(0);
    this._clut.fill(0);
    this._bgColor = 0;
  }

  _position() {
    if (this._follower && this._getPos) {
      return Math.max(0, this._getPos() + cdgSyncOffset);
    }
    if (!this._audioCtx) return 0;
    // Subtract output latency: currentTime is ahead of what you hear
    return Math.max(0,
      this._audioCtx.currentTime - this._startAt - this._outputLatency + cdgSyncOffset
    );
  }

  _renderLoop() {
    if (this._stopped) return;
    this._rafId = requestAnimationFrame(() => this._renderLoop());
    if (this._paused || !this._data) return;

    const pos    = this._position();
    const target = Math.floor(pos * CDG_PACKETS_PER_SEC);

    if (target < this._pktIdx - 2) {
      // Backward jump (sync adjustment): replay from 0
      this._hardReset();
    }
    while (this._pktIdx <= target) {
      const off = this._pktIdx * CDG_PACKET_SIZE;
      if (off + CDG_PACKET_SIZE > this._data.length) break;
      this._processPacket(off);
      this._pktIdx++;
    }
    this._paint();
  }

  _processPacket(offset) {
    const d = this._data;
    if ((d[offset] & 0x3F) !== CDG_CMD) return;
    const instr = d[offset + 1] & 0x3F;
    const data  = d.subarray(offset + 4, offset + 20);
    switch (instr) {
      case CDG_INSTR.MEMORY_PRESET:  this._memPreset(data);        break;
      case CDG_INSTR.BORDER_PRESET:  this._borderPreset(data);     break;
      case CDG_INSTR.TILE_BLOCK:     this._tile(data, false);      break;
      case CDG_INSTR.TILE_BLOCK_XOR: this._tile(data, true);       break;
      case CDG_INSTR.SCROLL_PRESET:  this._scroll(data, false);    break;
      case CDG_INSTR.SCROLL_COPY:    this._scroll(data, true);     break;
      case CDG_INSTR.LOAD_CTAB_LOW:  this._loadClut(data, 0);     break;
      case CDG_INSTR.LOAD_CTAB_HIGH: this._loadClut(data, 8);     break;
    }
  }

  _loadClut(data, base) {
    for (let i = 0; i < 8; i++) {
      const hi = data[i*2]   & 0x3F, lo = data[i*2+1] & 0x3F;
      const r  = ((hi >> 2) & 0x0F) * 17;
      const g  = (((hi & 3) << 2) | ((lo >> 4) & 3)) * 17;
      const b  = (lo & 0x0F) * 17;
      this._clut[base + i] = (0xFF << 24) | (b << 16) | (g << 8) | r;
    }
  }

  _memPreset(data) {
    if ((data[1] & 0x0F) === 0) { this._bgColor = data[0] & 0x0F; this._pixels.fill(this._bgColor); }
  }

  _borderPreset(data) {
    const c = data[0] & 0x0F;
    for (let y = 0; y < CDG_HEIGHT; y++)
      for (let x = 0; x < CDG_WIDTH; x++)
        if (x < 6 || x >= CDG_WIDTH-6 || y < 12 || y >= CDG_HEIGHT-12)
          this._pixels[y * CDG_WIDTH + x] = c;
  }

  _tile(data, xor) {
    const c0 = data[0]&0x0F, c1 = data[1]&0x0F;
    const tx = (data[3]&0x3F)*6, ty = (data[2]&0x1F)*12;
    if (tx+6 > CDG_WIDTH || ty+12 > CDG_HEIGHT) return;
    for (let r = 0; r < 12; r++) {
      const row = data[4+r] & 0x3F;
      for (let c = 0; c < 6; c++) {
        const bit = (row >> (5-c)) & 1;
        const idx = (ty+r)*CDG_WIDTH + (tx+c);
        this._pixels[idx] = xor ? this._pixels[idx] ^ (bit?c1:c0) : (bit?c1:c0);
      }
    }
  }

  _scroll(data, copy) {
    const h = data[1]&0x3F, v = data[2]&0x3F, color = data[0]&0x0F;
    let dx=0, dy=0;
    const hc=(h>>4)&3, vc=(v>>4)&3;
    if(hc===1) dx=h&7; if(hc===2) dx=-(CDG_WIDTH-(h&7));
    if(vc===1) dy=v&15; if(vc===2) dy=-(CDG_HEIGHT-(v&15));
    const old=this._pixels.slice(), W=CDG_WIDTH, H=CDG_HEIGHT;
    for(let y=0;y<H;y++) for(let x=0;x<W;x++)
      this._pixels[y*W+x]=copy?old[((y-dy+H)%H)*W+((x-dx+W)%W)]:color;
  }

  _paint() {
    const px = this._img.data;
    for (let i = 0; i < this._pixels.length; i++) {
      const rgba = this._clut[this._pixels[i]] || 0, b = i*4;
      px[b]=rgba&0xFF; px[b+1]=(rgba>>8)&0xFF; px[b+2]=(rgba>>16)&0xFF; px[b+3]=255;
    }
    this.ctx.putImageData(this._img, 0, 0);
  }
}
