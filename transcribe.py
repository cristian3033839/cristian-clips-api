#!/usr/bin/env python3
"""Transcribe one wav file in a SHORT-LIVED subprocess.

Why a subprocess: faster-whisper loads the tiny model (~250 MB with its
libraries) into the calling process. On Render Free (512 MB) the API
server + the model + ffmpeg together exceed the limit and the instance
is OOM-killed (2026-11-07 02:34, job aa3100e54b74). Running the
transcription in its own process keeps the server lean; the process
exits and frees the memory before ffmpeg runs.

Thread limits are set BEFORE importing faster_whisper: the container
sees the host's many cores and the libraries would otherwise spawn
large thread pools. cpu_threads=1 keeps the peak low.

Usage:
    python3 transcribe.py <wav> [--words]
    --words : include per-word timestamps (karaoke subtitles)
Without --words only line-level segments are returned.

Output (JSON on stdout):
    {"segments": [{"start": float, "end": float, "text": str,
                   "words": [[start, end, text], ...]}, ...]}
"""
import os
import sys

# Limit thread pools BEFORE any heavy import reads them.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import json


def main():
    wav = sys.argv[1]
    want_words = "--words" in sys.argv[2:]
    from faster_whisper import WhisperModel
    model = WhisperModel("tiny", device="cpu", compute_type="int8",
                         cpu_threads=1)
    segments, _ = model.transcribe(
        wav, language="es", word_timestamps=want_words)
    out = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        words = []
        if want_words:
            for w in (s.words or []):
                t = w.word.strip()
                if t:
                    words.append([w.start, w.end, t])
        out.append({"start": s.start, "end": s.end, "text": text,
                    "words": words})
    json.dump({"segments": out}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
