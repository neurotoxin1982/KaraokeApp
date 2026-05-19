import os
import uuid
from flask import Flask, request, jsonify, send_file, render_template_string
from redis import Redis
from rq import Queue
from rq.job import Job, NoSuchJobError

app = Flask(__name__)

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
redis_conn = Redis.from_url(REDIS_URL)
q = Queue('karaoke', connection=redis_conn)

# ── Frontend ─────────────────────────────────────────────────────────────────

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>🎤 Karaoke Maker</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: #0f0f1a;
      color: #e0e0f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }

    .card {
      background: #1a1a2e;
      border: 1px solid #2a2a4a;
      border-radius: 1.25rem;
      padding: 2.5rem;
      width: 100%;
      max-width: 540px;
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

    .subtitle {
      color: #888;
      font-size: 0.9rem;
      margin-bottom: 2rem;
    }

    label {
      display: block;
      font-size: 0.85rem;
      font-weight: 600;
      color: #aaa;
      margin-bottom: 0.5rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    input[type="url"] {
      width: 100%;
      padding: 0.75rem 1rem;
      border-radius: 0.6rem;
      border: 1px solid #2a2a4a;
      background: #0f0f1a;
      color: #e0e0f0;
      font-size: 0.95rem;
      outline: none;
      transition: border-color 0.2s;
    }
    input[type="url"]:focus { border-color: #a78bfa; }

    button {
      margin-top: 1rem;
      width: 100%;
      padding: 0.8rem;
      border-radius: 0.6rem;
      border: none;
      background: linear-gradient(135deg, #7c3aed, #2563eb);
      color: #fff;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.2s;
    }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    button:not(:disabled):hover { opacity: 0.88; }

    #status-box {
      margin-top: 1.5rem;
      padding: 1rem 1.25rem;
      border-radius: 0.75rem;
      font-size: 0.9rem;
      display: none;
    }
    .status-waiting  { background: #1e1e3a; border: 1px solid #3a3a6a; color: #aaa; }
    .status-started  { background: #1a2a1a; border: 1px solid #2a6a2a; color: #6fdb6f; }
    .status-finished { background: #1a2a3a; border: 1px solid #2a5a9a; color: #60a5fa; }
    .status-failed   { background: #2a1a1a; border: 1px solid #6a2a2a; color: #f87171; }

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
      border: 2px solid #888;
      border-top-color: #a78bfa;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      vertical-align: middle;
      margin-right: 6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="card">
    <h1>🎤 Karaoke Maker</h1>
    <p class="subtitle">Paste a YouTube link — we'll strip out the vocals.</p>

    <label for="url-input">YouTube URL</label>
    <input type="url" id="url-input" placeholder="https://www.youtube.com/watch?v=..." />

    <button id="submit-btn" onclick="submitJob()">Generate Karaoke Track</button>

    <div id="status-box"></div>
  </div>

  <script>
    let pollInterval = null;

    async function submitJob() {
      const url = document.getElementById('url-input').value.trim();
      if (!url) return alert('Please enter a YouTube URL.');

      const btn = document.getElementById('submit-btn');
      btn.disabled = true;
      showStatus('waiting', '<span class="spinner"></span> Submitting job…');

      try {
        const res = await fetch('/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        pollStatus(data.job_id);
      } catch (err) {
        showStatus('failed', '❌ ' + err.message);
        btn.disabled = false;
      }
    }

    function pollStatus(jobId) {
      clearInterval(pollInterval);
      pollInterval = setInterval(async () => {
        try {
          const res = await fetch('/status/' + jobId);
          const data = await res.json();

          if (data.status === 'queued' || data.status === 'deferred') {
            showStatus('waiting', '<span class="spinner"></span> Queued — waiting for worker…');
          } else if (data.status === 'started') {
            showStatus('started', '<span class="spinner"></span> Processing… this can take a few minutes.');
          } else if (data.status === 'finished') {
            clearInterval(pollInterval);
            showStatus('finished',
              '✅ Done! Your karaoke track is ready.<br><br>' +
              '<a class="download-btn" href="/download/' + jobId + '">⬇ Download Karaoke (WAV)</a>'
            );
            document.getElementById('submit-btn').disabled = false;
          } else if (data.status === 'failed') {
            clearInterval(pollInterval);
            showStatus('failed', '❌ Job failed. Please try another URL or check server logs.');
            document.getElementById('submit-btn').disabled = false;
          }
        } catch (e) {
          // Network blip — keep polling
        }
      }, 3000);
    }

    function showStatus(type, html) {
      const box = document.getElementById('status-box');
      box.className = 'status-box status-' + type;
      box.style.display = 'block';
      box.innerHTML = html;
    }
  </script>
</body>
</html>
"""

# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML)


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
        job_timeout=900,   # 15 min max — spleeter on long songs can be slow
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