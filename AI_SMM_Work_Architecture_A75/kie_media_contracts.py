"""Validated Kie.ai media creation contracts; no network and no paid requests.

Grounded in the provided Kie OpenAPI specs:
- nano-banana-pro (image_input, up to 8);
- google/nano-banana-edit (image_urls, up to 10);
- kling-3.0-omni/transformation (source video, optionally reference images).

The Kie File Upload and Get Task Details schemas MUST be integrated separately:
a task ID is not evidence of a completed or downloaded media artifact.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
import re
import ipaddress

CREATE_TASK_PATH = "/api/v1/jobs/createTask"
IMAGE_ASPECTS = frozenset(("1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9","auto"))
VIDEO_RATIOS = frozenset(("16:9","9:16","1:1"))
FORMATS = frozenset(("png","jpg"))
TASK_ID = re.compile(r"^[A-Za-z0-9_-]{8,200}$")


def _url(value: str) -> str:
    # Only user-approved uploaded resource URLs; no local paths, file:, or credentials.
    if not isinstance(value,str) or len(value)>4096 or value!=value.strip():
        raise ValueError("invalid media URL")
    parts=urlsplit(value)
    if parts.scheme not in ("https", "oss") or not parts.netloc or parts.username or parts.password or parts.fragment:
        raise ValueError("media URLs must be authenticated-upload HTTPS/OSS URLs")
    host=(parts.hostname or "").lower()
    if (not host or host in ("localhost", "localhost.localdomain")
            or host.endswith((".local", ".internal", ".localhost"))):
        raise ValueError("local media URL disallowed")
    try:
        ip=ipaddress.ip_address(host)
    except ValueError:
        ip=None
    if ip is not None and not ip.is_global:
        raise ValueError("non-public media IP address disallowed")
    return value


def _images(urls, max_count, required=False):
    if not isinstance(urls,(list,tuple)) or len(urls)>max_count or (required and not urls):
        raise ValueError("invalid number of reference images")
    return [_url(u) for u in urls]


def _prompt(value, limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError("invalid media prompt")
    return value.strip()


def nano_banana_pro(prompt, image_input=(), aspect_ratio="1:1",
                    resolution="1K", output_format="png"):
    """Create payload only; no API call, upload, or credit consumption."""
    if aspect_ratio not in IMAGE_ASPECTS or resolution not in ("1K","2K","4K") or output_format not in FORMATS:
        raise ValueError("unsupported Nano Banana Pro output parameters")
    return {"model":"nano-banana-pro",
            "input":{"prompt":_prompt(prompt,20000),
                     "image_input":_images(image_input,8),
                     "aspect_ratio":aspect_ratio,"resolution":resolution,
                     "output_format":output_format}}


def nano_banana_edit(prompt, image_urls, aspect_ratio="1:1", output_format="png"):
    if aspect_ratio not in IMAGE_ASPECTS or output_format not in ("png","jpeg"):
        raise ValueError("unsupported Nano Banana Edit output parameters")
    return {"model":"google/nano-banana-edit",
            "input":{"prompt":_prompt(prompt,20000),
                     "image_urls":_images(image_urls,10,required=True),
                     "aspect_ratio":aspect_ratio,"output_format":output_format}}


@dataclass(frozen=True)
class VideoMetadata:
    """Metadata must be independently obtained from uploaded source via ffprobe."""
    size_bytes: int
    duration_seconds: float
    width: int
    height: int
    fps: float
    extension: str

    def validate(self):
        if self.extension.lower().lstrip(".") not in ("mp4","mov"):
            raise ValueError("Kling requires MP4 or MOV")
        if not (0<self.size_bytes<=200*1024*1024):
            raise ValueError("Kling video exceeds 200 MB")
        if not (3<=self.duration_seconds<=15.5):
            raise ValueError("Kling supports 3-15.5 second clips")
        if not (700<=self.width<=4553 and 700<=self.height<=4553
                and self.width*self.height<=8294400
                and .4<=self.width/self.height<=2):
            raise ValueError("Kling source dimensions/aspect unsupported")
        if not (24<=self.fps<=60):
            raise ValueError("Kling source requires 24-60 fps")


def kling_omni_transformation(prompt, video_url, metadata: VideoMetadata,
                              image_urls=(), resolution="720p", aspect_ratio=None):
    """Edit one source video; image refs are optional, not a separate I2V mode."""
    if not isinstance(metadata,VideoMetadata):
        raise ValueError("source video metadata required")
    metadata.validate()
    refs=_images(image_urls,4)
    if resolution not in ("720p","1080p","4k"):
        raise ValueError("unsupported Kling resolution")
    if aspect_ratio is None:
        aspect_ratio = "16:9" if refs else "auto"
    if aspect_ratio not in (VIDEO_RATIOS if refs else frozenset(("auto",))):
        raise ValueError("Kling aspect ratio incompatible with source mode")
    body={"prompt":_prompt(prompt,3072),"video_urls":[_url(video_url)],
          "resolution":resolution,"aspect_ratio":aspect_ratio,"audio":False}
    if refs:
        body["image_urls"]=refs
    return {"model":"kling-3.0-omni/transformation","input":body}



def kling_omni_reference_to_video(prompt, image_urls=(), *, video_url=None,
                                  metadata=None, duration=5, resolution="720p",
                                  aspect_ratio=None, audio=None,
                                  customize_multi_shots=False,
                                  prefer_multi_shots=False, multi_prompt=()):
    """Create documented Reference-to-Video payload, with no task submission.

    Image-only reference creates video from stills. Video-only and video+image
    reference follow separate API branches, with no invented I2V capability in
    the Transformation model.
    """
    images = _images(image_urls, 7 if video_url is None else 4)
    has_video = video_url is not None
    if not has_video and not images:
        raise ValueError("reference-to-video needs images or source video")
    if has_video:
        if not isinstance(metadata, VideoMetadata):
            raise ValueError("reference-video metadata required")
        metadata.validate()
    elif metadata is not None:
        raise ValueError("video metadata supplied without video")
    if type(duration) is not int or duration < 3 or duration > 15:
        raise ValueError("reference video duration must be 3-15 seconds")
    if resolution not in ("720p", "1080p", "4k"):
        raise ValueError("unsupported reference video resolution")
    if audio is None:
        audio = not has_video
    if type(audio) is not bool or (has_video and audio):
        raise ValueError("source video mode cannot synthesize audio")
    if aspect_ratio is None:
        aspect_ratio = "auto" if has_video and not images else "16:9"
    if aspect_ratio not in (frozenset(("auto",)) if has_video and not images else VIDEO_RATIOS):
        raise ValueError("reference video aspect ratio incompatible with sources")
    if type(customize_multi_shots) is not bool or type(prefer_multi_shots) is not bool:
        raise ValueError("invalid multi-shot switches")
    if customize_multi_shots and prefer_multi_shots:
        raise ValueError("custom and automatic shot planning conflict")
    if customize_multi_shots and has_video:
        raise ValueError("custom shots require image-only input")
    if not isinstance(multi_prompt, (list, tuple)):
        raise ValueError("invalid shot list")
    if customize_multi_shots:
        if not 1 <= len(multi_prompt) <= 6:
            raise ValueError("custom shot count out of range")
        shots = []
        for shot in multi_prompt:
            if not isinstance(shot, dict) or set(shot) != {"prompt", "duration"}:
                raise ValueError("invalid custom shot")
            seconds = shot["duration"]
            if type(seconds) is not int or not 1 <= seconds <= 12:
                raise ValueError("invalid custom shot duration")
            shots.append({"prompt": _prompt(shot["prompt"], 512), "duration": seconds})
        if sum(shot["duration"] for shot in shots) > 15:
            raise ValueError("custom shot sum exceeds 15 seconds")
    elif multi_prompt:
        raise ValueError("custom shots require customize_multi_shots")
    body = {"prompt": _prompt(prompt, 3072), "resolution": resolution,
            "aspect_ratio": aspect_ratio, "audio": audio}
    if has_video:
        body["video_urls"] = [_url(video_url)]
    if images or not has_video:
        body["image_urls"] = images
    if not has_video:
        if customize_multi_shots:
            body["customize_multi_shots"] = True
            body["multi_prompt"] = shots
        elif prefer_multi_shots:
            body["prefer_multi_shots"] = True
        body["duration"] = duration
    elif images:
        body["duration"] = duration
    return {"model": "kling-3.0-omni/reference-to-video", "input": body}


def extract_task_id(response):
    """Kie can return HTTP 200 even when its JSON envelope code is not 200."""
    if not isinstance(response,dict) or response.get("code")!=200:
        raise ValueError("Kie task request not accepted")
    data=response.get("data")
    task_id=data.get("taskId") if isinstance(data,dict) else None
    if not isinstance(task_id,str) or not TASK_ID.fullmatch(task_id):
        raise ValueError("Kie task response missing valid taskId")
    return task_id
