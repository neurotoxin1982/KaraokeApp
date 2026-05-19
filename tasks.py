import os
import glob
import subprocess

OUTPUT_DIR = '/app/output'
COOKIES_PATH = '/app/cookies.txt'  # mount your YouTube cookies here


def process_karaoke(job_id: str, youtube_url: str) -> dict:
    """
    1. Download audio from YouTube via yt-dlp
    2. Separate vocals / accompaniment via spleeter
    Returns paths to both output files.
    """
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # ── 1. Download ──────────────────────────────────────────────────────────
    audio_template = os.path.join(job_dir, 'audio.%(ext)s')
    cmd = [
        'yt-dlp',
        '--extract-audio',
        '--audio-format', 'wav',
        '--audio-quality', '0',
        '-o', audio_template,
    ]

    # Use cookies if available — required to bypass YouTube bot detection on servers
    if os.path.exists(COOKIES_PATH):
        cmd += ['--cookies', COOKIES_PATH]

    cmd.append(youtube_url)

    dl = subprocess.run(cmd, capture_output=True, text=True)
    if dl.returncode != 0:
        raise RuntimeError(f'yt-dlp failed:\n{dl.stderr}')

    # Locate the downloaded wav
    candidates = glob.glob(os.path.join(job_dir, 'audio*.wav'))
    if not candidates:
        raise RuntimeError('Downloaded audio file not found after yt-dlp run.')
    audio_path = candidates[0]

    # ── 2. Separate ──────────────────────────────────────────────────────────
    from spleeter.separator import Separator

    separated_dir = os.path.join(job_dir, 'separated')
    separator = Separator('spleeter:2stems')
    separator.separate_to_file(audio_path, separated_dir)

    stem = os.path.splitext(os.path.basename(audio_path))[0]
    accompaniment = os.path.join(separated_dir, stem, 'accompaniment.wav')
    vocals = os.path.join(separated_dir, stem, 'vocals.wav')

    if not os.path.exists(accompaniment):
        raise RuntimeError(f'Spleeter output not found: {accompaniment}')

    return {
        'accompaniment_path': accompaniment,
        'vocals_path': vocals,
    }