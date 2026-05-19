import os
import json
import uuid
import subprocess
from flask import Flask, request, jsonify, send_file, Response
from redis import Redis
from rq import Queue
from rq.job import Job, NoSuchJobError

app = Flask(__name__)

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
redis_conn = Redis.from_url(REDIS_URL)
q = Queue('karaoke', connection=redis_conn)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>🎤 Karaoke Maker</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: #0f0f1a;
      color: #e0e0f0;
      min-height: 100vh;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 2rem 1rem;
    }

    .card {
      background: #1a1a2e;
      border: 1px solid #2a2a4a;
      border-radius: 1.25rem;
      padding: 2.5rem;
      width: 100%;
      max-width: 600px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.5);
    }

    h1 {
      font-size: 1.8rem;
      font-weight: 700;
      margin-bottom: 0.35rem;
      background: linear-gradient(135deg, #a78bfa, #60a5fa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .subtitle { color: #888; font-size: 0.9rem; margin-bottom: 2rem; }

    label {
      display: block;
      font-size: 0.8rem;
      font-weight: 600;
      color: #aaa;
      margin-bottom: 0.5rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .search-row {
      display: flex;
      gap: 0.5rem;
      margin-bottom: 1rem;
    }

    input[type="text"] {
      flex: 1;
      padding: 0.75rem 1rem;
      border-radius: 0.6rem;
      border: 1px solid #2a2a4a;
      background: #0f0f1a;
      color: #e0e0f0;
      font-size: 0.95rem;
      outline: none;
      transition: border-color 0.2s;
    }
    input[type="text"]:focus { border-color: #a78bfa; }

    .btn {
      padding: 0.75rem 1.25rem;
      border-radius: 0.6rem;
      border: none;
      background: linear-gradient(135deg, #7c3aed, #2563eb);
      color: #fff;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.2s;
      white-space: nowrap;
    }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn:not(:disabled):hover { opacity: 0.85; }

    /* Search results */
    #results { display: none; margin-top: 0.5rem; }

    .result-item {
      display: flex;
      align-items: center;
      gap: 0.85rem;
      padding: 0.75rem;
      border-radius: 0.75rem;
      border: 1px solid #2a2a4a;
      margin-bottom: 0.5rem;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
    }
    .result-item:hover { background: #252545; border-color: #a78bfa; }

    .result-item img {
      width: 80px;
      height: 52px;
      object-fit: cover;
      border-radius: 0.4rem;
      flex-shrink: 0;
      background: #111;
    }

    .result-info { flex: 1; min-width: 0; }
    .result-title {
      font-size: 0.9rem;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 0.2rem;
    }
    .result-meta { font-size: 0.78rem; color: #888; }

    .result-btn {
      flex-shrink: 0;
      padding: 0.4rem 0.9rem;
      border-radius: 0.5rem;
      border: none;
      background: #7c3aed;
      color: #fff;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
    }
    .result-btn:hover { background: #6d28d9; }

    /* Status */
    #status-box {
      margin-top: 1.25rem;
      padding: 1rem 1.25rem;
      border-radius: 0.75rem;
      font-size: 0.9rem;
      display: none;
    }
    .status-waiting  { background: #1e1e3a; border: 1px solid #3a3a6a; color: #aaa; }
    .status-started  { background: #1a2a1a; border: 1px solid #2a6a2a; color: #6fdb6f; }
    .status-finished { background: #1a2a3a; border: 1px solid #2a5a9a; color: #60a5fa; }
    .status-failed   { background: #2a1a1a; border: 1px solid #6a2a2a; color: #f87171; }

    .now-playing {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem;
      background: #111128;
      border-radius: 0.6rem;
      margin-bottom: 1rem;
      border: 1px solid #2a2a4a;
    }
    .now-playing img { width: 60px; height: 40px; object-fit: cover; border-radius: 0.3rem; }
    .now-playing-title { font-size: 0.88rem; font-weight: 600; }
    .now-playing-channel { font-size: 0.78rem; color: #888; }

    .download-btn {
      display: inline-block;
      margin-top: 0.75rem;
      padding: 0.6rem 1.2rem;
      border-radius: 0.5rem;
      background: #2563eb;
      color: #fff;
      text-decoration: none;
      font-weight: 600;
      font-size: 0.9rem;
    }
    .download-btn:hover { background: #1d4ed8; }

    .spinner {
      display: inline-block;
      width: 14px; height: 14px;
      border: 2px solid #555;
      border-top-color: #a78bfa;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      vertical-align: middle;
      margin-right: 6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .no-results { color: #666; font-size: 0.88rem; text-align: center; padding: 1rem 0; }
  </style>
</head>
<body>
  <div class="card">
    <h1>🎤 Karaoke Maker</h1>
    <p class="subtitle">Search for a song — we'll strip out the vocals.</p>

    <label for="search-input">Search YouTube</label>
    <div class="search-row">
      <input type="text" id="search-input" placeholder="e.g. Bohemian Rhapsody Queen"
             onkeydown="if(event.key==='Enter') doSearch()"/>
      <button class="btn" id="search-btn" onclick="doSearch()">Search</button>
    </div>

    <div id="results"></div>
    <div id="status-box"></div>
  </div>

  <script>
    let pollInterval = null;
    let currentJob = null;

    async function doSearch() {
      const q = document.getElementById('search-input').value.trim();
      if (!q) return;

      const btn = document.getElementById('search-btn');
      btn.disabled = true;
      btn.textContent = 'Searching…';

      const resultsEl = document.getElementById('results');
      resultsEl.style.display = 'block';
      resultsEl.innerHTML = '<div class="no-results"><span class="spinner"></span> Searching YouTube…</div>';

      try {
        const res = await fetch('/search?q=' + encodeURIComponent(q));
        const videos = await res.json();

        if (!videos.length) {
          resultsEl.innerHTML = '<div class="no-results">No results found.</div>';
        } else {
          resultsEl.innerHTML = videos.map(v => `
            <div class="result-item" onclick="pickVideo('${v.id}', '${esc(v.url)}', '${esc(v.title)}', '${esc(v.channel)}', '${esc(v.thumbnail)}', '${esc(v.duration)}')">
              <img src="${v.thumbnail}" alt="" loading="lazy"/>
              <div class="result-info">
                <div class="result-title" title="${esc(v.title)}">${v.title}</div>
                <div class="result-meta">${v.channel} · ${v.duration}</div>
              </div>
              <button class="result-btn">Make Karaoke</button>
            </div>
          `).join('');
        }
      } catch(e) {
        resultsEl.innerHTML = '<div class="no-results">Search failed. Try again.</div>';
      }

      btn.disabled = false;
      btn.textContent = 'Search';
    }

    function esc(s) {
      return String(s || '').replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;');
    }

    function pickVideo(id, url, title, channel, thumbnail, duration) {
      // Hide results, show now-playing + status
      document.getElementById('results').style.display = 'none';

      showStatus('waiting',
        `<div class="now-playing">
          <img src="${thumbnail}" alt=""/>
          <div>
            <div class="now-playing-title">${title}</div>
            <div class="now-playing-channel">${channel} · ${duration}</div>
          </div>
        </div>
        <span class="spinner"></span> Submitting job…`
      );

      submitJob(url);
    }

    async function submitJob(url) {
      try {
        const res = await fetch('/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        currentJob = data.job_id;
        pollStatus(data.job_id);
      } catch(err) {
        showStatus('failed', '❌ ' + err.message);
      }
    }

    function pollStatus(jobId) {
      clearInterval(pollInterval);
      pollInterval = setInterval(async () => {
        try {
          const res = await fetch('/status/' + jobId);
          const data = await res.json();

          const box = document.getElementById('status-box');
          const nowPlaying = box.querySelector('.now-playing')?.outerHTML || '';

          if (data.status === 'queued' || data.status === 'deferred') {
            showStatus('waiting', nowPlaying + '<span class="spinner"></span> Queued — waiting for worker…');
          } else if (data.status === 'started') {
            showStatus('started', nowPlaying + '<span class="spinner"></span> Processing… this can take a few minutes.');
          } else if (data.status === 'finished') {
            clearInterval(pollInterval);
            showStatus('finished',
              nowPlaying +
              '✅ Done! Your karaoke track is ready.<br><br>' +
              '<a class="download-btn" href="/download/' + jobId + '">⬇ Download Karaoke (WAV)</a>' +
              '&nbsp;&nbsp;<button class="btn" style="width:auto;margin-top:0" onclick="reset()">Search again</button>'
            );
          } else if (data.status === 'failed') {
            clearInterval(pollInterval);
            showStatus('failed',
              nowPlaying +
              '❌ Job failed. Please try a different song.<br><br>' +
              '<button class="btn" style="width:auto;margin-top:0.5rem" onclick="reset()">Try again</button>'
            );
          }
        } catch(e) { /* network blip — keep polling */ }
      }, 3000);
    }

    function showStatus(type, html) {
      const box = document.getElementById('status-box');
      box.className = 'status-box status-' + type;
      box.style.display = 'block';
      box.innerHTML = html;
    }

    function reset() {
      clearInterval(pollInterval);
      document.getElementById('status-box').style.display = 'none';
      document.getElementById('results').style.display = 'none';
      document.getElementById('search-input').value = '';
      document.getElementById('search-input').focus();
    }
  </script>
</body>
</html>
"""

# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return Response(HTML, mimetype='text/html')


@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])

    result = subprocess.run(
        [
            'yt-dlp',
            f'ytsearch8:{query}',
            '--dump-json',
            '--flat-playlist',
            '--no-download',
            '--js-runtimes', 'nodejs',
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    videos = []
    for line in result.stdout.strip().splitlines():
        try:
            d = json.loads(line)
            vid_id = d.get('id', '')
            thumbnail = (
                d.get('thumbnail')
                or (d.get('thumbnails') or [{}])[-1].get('url', '')
                or (f'https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg' if vid_id else '')
            )
            videos.append({
                'id':        vid_id,
                'url':       d.get('webpage_url') or d.get('url') or f"https://www.youtube.com/watch?v={vid_id}",
                'title':     d.get('title', 'Unknown'),
                'channel':   d.get('uploader') or d.get('channel') or d.get('channel_id', ''),
                'thumbnail': thumbnail,
                'duration':  d.get('duration_string') or '',
            })
        except Exception:
            continue

    return jsonify(videos)


@app.route('/search/debug')
def search_debug():
    """Temporary debug endpoint — remove after fixing search."""
    query = request.args.get('q', 'linkin park').strip()
    result = subprocess.run(
        ['yt-dlp', f'ytsearch3:{query}', '--dump-json', '--flat-playlist',
         '--no-download', '--js-runtimes', 'nodejs'],
        capture_output=True, text=True, timeout=60,
    )
    return jsonify({
        'returncode': result.returncode,
        'stdout_lines': result.stdout.strip().splitlines()[:3],
        'stderr': result.stderr[-2000:] if result.stderr else '',
    })


@app.route('/submit', methods=['POST'])
def submit():
    data = request.get_json(force=True)
    url = (data or {}).get('url', '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    job_id = str(uuid.uuid4())
    q.enqueue(
        'tasks.process_karaoke',
        job_id,
        url,
        job_id=job_id,
        job_timeout=900,
    )
    return jsonify({'job_id': job_id})


@app.route('/status/<job_id>')
def status(job_id):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        return jsonify({'status': 'not_found'}), 404

    payload = {'status': job.get_status()}
    if job.is_finished:
        payload['result'] = job.result
    if job.is_failed:
        payload['error'] = str(job.exc_info)
    return jsonify(payload)


@app.route('/download/<job_id>')
def download(job_id):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        return jsonify({'error': 'Job not found'}), 404

    if not job.is_finished:
        return jsonify({'error': 'Job not finished yet'}), 400

    accompaniment = (job.result or {}).get('accompaniment_path', '')
    if not accompaniment or not os.path.exists(accompaniment):
        return jsonify({'error': 'Output file not found'}), 404

    return send_file(accompaniment, as_attachment=True, download_name='karaoke.wav')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
