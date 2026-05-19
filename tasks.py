import os
import glob
import subprocess

OUTPUT_DIR = '/app/output'


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
    dl = subprocess.run(
        [
            'yt-dlp',
            '--extract-audio',
            '--audio-format', 'wav',
            '--audio-quality', '0',
            '-o', audio_template,
            youtube_url,
        ],
        capture_output=True,
        text=True,
    )
    if dl.returncode != 0:
        raise RuntimeError(f'yt-dlp failed:\n{dl.stderr}')

    # Locate the downloaded wav (yt-dlp may name it audio.wav or audio.opus.wav etc.)
    candidates = glob.glob(os.path.join(job_dir, 'audio*.wav'))
    if not candidates:
        raise RuntimeError('Downloaded audio file not found after yt-dlp run.')
    audio_path = candidates[0]

    # ── 2. Separate ──────────────────────────────────────────────────────────
    # Import here so the worker process loads spleeter only when needed
    from spleeter.separator import Separator  # noqa: PLC0415

    separated_dir = os.path.join(job_dir, 'separated')
    separator = Separator('spleeter:2stems')
    separator.separate_to_file(audio_path, separated_dir)

    # spleeter outputs to: separated/<audio_stem>/accompaniment.wav
    stem = os.path.splitext(os.path.basename(audio_path))[0]  # e.g. "audio"
    accompaniment = os.path.join(separated_dir, stem, 'accompaniment.wav')
    vocals = os.path.join(separated_dir, stem, 'vocals.wav')

    if not os.path.exists(accompaniment):
        raise RuntimeError(f'Spleeter output not found at expected path: {accompaniment}')

    return {
        'accompaniment_path': accompaniment,
        'vocals_path': vocals,
    }