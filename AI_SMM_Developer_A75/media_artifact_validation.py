"""Shared full-byte validation for local and remote project media artifacts."""
import hashlib
import json
import math
from pathlib import Path

def digest_file(path, *, check=None):
    """Hash all bytes with cooperative stop points; legacy callers need no callback."""
    if check is not None and not callable(check):
        raise TypeError('Media digest cancellation check must be callable')
    result=hashlib.sha256()
    if check is not None:check()
    with Path(path).open("rb") as source:
        while True:
            if check is not None:check()
            block=source.read(1024*1024)
            if check is not None:check()
            if not block:break
            result.update(block)
    if check is not None:check()
    return result.hexdigest()


def validate_media_bytes(path, *, is_video, check=None):
    """Verify bytes before they cross the project publication boundary."""
    if check is not None:check()
    if path.stat().st_size > (500 if is_video else 60)*1024*1024:
        raise ValueError("downloaded Kie media exceeds maximum size")
    if not is_video:
        from PIL import Image, UnidentifiedImageError
        expected="PNG" if path.suffix.lower()==".png" else "JPEG"
        try:
            with Image.open(path) as image:
                if image.format!=expected:
                    raise ValueError("downloaded photo MIME does not match output")
                width,height=image.size
                if (width<=0 or height<=0 or width>16000 or height>16000
                        or width*height>40_000_000):
                    raise ValueError("downloaded photo pixel budget exceeded")
                # Verify actual decoded pixels, not only the image header.
                image.load()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError("downloaded photo cannot be fully decoded") from exc
    else:
        from media_subprocess_runtime import run_owned_media_process
        try:
            code,output,_=run_owned_media_process(
                ["ffprobe","-v","error","-show_entries",
                 "stream=codec_type:format=duration","-of","json",str(path)],
                check,timeout=15,capture_stdout=True)
            if code:raise ValueError("nonzero ffprobe exit")
            metadata=json.loads(output)
        except (OSError,TimeoutError,ValueError) as exc:
            raise ValueError("downloaded video failed ffprobe verification") from exc
        if not any(item.get("codec_type")=="video"
                   for item in metadata.get("streams",[]) if isinstance(item,dict)):
            raise ValueError("downloaded media has no video stream")
        duration=float(metadata.get("format",{}).get("duration",0))
        if not math.isfinite(duration) or duration<=0:
            raise ValueError("downloaded video duration invalid")



    if check is not None:check()
