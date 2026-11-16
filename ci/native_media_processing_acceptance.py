#!/usr/bin/env python3
"""Real local media bytes -> Qt workers -> validated outcomes, not mocked results.

This is NOT remote generative video/photo, Kie.ai, or full interactive E2E.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "AI_SMM_Work_Architecture_A75"))

from app import EditWorker, VideoTrimWorker
from media_result_ownership import commit_owned_image


def checked(cmd, timeout=90):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                            encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("media command failed with return code " + str(result.returncode))
    return result.stdout


def run_worker(app, worker, timeout=60):
    successes, failures = [], []
    worker.done.connect(successes.append)
    worker.failed.connect(failures.append)
    worker.start()
    deadline = time.monotonic() + timeout
    while worker.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    if worker.isRunning():
        raise RuntimeError("Qt media worker deadline exceeded")
    if not worker.wait(5000):
        raise RuntimeError("Qt media worker did not join")
    app.processEvents()
    if len(successes) != 1 or failures:
        raise RuntimeError("Qt media operation did not complete: "
                           + ("failure" if failures else "missing result"))
    return Path(successes[0])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or QApplication([])
    evidence = {"status": "BLOCKED", "scope": "real local photo/video processing",
                "kie_ai_tested": False, "remote_generation_tested": False,
                "checks": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="ai_smm_media_acceptance_") as temp:
            root = Path(temp)
            owners = [root / "project_a", root / "project_b"]
            for owner in owners:
                (owner / "processed").mkdir(parents=True)
            original = root / "original.png"
            reference = root / "reference.png"
            with Image.new("RGB", (256, 192), (60, 120, 190)) as image:
                draw = ImageDraw.Draw(image)
                draw.ellipse((20, 20, 180, 160), fill=(220, 90, 40))
                image.save(original)
            with Image.new("RGB", (256, 192), (180, 140, 80)) as image:
                image.save(reference)
            original_hash = digest(original)
            output = owners[0] / "processed" / "adjusted.jpg"
            got = run_worker(app, EditWorker(original, reference, output,
                            "Сделай светлее, ярче, по образцу эталона",use_reference=True,adjustments={"brightness":1.12}), 45)
            assert got == output and got.stat().st_size > 1000
            with Image.open(got) as image:
                image.verify()
            assert digest(original) == original_hash, "original photo mutated"
            evidence["checks"]["photo_real_bytes_local_edit"] = "PASS"

            db = sqlite3.connect(":memory:")
            try:
                db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY)")
                db.execute("""CREATE TABLE project_files (
                    id TEXT PRIMARY KEY, project_id TEXT,kind TEXT,
                    display_name TEXT,path TEXT,created_at TEXT)""")
                db.executemany("INSERT INTO projects VALUES(?)", [("a",), ("b",)])
                db.commit()
                accepted = commit_owned_image(db, "a", output, got, owners[0])
                assert accepted == str(output.resolve())
                assert db.execute("SELECT COUNT(*) FROM project_files").fetchone()[0] == 1
                rejected = False
                try:
                    commit_owned_image(db, "b", output, got, owners[1])
                except ValueError:
                    rejected = True
                assert rejected, "cross-project media output incorrectly accepted"
                assert db.execute("SELECT COUNT(*) FROM project_files").fetchone()[0] == 1
                evidence["checks"]["project_image_ownership"] = "PASS"
            finally:
                db.close()

            video = root / "source.mp4"
            checked(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=12",
                    "-t", "2", "-c:v", "mpeg4", "-q:v", "5", "-y", str(video)])
            original_video_hash = digest(video)
            target = owners[0] / "processed" / "clip.mp4"
            result = run_worker(app, VideoTrimWorker(video, target, .25, 1.5), 100)
            assert result == target and target.stat().st_size > 1000
            meta = json.loads(checked(["ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=codec_type",
                "-of", "json", str(target)]))
            length = float(meta["format"]["duration"])
            assert .8 <= length <= 1.7, "trimmed media duration incorrect"
            assert any(s.get("codec_type") == "video" for s in meta.get("streams", []))
            assert digest(video) == original_video_hash, "original video mutated"
            evidence["checks"]["video_real_bytes_ffmpeg_trim"] = "PASS"
            evidence["checks"]["video_codec_probe"] = "PASS"
            evidence["checks"]["video_original_integrity"] = "PASS"
        evidence["status"] = "PASS"
    except Exception as exc:
        evidence["status"] = "BLOCKED"
        evidence["error_class"] = type(exc).__name__
        evidence["reason"] = str(exc)[:160] if isinstance(exc, AssertionError) else "media operation or dependency failed"
    output = ROOT / "ci_evidence" / "native_media_acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))
    return 0 if evidence["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
