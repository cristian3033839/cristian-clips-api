#!/usr/bin/env python3
"""Cristian Clips — clip-cutting API service (Render deployment).

Same endpoints as the local service, tuned for Render's free tier
(0.1 CPU, 512 MB RAM): when the video has a YouTube heatmap with
enough peaks, moments are picked straight from it and the full-audio
transcription is skipped; each clip's short segment transcription
supplies its title and viral reasons.

Endpoints:
  POST /api/jobs            {youtube_url, format, num_clips, min_seconds,
                             max_seconds, subtitles, subtitle_style, avatar}
                            -> {job_id, status} (returns immediately;
                            the job runs in a background thread)
  GET  /api/jobs/{id}      {job_id, status, progress, clips: [...]}
  GET  /api/jobs/{id}/clips/{filename}
                            -> the finished mp4 (Content-Disposition attachment)
  GET  /                    health check

Jobs persist in jobs.json; each job's files live under jobs/<job_id>/.
Render's disk is ephemeral, so jobs do not survive a restart.
"""
import json
import os
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import pipeline

BASE = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE, "jobs")
JOBS_FILE = os.path.join(BASE, "jobs.json")
HOST, PORT = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8765"))

_lock = threading.Lock()
_jobs = {}


def _load_jobs():
    if os.path.exists(JOBS_FILE):
        try:
            with open(JOBS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_jobs():
    tmp = JOBS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_jobs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, JOBS_FILE)


_jobs.update(_load_jobs())


def _update(job_id, **fields):
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save_jobs()


def run_job(job_id):
    """Background worker: download -> transcribe -> pick moments -> render."""
    job = _jobs[job_id]
    jdir = os.path.join(JOBS_DIR, job_id)
    cdir = os.path.join(jdir, "cortes")
    os.makedirs(cdir, exist_ok=True)
    opts = job["options"]
    clip_dur = int(opts["max_seconds"])
    horizontal = opts["format"] == "horizontal"
    try:
        _update(job_id, status="downloading",
                progress="Descargando el video de YouTube...")
        info = pipeline.download_info(job["youtube_url"])
        with open(os.path.join(jdir, "info.json"), "w",
                  encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
        duration = float(info.get("duration") or 0)
        _update(job_id, title=info.get("title", ""),
                duration=duration)
        orig = os.path.join(jdir, "original.mp4")
        pipeline.download_video(job["youtube_url"], orig)

        # Render free tier (0.1 CPU, 512 MB): skip the full transcription
        # when the video's heatmap already gives enough moments. Each
        # clip's short segment transcription then supplies its title.
        num = int(opts["num_clips"])
        picks = pipeline.heatmap_picks(info, duration, num, clip_dur)
        heatmap_only = len(picks) >= num
        if heatmap_only:
            _update(job_id, status="rendering",
                    progress="Momentos elegidos del heatmap, sin transcribir todo el video...")
        else:
            _update(job_id, status="transcribing",
                    progress="Transcribiendo el audio...")
            wav = os.path.join(jdir, "audio-full.wav")
            pipeline.extract_wav(orig, wav)
            segments = pipeline.transcribe_full(wav, duration)
            pipeline.save_transcript(
                segments, os.path.join(jdir, "transcripcion.txt"))
            _update(job_id, status="rendering",
                    progress="Buscando los mejores momentos...")
            picks = pipeline.pick_moments(
                info, segments, duration, num, clip_dur)
        clips = []
        for i, pick in enumerate(picks, 1):
            # Cut and transcribe with temporary names first: in
            # heatmap-only mode the real title is only known after the
            # segment transcription, and the file name uses its slug.
            raw_tmp = os.path.join(cdir, f"corte{i}-tmp-raw.mp4")
            wav_tmp = os.path.join(cdir, f"corte{i}-tmp.wav")
            _update(job_id,
                    progress=f"Generando corte {i} de {len(picks)}...")
            pipeline.run(["ffmpeg", "-y", "-v", "error",
                          "-ss", str(pick["start"]), "-t", str(pick["duration"]),
                          "-i", orig, "-c", "copy", raw_tmp])
            pipeline.extract_wav(raw_tmp, wav_tmp)
            # In heatmap-only mode the title and viral reasons come from
            # this clip's own words, so transcribe the segment even with
            # subtitles off.
            words = []
            if opts["subtitles"] or pick.get("title") is None:
                words = pipeline.transcribe_segment(wav_tmp)
            if pick.get("title") is None:
                text = " ".join(w for (_, _, w) in words)
                pick["title"] = pipeline._title_from_text(text)
                pct, reasons = pipeline.viral_pct_and_reasons(
                    pick.get("heat_value"), text)
                pick["viral_pct"] = pct
                pick["reasons"] = reasons
            slug = pipeline.slugify(pick["title"])
            name = f"corte{i}-{slug}"
            raw = os.path.join(cdir, f"{name}-raw.mp4")
            wav_c = os.path.join(cdir, f"{name}.wav")
            ass = os.path.join(cdir, f"{name}.ass")
            out = os.path.join(cdir, f"{name}.mp4")
            os.rename(raw_tmp, raw)
            os.rename(wav_tmp, wav_c)
            ass_path = None
            if opts["subtitles"] and words:
                pipeline.build_ass(words, ass, style=opts["subtitle_style"],
                                   horizontal=horizontal)
                ass_path = ass
            pipeline.render_clip(raw, pick["start"], pick["duration"], out,
                                 ass_path=ass_path, horizontal=horizontal,
                                 avatar=bool(opts["avatar"]),
                                 style=opts["subtitle_style"])
            clips.append({
                "filename": f"{name}.mp4",
                "start": pick["start"],
                "end": pick["start"] + pick["duration"],
                "duration": pick["duration"],
                "viral_pct": pick["viral_pct"],
                "title": pick["title"],
                "reasons": pick["reasons"],
            })
            _update(job_id, clips=clips)
        _update(job_id, status="done",
                progress=f"Listo: {len(clips)} cortes generados.", clips=clips)
    except Exception as e:
        traceback.print_exc()
        _update(job_id, status="error",
                progress="Error en el proceso.",
                error=str(e)[-500:])


def public_job(job):
    return {
        "job_id": job["job_id"],
        "youtube_url": job["youtube_url"],
        "title": job.get("title", ""),
        "duration": job.get("duration", 0),
        "status": job["status"],
        "progress": job.get("progress", ""),
        "error": job.get("error", ""),
        "options": job["options"],
        "clips": job.get("clips", []),
        "created_at": job.get("created_at", ""),
        "updated_at": job.get("updated_at", ""),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, filename):
        from urllib.parse import quote
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(size))
        # filename* (RFC 5987) carries the UTF-8 name; filename is ASCII.
        safe = filename.encode("ascii", "ignore").decode("ascii")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{safe}"; '
            f"filename*=UTF-8''{quote(filename)}")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")
                 if p != ""]
        if not parts:
            with _lock:
                count = len(_jobs)
            self._send_json(200, {"service": "cristian-clips-api",
                                  "status": "ok", "jobs": count})
            return
        if parts[0] == "api" and len(parts) >= 3 and parts[1] == "jobs":
            job_id = parts[2]
            with _lock:
                job = _jobs.get(job_id)
            if job is None:
                self._send_json(404, {"error": "job not found"})
                return
            if len(parts) == 3:
                self._send_json(200, public_job(job))
                return
            if len(parts) == 5 and parts[3] == "clips":
                filename = parts[4]
                # No path traversal: basename only, inside this job's cortes.
                if filename != os.path.basename(filename):
                    self._send_json(400, {"error": "bad filename"})
                    return
                path = os.path.join(JOBS_DIR, job_id, "cortes", filename)
                if not os.path.isfile(path):
                    self._send_json(404, {"error": "clip not found"})
                    return
                self._send_file(path, filename)
                return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/jobs":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json(400, {"error": "invalid JSON"})
            return
        url = (body.get("youtube_url") or "").strip()
        if not url or ("youtube.com" not in url and "youtu.be" not in url):
            self._send_json(400, {"error": "youtube_url is required"})
            return
        fmt = body.get("format", "vertical")
        if fmt not in ("vertical", "horizontal"):
            fmt = "vertical"
        try:
            num_clips = max(1, min(20, int(body.get("num_clips", 3))))
            min_s = max(10, min(90, int(body.get("min_seconds", 40))))
            max_s = max(10, min(120, int(body.get("max_seconds", 60))))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "bad numeric option"})
            return
        if max_s < min_s:
            max_s = min_s
        style = str(body.get("subtitle_style", "karaoke"))
        if style not in pipeline.STYLES:
            style = "karaoke"
        options = {
            "format": fmt,
            "num_clips": num_clips,
            "min_seconds": min_s,
            "max_seconds": max_s,
            "subtitles": bool(body.get("subtitles", True)),
            "subtitle_style": style,
            "avatar": bool(body.get("avatar", True)),
        }
        job_id = uuid.uuid4().hex[:12]
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        job = {"job_id": job_id, "youtube_url": url, "title": "",
               "duration": 0, "status": "downloading",
               "progress": "Trabajo creado, empezando la descarga...",
               "error": "", "options": options, "clips": [],
               "created_at": now, "updated_at": now}
        with _lock:
            _jobs[job_id] = job
            _save_jobs()
        os.makedirs(os.path.join(JOBS_DIR, job_id, "cortes"), exist_ok=True)
        t = threading.Thread(target=run_job, args=(job_id,), daemon=True)
        t.start()
        self._send_json(201, {"job_id": job_id, "status": "downloading"})


if __name__ == "__main__":
    os.makedirs(JOBS_DIR, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"cristian-clips-api listening on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()
