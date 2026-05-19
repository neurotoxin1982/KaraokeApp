import os
import sys
from flask import Flask, request, jsonify
from redis import Redis
from rq import Queue, Worker, Connection

app = Flask(__name__)
redis_url = os.getenv('REDIS_URL', 'redis://redis:6379/0')
redis_conn = Redis.from_url(redis_url)
q = Queue(connection=redis_conn)

def process_karaoke_job(video_url):
    print(f"Worker started downloading: {video_url}")
    return "Success"

@app.route('/convert', methods=['POST'])
def convert_request():
    data = request.get_json() or {}
    url = data.get('url')
    if not url:
        return jsonify({"error": "Missing URL"}), 400
        
    job = q.enqueue(process_karaoke_job, url)
    return jsonify({"job_id": job.get_id(), "status": "queued"}), 202

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        listen = ['default']
        with Connection(redis_conn):
            worker = Worker(list(map(Queue, listen)))
            print("Coolify Background Worker Listening for Tracks...")
            worker.work()
    else:
        app.run(host='0.0.0.0', port=5000)