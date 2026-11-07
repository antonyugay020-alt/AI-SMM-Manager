"""AI SMM media gateway: local project assets -> uploaded refs -> durable Kie jobs.

Adapters:
  upload_file(local_path) -> HTTPS/OSS URL, using the documented File Upload API;
  query_task(task_id) -> provider's Get Task Details JSON;
  fetch_verified_file(result_url, target) -> atomic checked download.
Adapter endpoints are intentionally NOT guessed from the createTask documentation.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from PIL import Image
from kie_media_contracts import (
    _url, VideoMetadata, nano_banana_pro, nano_banana_edit,
    kling_omni_transformation, kling_omni_reference_to_video)
from kie_media_jobs import (prepare, submit, complete, recover_pending,
                            list_project_tasks, abandon_prepared)


def verified_image(path, *, limit_mb, kling=False):
    item=Path(path)
    if not item.is_file() or item.is_symlink() or not 0<item.stat().st_size<=limit_mb*1024*1024:
        raise ValueError("invalid local image source or size")
    with Image.open(item) as photo:
        if photo.format not in (("PNG","JPEG") if kling else ("PNG","JPEG","WEBP")):
            raise ValueError("image source format not supported by model")
        width,height=photo.size
        if kling and (width<300 or height<300 or not .4<=width/height<=2.5):
            raise ValueError("Kling reference image dimensions unsupported")
        photo.verify()
    return item


def verified_video(path):
    item=Path(path)
    if not item.is_file() or item.is_symlink() or item.suffix.lower() not in (".mp4",".mov"):
        raise ValueError("Kling source video must be a regular MP4/MOV")
    try:
        process=subprocess.run(["ffprobe","-v","error","-show_entries",
            "format=duration:stream=codec_type,width,height,r_frame_rate",
            "-of","json",str(item)], capture_output=True, check=True,
            timeout=20)
        metadata=json.loads(process.stdout)
        stream=next(s for s in metadata["streams"] if s.get("codec_type")=="video")
        numerator,denominator=stream["r_frame_rate"].split("/")
        fps=int(numerator)/int(denominator)
        result=VideoMetadata(item.stat().st_size,
            float(metadata["format"]["duration"]),
            int(stream["width"]),int(stream["height"]),fps,item.suffix)
        result.validate()
        return item,result
    except (OSError,subprocess.SubprocessError,KeyError,ValueError,
            StopIteration,ZeroDivisionError) as exc:
        raise ValueError("Kling source media failed ffprobe requirements") from exc


class KieMediaGateway:
    def __init__(self, db, workspace, *, upload_file, create_task,
                 query_task, fetch_verified_file=None):
        from kie_media_download import download_result
        self.db=db
        self.workspace=workspace
        self.upload_file=upload_file
        self.create_task=create_task
        self.query_task=query_task
        self.fetch_verified_file=fetch_verified_file or download_result

    def _authorized(self, project_id, filename):
        """Only source assets already registered to the launch project may leave it."""
        from project_file_boundary import reject_path_link, require_managed_file
        if not isinstance(project_id,str) or not project_id:
            raise ValueError("project owner required")
        db=self.db
        if not db.execute("SELECT 1 FROM projects WHERE id=?",(project_id,)).fetchone():
            raise ValueError("project owner not found")
        root=Path(self.workspace(project_id))
        source=Path(filename)
        reject_path_link(source)
        source=source.resolve(strict=True)
        if not source.is_file():
            raise ValueError("uploaded source is not a regular file")
        require_managed_file(root,source,("originals","references","processed"))
        authorized=db.execute("""SELECT 1 FROM assets
              WHERE project_id=? AND stored_path=?
              UNION ALL SELECT 1 FROM project_files
              WHERE project_id=? AND path=? LIMIT 1""",
              (project_id,str(source),project_id,str(source))).fetchone()
        if authorized is None:
            raise ValueError("media source is not registered to project")
        return source

    def _upload_image_refs(self, project_id, paths, *, limit, kling=False):
        validated=[verified_image(self._authorized(project_id,p),limit_mb=limit,
                                  kling=kling) for p in paths]
        return [_url(self.upload_file(image)) for image in validated]

    def prepare_image(self, project_id, *, prompt, sources, pro=True,
                      aspect_ratio="1:1",resolution="1K",output_format=None,
                      explicit_upload_consent=False):
        """No model generation; external source upload requires separate consent."""
        sources=list(sources)
        if sources and explicit_upload_consent is not True:
            raise RuntimeError("external source upload requires explicit user consent")
        # Pro supports both text-to-image and image-to-image. Nano Edit
        # requires at least one existing photograph per its OpenAPI contract.
        if len(sources)>(8 if pro else 10) or (not pro and not sources):
            raise ValueError("invalid number of Nano Banana reference sources")
        urls=self._upload_image_refs(project_id,sources,limit=30 if pro else 10)
        if pro:
            payload=nano_banana_pro(prompt, urls,aspect_ratio,resolution,
                                    output_format or "png")
        else:
            payload=nano_banana_edit(prompt, urls,aspect_ratio,
                                     output_format or "png")
        return prepare(self.db,project_id,payload)

    def prepare_video(self, project_id, *, prompt, source_video=None,
                      source_images=(), reference_mode=True,
                      duration=5, resolution="720p",aspect_ratio=None,
                      audio=None, multi_prompt=(), explicit_upload_consent=False):
        """Reference mode creates from images; transformation edits existing video."""
        images=list(source_images)
        if (images or source_video is not None) and explicit_upload_consent is not True:
            raise RuntimeError("external source upload requires explicit user consent")
        if len(images)>(7 if source_video is None else 4):
            raise ValueError("too many Kling reference images")
        if not reference_mode and source_video is None:
            raise ValueError("Transformation requires source video")
        local=metadata=None
        if source_video is not None:
            local,metadata=verified_video(self._authorized(project_id,source_video))
        urls=self._upload_image_refs(project_id,images,limit=50,kling=True)
        video_url=_url(self.upload_file(local)) if local is not None else None
        if reference_mode:
            payload=kling_omni_reference_to_video(
                prompt,urls,video_url=video_url,metadata=metadata,
                duration=duration,resolution=resolution,
                aspect_ratio=aspect_ratio,audio=audio,
                customize_multi_shots=bool(multi_prompt),
                multi_prompt=multi_prompt)
        else:
            if video_url is None:
                raise ValueError("Transformation requires source video")
            payload=kling_omni_transformation(
                prompt,video_url,metadata,urls,resolution,aspect_ratio)
        return prepare(self.db,project_id,payload)

    def start(self, project_id, job_id, *, explicit_paid_consent=False):
        return submit(self.db,project_id,job_id,self.create_task,
                      enable_paid=explicit_paid_consent)

    def refresh(self, project_id, job_id):
        return complete(self.db,project_id,job_id,self.query_task,
                        self.fetch_verified_file,self.workspace)

    def recover(self, project_id):
        return recover_pending(self.db,project_id)

    def tasks(self, project_id, *, limit=100):
        return list_project_tasks(self.db,project_id,limit=limit)

    def discard_unpaid(self, project_id, job_id):
        return abandon_prepared(self.db,project_id,job_id)
