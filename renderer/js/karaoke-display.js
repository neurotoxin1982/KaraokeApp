/**
 * KaraokeDisplay — single shared rendering layer for all three customer-facing
 * surfaces: the Manager's embedded preview, the Electron popout window, and
 * the wireless browser screen.
 *
 * Ownership boundary:
 *   - THIS module owns: DOM structure for every screen, the fixed 1920x1080
 *     stage + letterbox/pillarbox fit, the safe-area margin, and turning a
 *     single `state` object into the right screen / QR / banner / next-up /
 *     text on screen.
 *   - It does NOT own: business logic (which screen is active right now,
 *     queue/playback orchestration), networking (IPC / WebSocket), or driving
 *     CDG/video playback -- those stay in each host page. A host calls
 *     `KaraokeDisplay.applyState(state)` whenever something changes, and
 *     drives playback directly via `KaraokeDisplay.els.canvas` / `.video`.
 *
 * Usage (identical in every host):
 *   <script src="js/karaoke-display.js"></script>   (or /js/... over HTTP)
 *   <div id="display-root"></div>
 *   <script>
 *     KaraokeDisplay.mount(document.getElementById('display-root'));
 *     KaraokeDisplay.applyState({ screen: 'idle', idle: {...}, appearance: {...} });
 *   </script>
 */
(function (global) {
  'use strict';

  const STAGE_W = 1920, STAGE_H = 1080;
  const QR_SIZE_SCALE   = [0, 0.65, 0.85, 1, 1.3, 1.65]; // index 1-5 -> XS..XL
  const BANNER_SCALE    = [0, 0.55, 0.75, 1.00, 1.35, 1.75];
  const QR_POS_DEFAULT  = { x: 50, y: 85 };

  // ── DOM template ──────────────────────────────────────────────────────────
  // Every screen is a direct child of #kd-stage, so everything positions
  // against the fixed 1920x1080 stage, never the physical screen. Elements
  // that must stay clear of overscan (QR, text, next-up, countdown) live
  // inside a nested .kd-safe-area (inset:5%); backgrounds/media/banner bars
  // are full-bleed direct children, same convention as broadcast lower-thirds.
  const TEMPLATE = `
    <div id="kd-viewport">
      <div id="kd-stage">

        <div id="kd-screen-karaoke" class="kd-screen">
          <div id="kd-canvas-wrap"><canvas id="kd-cdg-canvas"></canvas></div>
          <video id="kd-video" class="kd-fill" playsinline></video>
          <div class="kd-safe-area">
            <div id="kd-karaoke-qr-a" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
            <div id="kd-karaoke-qr-b" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
          </div>
          <div id="kd-info-bar">
            <div id="kd-info-title"></div>
            <div id="kd-info-artist"></div>
            <div id="kd-info-singer"></div>
          </div>
          <!-- This IS the "playback banner" -- one slot, driven by state.playbackBanner
               (see applyState). Kept as the karaoke screen's own ticker rather than a
               separate top-level element so there's exactly one banner concept, not two. -->
          <div id="kd-karaoke-ticker-wrap" class="kd-ticker-wrap"><div id="kd-karaoke-ticker" class="kd-ticker"></div></div>
        </div>

        <div id="kd-screen-idle" class="kd-screen">
          <img id="kd-idle-bg" class="kd-fill" alt="">
          <div id="kd-idle-default"><div class="kd-logo">&#127908;</div><p>No song playing</p></div>
          <div id="kd-idle-ticker-wrap" class="kd-ticker-wrap"><div id="kd-idle-ticker" class="kd-ticker"></div></div>
          <div class="kd-safe-area">
            <p id="kd-idle-text"></p>
            <div id="kd-idle-next-up" class="kd-next-up"></div>
            <div id="kd-idle-qr-a" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
            <div id="kd-idle-qr-b" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
          </div>
        </div>

        <div id="kd-screen-ad" class="kd-screen">
          <img id="kd-ad-picture" class="kd-fill" alt="">
          <video id="kd-ad-video" class="kd-fill" muted loop></video>
          <div class="kd-safe-area">
            <div id="kd-ad-next-up" class="kd-next-up"></div>
            <div id="kd-ad-qr-a" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
            <div id="kd-ad-qr-b" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
          </div>
        </div>

        <div id="kd-screen-thankyou" class="kd-screen kd-center">
          <div id="kd-thanks-inner"></div>
          <div id="kd-thanks-ticker-wrap" class="kd-ticker-wrap"><div id="kd-thanks-ticker" class="kd-ticker"></div></div>
          <div class="kd-safe-area">
            <div id="kd-thanks-qr-a" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
            <div id="kd-thanks-qr-b" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
          </div>
        </div>

        <div id="kd-screen-betweensongs" class="kd-screen kd-column">
          <div id="kd-between-media-wrap">
            <img id="kd-between-venue" class="kd-fill" alt="">
            <div class="kd-safe-area">
              <div id="kd-between-countdown">
                <div class="kd-countdown-label">NEXT SINGER</div>
                <div id="kd-between-next-singer"></div>
                <div class="kd-countdown-label">GET READY IN</div>
                <div id="kd-between-countdown-num"></div>
              </div>
              <div id="kd-between-next-up" class="kd-next-up"></div>
              <div id="kd-between-qr-a" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
              <div id="kd-between-qr-b" class="kd-qr-box"><img class="kd-qr-img" alt="QR"></div>
            </div>
          </div>
          <div id="kd-between-ticker-wrap" class="kd-ticker-wrap" style="position:static"><div id="kd-between-ticker" class="kd-ticker"></div></div>
        </div>

      </div>
    </div>
  `;

  const STYLE = `
    #kd-viewport { position: absolute; inset: 0; background: #000; overflow: hidden; }
    #kd-stage {
      position: absolute; top: 0; left: 0;
      width: ${STAGE_W}px; height: ${STAGE_H}px;
      transform-origin: top left;
      overflow: hidden; background: #000;
      font-family: system-ui, -apple-system, sans-serif;
    }
    #kd-stage * { box-sizing: border-box; }
    .kd-screen { position: absolute; inset: 0; display: none; background: #000; overflow: hidden; }
    .kd-screen.kd-visible { display: flex; flex-direction: column; }
    .kd-screen.kd-center.kd-visible { align-items: center; justify-content: center; }
    .kd-fill { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; display: none; }
    .kd-fill.kd-shown { display: block; }
    #kd-ad-picture.kd-shown, #kd-ad-video.kd-shown, #kd-between-venue.kd-shown { object-fit: cover; }

    /* 5% overscan margin -- inset:5% resolves top/bottom against height and
       left/right against width automatically, so this is a true 5% per axis. */
    .kd-safe-area { position: absolute; inset: 5%; z-index: 6; }

    .kd-qr-box { position: absolute; display: none; flex-direction: column; align-items: center; padding: 6px; gap: 6px; z-index: 10; }
    .kd-qr-box.kd-shown { display: flex; }
    .kd-qr-img { width: auto; height: 90px; aspect-ratio: 1/1; border-radius: 10px; image-rendering: pixelated; }

    .kd-ticker-wrap {
      display: none; position: absolute; bottom: 0; left: 0; right: 0; z-index: 8;
      background: rgba(124,58,237,0.92); padding: 28px 0; overflow: hidden;
    }
    .kd-ticker-wrap.kd-shown { display: block; }
    .kd-ticker { white-space: nowrap; display: inline-block; font-size: 2.8rem; font-weight: 700; color: #fff; animation: kd-scroll 18s linear infinite; }
    @keyframes kd-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }

    #kd-canvas-wrap { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
    #kd-cdg-canvas { image-rendering: pixelated; image-rendering: crisp-edges; display: block; }

    #kd-info-bar {
      display: none; position: absolute; bottom: 0; left: 0; right: 0; z-index: 7;
      background: linear-gradient(transparent, rgba(0,0,0,.85)); padding: 24px 96px 20px;
      transition: bottom .15s ease-out;
    }
    #kd-info-bar.kd-shown { display: block; }
    #kd-info-title  { font-size: 2rem; font-weight: 700; color: #fff; line-height: 1.2; }
    #kd-info-artist { font-size: 1.2rem; color: #a78bfa; margin-top: 4px; }
    #kd-info-singer { font-size: 1rem; color: #9ca3af; margin-top: 4px; }

    #kd-screen-idle .kd-logo { font-size: 5rem; }
    #kd-screen-idle p { font-size: 1.1rem; color: #4b5563; text-align: center; margin-top: 8px; }
    #kd-idle-default { display: flex; flex-direction: column; align-items: center; justify-content: center; position: absolute; inset: 0; }
    #kd-idle-default.kd-hidden { display: none; }
    #kd-idle-text {
      position: absolute; top: 0; left: 0; right: 0; margin: 0; padding: 28px 40px 40px;
      font-weight: 700; color: #fff; text-shadow: 0 2px 8px rgba(0,0,0,.8); text-align: center;
      background: linear-gradient(180deg, rgba(8,6,14,.6), transparent); display: none;
    }
    #kd-idle-text.kd-shown { display: block; }

    .kd-next-up {
      display: none; position: absolute; left: 0; top: 0; z-index: 5;
      background: rgba(0,0,0,.72); border-radius: 14px; padding: 14px 16px;
      backdrop-filter: blur(4px); min-width: 200px; max-width: 300px;
    }
    .kd-next-up.kd-shown { display: block; }

    #kd-between-media-wrap { flex: 1; display: flex; align-items: stretch; justify-content: center; overflow: hidden; min-height: 0; position: relative; }
    #kd-between-countdown {
      position: absolute; top: 0; right: 0; z-index: 6;
      background: rgba(0,0,0,.72); border-radius: 14px; padding: 10px 18px; text-align: center; backdrop-filter: blur(4px);
      display: none;
    }
    #kd-between-countdown.kd-shown { display: block; }
    .kd-countdown-label { font-size: .55rem; color: #6b7280; letter-spacing: .08em; margin-bottom: 3px; text-transform: uppercase; }
    #kd-between-next-singer { font-size: 1.3rem; font-weight: 700; color: #fff; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
    #kd-between-countdown-num { font-size: 2.2rem; font-weight: 700; color: #fff; font-variant-numeric: tabular-nums; line-height: 1; }
    #kd-karaoke-ticker-wrap { z-index: 19; }

    @keyframes kd-th-bounce   { 0%,100%{transform:translateY(0) scale(1)} 50%{transform:translateY(-15px) scale(1.12)} }
    @keyframes kd-th-pulse    { 0%,100%{transform:scale(1);opacity:1} 50%{transform:scale(.88);opacity:.75} }
    @keyframes kd-th-float-up { 0%{transform:translateY(0);opacity:1} 100%{transform:translateY(-160px);opacity:0} }
    @keyframes kd-th-fall     { 0%{transform:translateY(0) rotate(0deg)} 100%{transform:translateY(1400px) rotate(720deg)} }
    @keyframes kd-th-spin     { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
    @keyframes kd-th-radiate  { 0%{transform:translate(-50%,-50%) rotate(var(--a,0deg)) translateY(0);opacity:1} 100%{transform:translate(-50%,-50%) rotate(var(--a,0deg)) translateY(-140px);opacity:0} }
    @keyframes kd-th-flame    { 0%{transform:scaleY(1) rotate(-4deg)} 100%{transform:scaleY(1.1) translateY(-12px) rotate(4deg)} }
    @keyframes kd-th-flame-big{ 0%{transform:scale(1) rotate(-8deg)} 100%{transform:scale(1.12) rotate(8deg)} }
    @keyframes kd-th-glow-fire{ 0%{text-shadow:0 0 20px #f60,0 0 40px #f40} 100%{text-shadow:0 0 50px #f60,0 0 90px #f00} }
    @keyframes kd-th-twinkle  { 0%,100%{opacity:.15} 50%{opacity:1} }
    @keyframes kd-th-rocket   { 0%,100%{transform:translateY(0) rotate(5deg)} 50%{transform:translateY(-20px) rotate(-5deg)} }
    @keyframes kd-th-rainbow  { 0%{color:#f44} 16%{color:#f80} 33%{color:#fd0} 50%{color:#4f8} 66%{color:#48f} 83%{color:#c4f} 100%{color:#f44} }
    @keyframes kd-th-shimmer  { from{background-position:0% 50%} to{background-position:200% 50%} }
  `;

  const els = {};
  let styleInjected = false;

  // ── mount ─────────────────────────────────────────────────────────────────
  function mount(root) {
    if (!styleInjected) {
      const styleEl = document.createElement('style');
      styleEl.textContent = STYLE;
      document.head.appendChild(styleEl);
      styleInjected = true;
    }
    root.innerHTML = TEMPLATE;

    els.viewport = root.querySelector('#kd-viewport');
    els.stage    = root.querySelector('#kd-stage');
    els.canvas   = root.querySelector('#kd-cdg-canvas');
    els.canvasWrap = root.querySelector('#kd-canvas-wrap');
    els.video    = root.querySelector('#kd-video');
    [
      'kd-screen-karaoke', 'kd-screen-idle', 'kd-screen-ad', 'kd-screen-thankyou', 'kd-screen-betweensongs',
    ].forEach(id => { els[camel(id)] = root.querySelector('#' + id); });

    _observeFit();
    _resizeCdgCanvas();
    return els;
  }

  function camel(id) {
    return id.replace(/^kd-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  }

  // ── Fixed-stage fit (letterbox/pillarbox) ────────────────────────────────
  // Watching the viewport ELEMENT (not window.resize) is what makes this
  // identical whether it's an embedded preview panel or a full-screen window.
  function _fitStage() {
    const vw = els.viewport.clientWidth, vh = els.viewport.clientHeight;
    if (!vw || !vh) return;
    const scale   = Math.min(vw / STAGE_W, vh / STAGE_H);
    const offsetX = (vw - STAGE_W * scale) / 2;
    const offsetY = (vh - STAGE_H * scale) / 2;
    els.stage.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${scale})`;
  }
  function _observeFit() {
    new ResizeObserver(_fitStage).observe(els.viewport);
    _fitStage();
  }
  // Fitted against the fixed stage size, not the real window -- the stage's
  // own transform handles all responsiveness uniformly, so this runs once.
  function _resizeCdgCanvas() {
    const scale = Math.min(STAGE_W / 300, STAGE_H / 216);
    els.canvas.style.width  = (300 * scale) + 'px';
    els.canvas.style.height = (216 * scale) + 'px';
  }

  // ── Shared rendering helpers ──────────────────────────────────────────────
  function _hexToRgba(hex, opacityPct) {
    const h = (hex || '#7c3aed').replace('#', '');
    const r = parseInt(h.substring(0, 2), 16) || 0;
    const g = parseInt(h.substring(2, 4), 16) || 0;
    const b = parseInt(h.substring(4, 6), 16) || 0;
    const a = Math.max(0, Math.min(100, parseInt(opacityPct, 10) ?? 92)) / 100;
    return `rgba(${r},${g},${b},${a})`;
  }

  function _qrBoxStyleAt(x, y) {
    return `left:${x}%;top:${y}%;transform:translate(-50%,-50%);`;
  }
  function _qrSizeStyle(sizeLevel) {
    // Fixed stage-px, not vw -- vw would bypass the stage's own scale
    // transform and size inconsistently once the stage is scaled down/up.
    const scale = QR_SIZE_SCALE[sizeLevel] || 1;
    return `height:${Math.round(90 * scale)}px;`;
  }
  // boxA/boxB: {box, img} element pairs, cached once per screen (see _qrPairs).
  function _showQrIn(pair, qr) {
    if (!qr || !qr.dataUrl) { _hideQrIn(pair); return; }
    const x = qr.x ?? QR_POS_DEFAULT.x, y = qr.y ?? QR_POS_DEFAULT.y;
    const sizeStyle = _qrSizeStyle(qr.size);
    pair.a.box.style.cssText = 'z-index:10;' + _qrBoxStyleAt(x, y);
    pair.a.img.style.cssText = sizeStyle;
    pair.a.img.src = qr.dataUrl;
    pair.a.box.classList.add('kd-shown');
    if (qr.both) {
      pair.b.box.style.cssText = 'z-index:10;' + _qrBoxStyleAt(100 - x, y);
      pair.b.img.style.cssText = sizeStyle;
      pair.b.img.src = qr.dataUrl;
      pair.b.box.classList.add('kd-shown');
    } else {
      pair.b.box.classList.remove('kd-shown');
    }
  }
  function _hideQrIn(pair) {
    pair.a.box.classList.remove('kd-shown');
    pair.b.box.classList.remove('kd-shown');
  }

  function _setupScrollTicker(el, text, singleAnimSec) {
    const gap = '     ';
    el.textContent = text + gap;
    const singleW    = el.scrollWidth;
    const containerW = el.parentElement ? el.parentElement.offsetWidth : STAGE_W;
    const effectiveW = containerW > 0 ? containerW : STAGE_W;
    const halfReps   = singleW > 0 ? Math.min(20, Math.max(1, Math.ceil(effectiveW / singleW) + 1)) : 4;
    const half       = Array(halfReps).fill(text).join(gap);
    el.textContent   = half + gap + half;
    el.style.animationDuration = (singleAnimSec * halfReps) + 's';
  }
  function _renderTicker(wrapEl, tickerEl, text, animSec, appearance) {
    const scale = BANNER_SCALE[parseInt(appearance?.scale ?? 3, 10)] ?? 1;
    tickerEl.style.fontSize = (2.8 * scale).toFixed(2) + 'rem';
    tickerEl.style.color    = appearance?.color || '#fff';
    wrapEl.style.padding    = Math.round(28 * scale) + 'px 0';
    wrapEl.style.background = appearance?.bgColor || _hexToRgba(appearance?.bgHex, appearance?.bgOpacity);
    _setupScrollTicker(tickerEl, text || '', animSec || 18);
  }

  function _renderNextUp(list, el, scale) {
    if (!list || !list.length) { el.classList.remove('kd-shown'); return; }
    const s = scale || 1;
    const esc = str => (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const fmtW = sec => sec < 60 ? `&asymp; ${Math.round(sec)}s` : `&asymp; ${Math.floor(sec/60)}:${String(Math.floor(sec%60)).padStart(2,'0')}`;
    const r  = base => (base * s).toFixed(2) + 'rem';
    const px = base => Math.round(base * s) + 'px';
    el.style.minWidth = px(200);
    el.style.padding  = `${px(14)} ${px(16)}`;
    el.innerHTML =
      `<div style="font-size:${r(0.55)};color:#a78bfa;letter-spacing:.12em;font-weight:700;margin-bottom:${px(10)};text-transform:uppercase">Up Next</div>` +
      list.map((e, i) =>
        `<div style="margin-bottom:${i < list.length - 1 ? px(9) : '0'}">` +
          `<div style="display:flex;align-items:baseline;gap:${px(5)}">` +
            `<span style="font-size:${r(0.65)};font-weight:700;color:${e.isNext ? '#a78bfa' : '#6b7280'}">${e.isNext ? '&#9654;' : (i + 1)}</span>` +
            `<span style="font-size:${e.isNext ? r(0.88) : r(0.78)};font-weight:700;color:#fff;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(e.singer)}</span>` +
            `<span style="font-size:${r(0.6)};color:#9ca3af;white-space:nowrap">${fmtW(e.waitSec)}</span>` +
          `</div>` +
        `</div>`
      ).join('');
    el.classList.add('kd-shown');
  }

  // 10 celebration variants for the Thank You screen. Sizes are fixed
  // stage-px (not vw) for the same reason as the QR helper above.
  function _getThanksHTML(singer, variant) {
    const esc = s => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const r = (a, b) => (a + Math.random() * (b - a)).toFixed(2);
    const nm = singer ? `<div style="font-size:2.8rem;color:#a78bfa;font-weight:700;margin-top:4px;max-width:82%;text-overflow:ellipsis;white-space:nowrap;overflow:hidden">${esc(singer)}</div>` : '';
    const C = [
      { icon:`<div style="font-size:9rem;animation:kd-th-bounce .6s ease-in-out infinite">&#128079;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;color:#fff;animation:kd-th-bounce .7s .1s ease-in-out infinite">Thank You!</div>`, ptcl:Array.from({length:8},(_,i)=>`<div style="position:absolute;left:50%;top:50%;font-size:2rem;--a:${i*45}deg;animation:kd-th-radiate 1.2s ${(i*.08).toFixed(2)}s ease-out infinite">&#128079;</div>`).join('') },
      { icon:`<div style="font-size:9rem;animation:kd-th-bounce .75s ease-in-out infinite">&#127881;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;color:#fff">Thank You!</div>`, ptcl:Array.from({length:28},(_,i)=>{const c=['#f55','#f90','#fd2','#4d4','#48f','#b4f','#f5c','#fff'][i%8];return`<div style="position:absolute;top:-4%;left:${r(1,99)}%;width:${r(8,15)}px;height:${r(8,15)}px;background:${c};border-radius:${i%3?'50%':'3px'};animation:kd-th-fall ${r(1.5,2.8)}s ${r(0,1.8)}s linear infinite"></div>`}).join('') },
      { icon:`<div style="font-size:9rem;animation:kd-th-spin 3s linear infinite">&#11088;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;background:linear-gradient(90deg,#ffd700,#fff,#ffd700);background-size:200%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;animation:kd-th-shimmer 2s linear infinite">Thank You!</div>`, ptcl:Array.from({length:12},(_,i)=>{const e=['&#11088;','&#127775;','&#10024;','&#128171;'][i%4];return`<div style="position:absolute;left:50%;top:50%;font-size:1.8rem;--a:${i*30}deg;animation:kd-th-radiate 1.6s ${(i*.1).toFixed(1)}s ease-out infinite">${e}</div>`}).join('') },
      { bg:`<div style="position:absolute;inset:0;background:radial-gradient(circle at 50% 100%,rgba(255,70,0,.4),transparent 65%);pointer-events:none"></div>`, icon:`<div style="font-size:9rem;animation:kd-th-flame-big .4s ease-in-out infinite alternate">&#128293;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;color:#ff6600;animation:kd-th-glow-fire .9s ease-in-out infinite alternate">Thank You!</div>`, ptcl:Array.from({length:12},(_,i)=>`<div style="position:absolute;bottom:0;left:${(i*8.5+parseFloat(r(0,4))).toFixed(1)}%;font-size:${r(1.8,3.5)}rem;animation:kd-th-flame ${r(.4,.8)}s ${r(0,.4)}s ease-in-out infinite alternate">&#128293;</div>`).join('') },
      { icon:`<div style="font-size:9rem;animation:kd-th-bounce .8s ease-in-out infinite">&#127925;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;color:#a78bfa">Thank You!</div>`, ptcl:Array.from({length:14},(_,i)=>{const e=['&#127925;','&#127926;','&#127928;','&#127929;','&#127930;','&#127931;','&#127932;'][i%7];return`<div style="position:absolute;left:${r(3,97)}%;bottom:${r(5,30)}%;font-size:${r(1.5,2.8)}rem;animation:kd-th-float-up ${r(1.5,3)}s ${r(0,2)}s ease-out infinite">${e}</div>`}).join('') },
      { icon:`<div style="font-size:9rem;animation:kd-th-bounce .6s ease-in-out infinite">&#129395;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;color:#fff">Thank You!</div>`, ptcl:Array.from({length:12},(_,i)=>`<div style="position:absolute;bottom:-8%;left:${r(3,97)}%;font-size:${r(2,4.5)}rem;animation:kd-th-float-up ${r(2,4)}s ${r(0,2.5)}s ease-in infinite">&#127880;</div>`).join('') },
      { bg:`<div style="position:absolute;inset:0;background:radial-gradient(circle at center,rgba(255,215,0,.13),transparent 70%);pointer-events:none"></div>`, icon:`<div style="font-size:9rem;animation:kd-th-bounce 1.1s ease-in-out infinite;position:relative;z-index:2">&#128081;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;background:linear-gradient(90deg,#ffd700,#fff8dc,#ffd700);background-size:200%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;animation:kd-th-shimmer 2s linear infinite">Thank You!</div>`, ptcl:Array.from({length:10},(_,i)=>`<div style="position:absolute;left:50%;top:45%;font-size:1.6rem;transform:translate(-50%,-50%) rotate(${i*36}deg) translateY(-${r(70,120)}px);animation:kd-th-pulse ${r(.8,1.2)}s ${(i*.1).toFixed(1)}s ease-in-out infinite">&#10024;</div>`).join('') },
      { bg:`<div style="position:absolute;inset:0;background:radial-gradient(circle at center,rgba(190,0,80,.25),transparent);pointer-events:none"></div>`, icon:`<div style="font-size:9rem;animation:kd-th-pulse .6s ease-in-out infinite">&#10084;&#65039;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;color:#ff6899">Thank You!</div>`, ptcl:Array.from({length:16},(_,i)=>{const e=['&#10084;&#65039;','&#128156;','&#128153;','&#128154;','&#128155;','&#129505;','&#129394;','&#129293;'][i%8];return`<div style="position:absolute;top:-8%;left:${r(1,99)}%;font-size:${r(1.5,3)}rem;animation:kd-th-fall ${r(1.8,3.5)}s ${r(0,2.5)}s ease-in infinite">${e}</div>`}).join('') },
      { bg:`<div style="position:absolute;inset:0;background:radial-gradient(circle at center,rgba(0,20,80,.7),#000 80%);pointer-events:none"></div>`, icon:`<div style="font-size:9rem;animation:kd-th-rocket 1.5s ease-in-out infinite">&#128640;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;color:#88ccff;text-shadow:0 0 20px #4488ff">Thank You!</div>`, ptcl:Array.from({length:35},(_,i)=>`<div style="position:absolute;left:${r(0,100)}%;top:${r(0,100)}%;width:${r(2,4)}px;height:${r(2,4)}px;background:#fff;border-radius:50%;animation:kd-th-twinkle ${r(1,3)}s ${r(0,2)}s ease-in-out infinite"></div>`).join('') },
      { bg:`<div style="position:absolute;inset:0;background:radial-gradient(circle at center,rgba(50,0,100,.85),#000 80%);pointer-events:none"></div>`, icon:`<div style="font-size:9rem;animation:kd-th-spin 4s linear infinite">&#128191;</div>`, title:`<div style="font-size:3.5rem;font-weight:800;animation:kd-th-rainbow 1.5s linear infinite">Thank You!</div>`, ptcl:Array.from({length:6},(_,i)=>{const c=['#f04','#f80','#fd0','#0f8','#08f','#c0f'][i];return`<div style="position:absolute;left:50%;top:50%;width:3px;height:48%;transform-origin:0 0;transform:rotate(${i*60}deg);background:linear-gradient(${c},transparent);animation:kd-th-spin 4s ${(i*.6).toFixed(1)}s linear infinite;opacity:.55"></div>`}).join('') },
    ];
    const cfg = C[variant % C.length];
    return `${cfg.bg||''}<div style="position:absolute;inset:0;overflow:hidden;pointer-events:none">${cfg.ptcl}</div><div style="position:relative;z-index:2;display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center;padding:16px;width:100%">${cfg.icon}${cfg.title}${nm}</div>`;
  }

  // ── QR element-pair cache (built lazily on first mount) ──────────────────
  function _qrPair(prefix) {
    const q = sel => els.stage.querySelector(sel);
    return {
      a: { box: q(`#${prefix}-a`), img: q(`#${prefix}-a .kd-qr-img`) },
      b: { box: q(`#${prefix}-b`), img: q(`#${prefix}-b .kd-qr-img`) },
    };
  }

  // ── Screen switching ──────────────────────────────────────────────────────
  const SCREEN_IDS = ['karaoke', 'idle', 'ad', 'thankyou', 'betweensongs'];
  function _showOnlyScreen(name) {
    SCREEN_IDS.forEach(s => {
      els[`screen${cap(s)}`]?.classList.toggle('kd-visible', s === name);
    });
  }
  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

  // ── Per-screen renderers ─────────────────────────────────────────────────
  // `playbackBanner` is a sibling of `cfg` (state.karaoke), not nested inside
  // it, since a host typically refreshes it on its own polling cadence -- but
  // applyState is still fully declarative: pass the complete current state
  // every time (including playbackBanner), and whatever's omitted renders as
  // off/empty. There is no partial-patch mode.
  function _renderKaraoke(cfg, appearance, playbackBanner) {
    if (!cfg) return;
    _showQrIn(_qrPair('kd-karaoke-qr'), cfg.showQr ? { ...appearance?.qr, dataUrl: cfg.qrDataUrl } : null);

    const info = els.stage.querySelector('#kd-info-bar');
    if (cfg.songInfo) {
      els.stage.querySelector('#kd-info-title').textContent  = cfg.songInfo.title  || '';
      els.stage.querySelector('#kd-info-artist').textContent = cfg.songInfo.artist || '';
      els.stage.querySelector('#kd-info-singer').textContent = cfg.songInfo.singer ? '♪ ' + cfg.songInfo.singer : '';
      info.classList.add('kd-shown');
    } else {
      info.classList.remove('kd-shown');
    }

    const wrap = els.stage.querySelector('#kd-karaoke-ticker-wrap');
    const ticker = els.stage.querySelector('#kd-karaoke-ticker');
    wrap.classList.toggle('kd-shown', !!playbackBanner?.visible);
    if (playbackBanner?.visible) _renderTicker(wrap, ticker, playbackBanner.text, playbackBanner.animSec, appearance?.banner);

    // Both are bottom-anchored, so once the banner's final visibility above
    // is settled, nudge the info bar to sit just above it instead of the two
    // stacking on top of each other.
    if (info.classList.contains('kd-shown')) {
      info.style.bottom = wrap.classList.contains('kd-shown') ? (wrap.offsetHeight + 8) + 'px' : '0';
    }
  }

  function _renderIdle(cfg, appearance) {
    if (!cfg) return;
    const defaultEl = els.stage.querySelector('#kd-idle-default');
    const textEl    = els.stage.querySelector('#kd-idle-text');
    const wrap      = els.stage.querySelector('#kd-idle-ticker-wrap');
    const ticker    = els.stage.querySelector('#kd-idle-ticker');
    const bgEl      = els.stage.querySelector('#kd-idle-bg');
    const nextUpEl  = els.stage.querySelector('#kd-idle-next-up');

    bgEl.classList.toggle('kd-shown', !!cfg.bgImageSrc);
    if (cfg.bgImageSrc) bgEl.src = cfg.bgImageSrc;

    // Idle only ever shows once the queue is confirmed empty, so there's
    // never a real "up next" to list -- kept for settings-matrix consistency.
    nextUpEl.classList.remove('kd-shown');

    const anyCustom = cfg.showQr || cfg.showBanner || cfg.showText;
    defaultEl.classList.toggle('kd-hidden', anyCustom);
    textEl.classList.toggle('kd-shown', anyCustom && cfg.showText);
    if (anyCustom && cfg.showText) {
      textEl.textContent = cfg.text || '';
      textEl.style.color = cfg.textColor || '#fff';
      textEl.style.fontSize = (cfg.textSize || 1.8) + 'rem';
    }
    wrap.classList.toggle('kd-shown', anyCustom && !!cfg.showBanner);
    if (anyCustom && cfg.showBanner) _renderTicker(wrap, ticker, cfg.bannerText, cfg.animSec, appearance?.banner);

    _showQrIn(_qrPair('kd-idle-qr'), (anyCustom && cfg.showQr) ? { ...appearance?.qr, dataUrl: cfg.qrDataUrl } : null);
  }

  function _renderAd(cfg, appearance) {
    if (!cfg) return;
    const picEl = els.stage.querySelector('#kd-ad-picture');
    const vidEl = els.stage.querySelector('#kd-ad-video');
    const nextUpEl = els.stage.querySelector('#kd-ad-next-up');

    if (cfg.mediaShowing === 'video' && cfg.videoSrc) {
      picEl.classList.remove('kd-shown');
      if (vidEl.dataset.src !== cfg.videoSrc) { vidEl.dataset.src = cfg.videoSrc; vidEl.src = cfg.videoSrc; }
      vidEl.classList.add('kd-shown');
      vidEl.play().catch(() => {});
    } else if (cfg.pictureSrc) {
      vidEl.pause(); vidEl.classList.remove('kd-shown');
      if (picEl.dataset.src !== cfg.pictureSrc) { picEl.dataset.src = cfg.pictureSrc; picEl.src = cfg.pictureSrc; }
      picEl.classList.add('kd-shown');
    }

    // Same "always empty today" situation as idle -- kept for parity.
    if (cfg.showNextUp && cfg.nextUpList?.length) _renderNextUp(cfg.nextUpList, nextUpEl, appearance?.nextUp?.scale);
    else nextUpEl.classList.remove('kd-shown');

    _showQrIn(_qrPair('kd-ad-qr'), cfg.showQr ? { ...appearance?.qr, dataUrl: cfg.qrDataUrl } : null);
  }

  function _renderThankYou(cfg, appearance) {
    if (!cfg) return;
    els.stage.querySelector('#kd-thanks-inner').innerHTML = _getThanksHTML(cfg.prevSinger || '', cfg.variant || 0);

    const wrap = els.stage.querySelector('#kd-thanks-ticker-wrap');
    const ticker = els.stage.querySelector('#kd-thanks-ticker');
    wrap.classList.toggle('kd-shown', !!cfg.showBanner);
    if (cfg.showBanner) _renderTicker(wrap, ticker, cfg.bannerText, cfg.animSec, appearance?.banner);

    _showQrIn(_qrPair('kd-thanks-qr'), cfg.showQr ? { ...appearance?.qr, dataUrl: cfg.qrDataUrl } : null);
  }

  function _renderBetweenSongs(cfg, appearance) {
    if (!cfg) return;
    const venueEl = els.stage.querySelector('#kd-between-venue');
    venueEl.classList.toggle('kd-shown', !!cfg.venueSrc);
    if (cfg.venueSrc) venueEl.src = cfg.venueSrc;

    const wrap = els.stage.querySelector('#kd-between-ticker-wrap');
    const ticker = els.stage.querySelector('#kd-between-ticker');
    wrap.classList.toggle('kd-shown', !!cfg.showBanner);
    if (cfg.showBanner) _renderTicker(wrap, ticker, cfg.bannerText, cfg.animSec, appearance?.banner);

    const cd = els.stage.querySelector('#kd-between-countdown');
    cd.classList.toggle('kd-shown', cfg.countdownSec != null);
    els.stage.querySelector('#kd-between-next-singer').textContent = cfg.nextSinger || '';
    if (cfg.countdownSec != null) _setCountdownText(cfg.countdownSec);

    const nextUpEl = els.stage.querySelector('#kd-between-next-up');
    if (cfg.showNextUp && cfg.nextUpList?.length) _renderNextUp(cfg.nextUpList, nextUpEl, appearance?.nextUp?.scale);
    else nextUpEl.classList.remove('kd-shown');

    _showQrIn(_qrPair('kd-between-qr'), cfg.showQr ? { ...appearance?.qr, dataUrl: cfg.qrDataUrl } : null);
  }

  function _setCountdownText(sec) {
    const el = els.stage.querySelector('#kd-between-countdown-num');
    const s = Math.max(0, Math.round(sec));
    el.textContent = s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s}`;
  }

  // High-frequency countdown tick, separate from applyState so a 1s timer
  // doesn't re-run the full render (and restart the ticker scroll) every tick.
  function tickCountdown(sec) { _setCountdownText(sec); }

  // ── The single reactive entrypoint ───────────────────────────────────────
  function applyState(state) {
    if (!state || !state.screen) return;
    _showOnlyScreen(state.screen);
    switch (state.screen) {
      case 'karaoke':      _renderKaraoke(state.karaoke, state.appearance, state.playbackBanner); break;
      case 'idle':         _renderIdle(state.idle, state.appearance); break;
      case 'ad':            _renderAd(state.ad, state.appearance); break;
      case 'thankyou':      _renderThankYou(state.thankyou, state.appearance); break;
      case 'betweensongs':  _renderBetweenSongs(state.betweensongs, state.appearance); break;
      case 'stopped': break;
    }
  }

  global.KaraokeDisplay = {
    mount, applyState, tickCountdown,
    STAGE_W, STAGE_H,
    els, // exposed so hosts can drive CDG/video playback directly
  };
})(typeof window !== 'undefined' ? window : globalThis);
