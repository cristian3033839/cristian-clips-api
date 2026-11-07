#!/usr/bin/env python3
"""Clip-cutting pipeline for the Cristian Clips API.

Reuses the proven logic from ~/workspace/araya-india/hacer_cortes.py
(and its siblings in araya-artico / araya-africa / araya-serpientes):
  - yt-dlp with the Android player client for the download
  - faster-whisper tiny, Spanish, for transcription
  - karaoke ASS subtitles (white -> yellow word by word)
  - ffmpeg render: central crop -> 720x1280, avatar overlay top-right
"""

import json
import os
import re
import shutil
import subprocess
import sys

AVATAR = os.environ.get(
    "AVATAR_PATH",
    "/home/hatch/workspace/araya-serpientes/avatar-foto-sin-fondo.png")

# YouTube cookies (Netscape cookies.txt from a logged-in session).
# Render secret files land in /etc/secrets/; the path is overridable.
# Only used when the file actually exists, so the service also runs
# without cookies. Added 2026-11-07: the android/ios/tv/web client
# fallback was tried and ALL FOUR are blocked by YouTube on Render's
# IPs (job ff3e7e52b27a) — cookies are the reliable fix.
COOKIES_PATH = os.environ.get("COOKIES_PATH", "/etc/secrets/youtube-cookies.txt")


def _yt_dlp_args():
    """Base yt-dlp args: multi-client fallback + cookies when present.

    Render mounts secret files read-only, but yt-dlp saves the cookie
    jar back to the file at the end of a run (OSError 30 otherwise),
    so the cookies are copied to a writable temp path first.
    IMPORTANT: the copy happens ONLY when /tmp has no jar yet.
    YouTube rotates session cookies in its responses and yt-dlp saves
    the new values back to the jar; re-copying the (now stale) secret
    file on every run discards those updates and the next run fails
    with a bot check (seen 2026-11-07: job 90645f983942 worked, job
    25722af80871 failed 6 minutes later on the same cookies).
    A secret-file update redeploys the service, which wipes /tmp, so
    a fresh export is picked up naturally on the next deploy.
    The web client needs a JS runtime to solve YouTube's "n" challenge
    (yt-dlp only enables deno by default) and the challenge solver
    script from GitHub, hence --js-runtimes node and
    --remote-components ejs:github."""
    args = ["python3", "-m", "yt_dlp",
            "--extractor-args", "youtube:player_client=android,ios,tv,web",
            "--js-runtimes", "node",
            "--remote-components", "ejs:github"]
    if os.path.exists(COOKIES_PATH):
        tmp_cookies = "/tmp/youtube-cookies.txt"
        if not os.path.exists(tmp_cookies):
            shutil.copyfile(COOKIES_PATH, tmp_cookies)
        args += ["--cookies", tmp_cookies]
    return args

# Subtitle styles offered by the public page (6 designs).
# karaoke: word-by-word \k tags, yellow fill over white.
# The rest render whole-line events (no \k) with their own look.
STYLES = {
    "karaoke":  {"fontsize_v": 48, "fontsize_h": 40, "primary": "&H0000FFFF&",
                 "secondary": "&H00FFFFFF&", "outline": 4, "border_style": 1,
                 "back": "&H80000000&", "karaoke": True, "caps": False},
    "clasico":  {"fontsize_v": 48, "fontsize_h": 40, "primary": "&H00FFFFFF&",
                 "secondary": "&H00FFFFFF&", "outline": 4, "border_style": 1,
                 "back": "&H80000000&", "karaoke": False, "caps": False},
    "tiktok":   {"fontsize_v": 64, "fontsize_h": 52, "primary": "&H00FFFFFF&",
                 "secondary": "&H00FFFFFF&", "outline": 5, "border_style": 1,
                 "back": "&H80000000&", "karaoke": False, "caps": False},
    "impacto":  {"fontsize_v": 56, "fontsize_h": 48, "primary": "&H0000FFFF&",
                 "secondary": "&H0000FFFF&", "outline": 4, "border_style": 1,
                 "back": "&H80000000&", "karaoke": False, "caps": True},
    "minimal":  {"fontsize_v": 36, "fontsize_h": 32, "primary": "&H00FFFFFF&",
                 "secondary": "&H00FFFFFF&", "outline": 2, "border_style": 1,
                 "back": "&H80000000&", "karaoke": False, "caps": False},
    "caja":     {"fontsize_v": 48, "fontsize_h": 40, "primary": "&H00FFFFFF&",
                 "secondary": "&H00FFFFFF&", "outline": 0, "border_style": 3,
                 "back": "&HCC000000&", "karaoke": False, "caps": False},
}

EMOTIONAL = ["no", "miedo", "increíble", "increible", "imposible", "nunca",
             "jamás", "jamas", "loco", "locura", "ayuda", "peligro",
             "muerte", "gigante", "enorme", "secreto", "verdad", "mentira",
             "amor", "traición", "traicion", "llora", "grita", "corre"]


def fmt_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def esc(t):
    return t.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def slugify(text, maxlen=40):
    # ASCII-only: accents break Content-Disposition filenames.
    import unicodedata
    s = unicodedata.normalize("NFKD", text)
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:maxlen].strip("-") or "corte"


def build_ass(words, path, style="karaoke", horizontal=False):
    """words: list of (start, end, text) relative to the clip start."""
    st = STYLES.get(style, STYLES["karaoke"])
    fontsize = st["fontsize_h"] if horizontal else st["fontsize_v"]
    playres_x, playres_y = (1280, 720) if horizontal else (720, 1280)
    margin_v = 60 if horizontal else 170
    lines = []
    cur = []
    for w in words:
        if cur and (len(cur) >= 3 or w[0] - cur[-1][1] > 0.5):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {playres_x}
PlayResY: {playres_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Clip,DejaVu Sans,{fontsize},{st["primary"]},{st["secondary"]},&H00000000&,{st["back"]},-1,0,0,0,100,100,1,0,{st["border_style"]},{st["outline"]},1,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for line in lines:
        start = line[0][0]
        end = line[-1][1]
        if st["karaoke"]:
            parts = []
            for (ws, we, wt) in line:
                dur_cs = max(1, int(round((we - ws) * 100)))
                # BUG LESSON: every word inside a \k tag must end with a
                # space, otherwise words come out stuck together.
                parts.append(f"{{\\k{dur_cs}}}{esc(wt)} ")
            text = "".join(parts)
        else:
            text = " ".join(esc(wt) for (_, _, wt) in line)
            if st["caps"]:
                text = text.upper()
        events.append(
            f"Dialogue: 0,{fmt_time(start)},{fmt_time(end)},Clip,,0,0,0,,{text}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")


def run(cmd, timeout=900):
    """Run a command, raising on failure with the tail of stderr."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "")[-1500:]
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{tail}")
    return p


def _run_with_retries(cmd, timeout=900, attempts=3):
    """YouTube's bot check ('Sign in to confirm you're not a bot') is
    intermittent on this VM: the same command can fail once and succeed
    minutes later. Retry a few times before giving up."""
    import time as _time
    last = None
    for i in range(attempts):
        try:
            return run(cmd, timeout=timeout)
        except Exception as e:
            last = e
            if i < attempts - 1:
                _time.sleep(20)
    raise last


def download_info(url):
    """Fetch the video metadata (title, duration, heatmap) via yt-dlp.

    Render's IPs get YouTube's bot check with the Android client alone,
    so several player clients are tried in order (android, iOS, TV, web)
    until one returns the player response. When a cookies file is
    present it is passed too (the reliable fix for the bot check)."""
    p = _run_with_retries(_yt_dlp_args() +
                          ["--no-download", "--dump-json", url], timeout=300)
    return json.loads(p.stdout)


def download_video(url, out_path):
    """Download best <=720p (these videos usually only offer 360p).
    yt-dlp resumes from the .part file, so a retry after a 403 continues.
    Several player clients are tried in order against YouTube's bot check,
    plus the cookies file when present."""
    _run_with_retries(_yt_dlp_args() +
                      ["-f", "best[height<=720]",
                       "-o", out_path, url], timeout=1800)


def extract_wav(src, wav_path, start=None, dur=None):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if dur is not None:
        cmd += ["-t", str(dur)]
    cmd += ["-i", src, "-vn", "-c:a", "pcm_s16le", "-ar", "16000", wav_path]
    run(cmd, timeout=600)


def _transcribe_subprocess(wav, word_timestamps=False):
    """Run faster-whisper in a SHORT-LIVED subprocess and return its
    JSON: {"segments": [{"start","end","text","words": [[s,e,t],...]}]}.
    The model (~250 MB) never loads into the API server process, so the
    server + model + ffmpeg cannot together exceed Render Free's 512 MB
    (OOM 2026-11-07 02:34). The subprocess exits and frees the memory
    before ffmpeg runs. Thread limits are set in transcribe.py."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "transcribe.py")
    cmd = [sys.executable, script, wav]
    if word_timestamps:
        cmd.append("--words")
    env = dict(os.environ)
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    out = subprocess.run(cmd, capture_output=True, text=True,
                         timeout=900, env=env)
    if out.returncode != 0:
        raise RuntimeError(
            "transcribe.py failed: " + (out.stderr or "")[-400:])
    return json.loads(out.stdout)


def _segments_from_wav(wav, offset=0.0):
    data = _transcribe_subprocess(wav, word_timestamps=False)
    out = []
    for s in data["segments"]:
        out.append({"start": s["start"] + offset, "end": s["end"] + offset,
                    "text": s["text"]})
    return out


def transcribe_full(wav_path, duration):
    """Transcribe the whole audio. Long audio is split into 20-minute
    chunks and transcribed piece by piece: a single faster-whisper run
    over a ~3h wav was once killed by memory (OOM) on this VM."""
    chunk = 1200  # 20 min
    if duration <= 2400:
        return _segments_from_wav(wav_path)
    segments = []
    start = 0.0
    idx = 0
    while start < duration:
        part = wav_path + f".part{idx}.wav"
        extract_wav(wav_path, part, start=start, dur=min(chunk, duration - start))
        segments += _segments_from_wav(part, offset=start)
        os.remove(part)
        start += chunk
        idx += 1
    return segments


def save_transcript(segments, path):
    with open(path, "w", encoding="utf-8") as f:
        for s in segments:
            f.write(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}\n")


def _window_text(segments, start, end):
    return " ".join(s["text"] for s in segments
                    if s["end"] > start and s["start"] < end)


def _title_from_text(text):
    """First sentence/fragment of the window's spoken text, cleaned."""
    text = re.sub(r"\s+", " ", text).strip(" .!?,;:")
    if not text:
        return "Momento destacado"
    # first sentence
    m = re.split(r"[.!?]\s", text, maxsplit=1)
    title = m[0].strip()
    if len(title) < 8 and len(m) > 1:
        title = (title + " " + m[1]).strip()
    if len(title) > 80:
        title = title[:77].rsplit(" ", 1)[0] + "..."
    return title[0].upper() + title[1:] if title else "Momento destacado"


def viral_pct_and_reasons(heat_value, text):
    """VIRAL % IS AN ESTIMATE, NOT A PREDICTION.

    It is computed from YouTube's most-replayed heatmap value (when the
    video has one) plus transcript signals (questions, numbers, emotional
    words, exclamations). Nobody can predict whether a clip will actually
    go viral; this number only ranks the clips of one video against each
    other so the user can pick which to try first.
    """
    reasons = []
    signals = 0
    low = text.lower()
    if heat_value is not None:
        pct = 55 + round(heat_value * 40)
        reasons.append("Uno de los momentos más repetidos del video")
    else:
        pct = 60
    if "?" in text or "¿" in text:
        signals += 1
        reasons.append("Incluye una pregunta que engancha")
    if re.search(r"\b\d+\b", text):
        signals += 1
        reasons.append("Incluye un dato concreto (número)")
    if any(w in low for w in EMOTIONAL):
        signals += 1
        reasons.append("Frase con carga emocional")
    if "!" in text or "¡" in text:
        signals += 1
        reasons.append("Exclamación / sorpresa")
    pct += 2 * signals
    if heat_value is None:
        pct += 4 * signals
    pct = max(40, min(97, pct))
    return pct, reasons[:3]


def heatmap_picks(info, duration, num_clips, clip_dur):
    """Picks from YouTube heatmap peaks only, WITHOUT any transcript.

    Render free tier has 0.1 CPU and 512 MB RAM: transcribing a whole
    30-minute video there takes far too long. When the video has a
    heatmap with enough peaks, the moments come straight from it and
    each clip's own short segment transcription (already done for the
    karaoke subtitles) supplies its title and viral reasons later.
    Returns picks with title=None and viral_pct=None to fill in per
    clip. If there are not enough peaks, the caller falls back to the
    full-transcription path (pick_moments)."""
    heat = info.get("heatmap") or []
    peaks = [e for e in heat
             if isinstance(e, dict) and e.get("start_time", 0) >= 20
             and e.get("value", 0) > 0]
    peaks.sort(key=lambda e: e["value"], reverse=True)
    picks = []
    used_starts = []
    spacing = max(30, clip_dur + 15)

    for e in peaks:
        if len(picks) >= num_clips:
            break
        start = max(0, int(e["start_time"]) - 20)
        if start + clip_dur > duration:
            start = max(0, int(duration) - clip_dur)
        if not all(abs(start - u) >= spacing for u in used_starts):
            continue
        used_starts.append(start)
        picks.append({"start": start, "duration": clip_dur,
                      "heat_value": e["value"], "viral_pct": None,
                      "title": None, "reasons": []})
    picks.sort(key=lambda p: p["start"])
    return picks


def pick_moments(info, segments, duration, num_clips, clip_dur):
    """Best moments: heatmap peaks first (skipping the 0-17s intro peak),
    otherwise the highest-signal transcript windows."""
    heat = info.get("heatmap") or []
    peaks = [e for e in heat
             if isinstance(e, dict) and e.get("start_time", 0) >= 20
             and e.get("value", 0) > 0]
    peaks.sort(key=lambda e: e["value"], reverse=True)
    picks = []
    used_starts = []
    # Moments must not overlap: spacing scales with the clip length so
    # short clips (10-15 s) can still yield many picks from one video.
    spacing = max(30, clip_dur + 15)

    def far_enough(start):
        return all(abs(start - u) >= spacing for u in used_starts)

    for e in peaks:
        if len(picks) >= num_clips:
            break
        start = max(0, int(e["start_time"]) - 20)
        if start + clip_dur > duration:
            start = max(0, int(duration) - clip_dur)
        if not far_enough(start):
            continue
        used_starts.append(start)
        text = _window_text(segments, start, start + clip_dur)
        pct, reasons = viral_pct_and_reasons(e["value"], text)
        picks.append({"start": start, "duration": clip_dur,
                      "heat_value": e["value"], "viral_pct": pct,
                      "title": _title_from_text(text), "reasons": reasons})

    if len(picks) < num_clips:
        # Fallback: score transcript windows by signal density.
        step = max(30, clip_dur // 2)
        scored = []
        t = 20
        while t + clip_dur <= duration:
            text = _window_text(segments, t, t + clip_dur)
            if text.strip():
                _, reasons = viral_pct_and_reasons(None, text)
                scored.append((len(reasons), t, text))
            t += step
        scored.sort(key=lambda x: (-x[0], x[1]))
        for _, start, text in scored:
            if len(picks) >= num_clips:
                break
            if not far_enough(start):
                continue
            used_starts.append(start)
            pct, reasons = viral_pct_and_reasons(None, text)
            picks.append({"start": start, "duration": clip_dur,
                          "heat_value": None, "viral_pct": pct,
                          "title": _title_from_text(text),
                          "reasons": reasons})
    picks.sort(key=lambda p: p["start"])
    return picks


def render_clip(src, start, dur, out_path, ass_path=None,
                horizontal=False, avatar=True, style="karaoke"):
    """Render one finished cut with ffmpeg (proven reference chain)."""
    fc_parts = []
    if avatar:
        av_w, av_h, av_x, av_y = (140, 249, 1130, 15) if horizontal \
            else (200, 355, 500, 20)
        fc_parts.append(f"[1:v]scale={av_w}:{av_h},format=rgba[av]")
    if horizontal:
        base = (f"[0:v]crop=608:342:16:9,scale=1280:720,"
                f"unsharp=5:5:0.6:5:5:0.0")
    else:
        base = (f"[0:v]crop=202:360:219:0,scale=720:1280,"
                f"unsharp=5:5:0.6:5:5:0.0")
    if ass_path:
        base += f",ass={ass_path}"
    if avatar:
        fc_parts.append(base + "[base]")
        fc_parts.append(f"[base][av]overlay={av_x}:{av_y}:shortest=1[v]")
        vmap = "[v]"
    else:
        fc_parts.append(base + "[v]")
        vmap = "[v]"
    fc = ";".join(fc_parts)
    cmd = ["ffmpeg", "-y", "-i", src]
    if avatar:
        cmd += ["-loop", "1", "-i", AVATAR]
    cmd += ["-filter_complex", fc, "-map", vmap, "-map", "0:a",
            "-c:v", "libx264", "-crf", "22", "-preset", "medium",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out_path]
    run(cmd, timeout=1800)


def transcribe_segment(wav_path):
    """Word timestamps for one clip segment (for the karaoke subtitles)."""
    data = _transcribe_subprocess(wav_path, word_timestamps=True)
    words = []
    for s in data["segments"]:
        for (st, en, txt) in s["words"]:
            words.append((st, en, txt))
    return words
