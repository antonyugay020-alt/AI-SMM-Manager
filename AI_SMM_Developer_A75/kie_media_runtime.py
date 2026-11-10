"""AI SMM media gateway: local project assets -> uploaded refs -> durable Kie jobs.

Adapters:
  upload_file(local_path) -> HTTPS/OSS URL, using the documented File Upload API;
  query_task(task_id) -> provider's Get Task Details JSON;
  fetch_verified_file(result_url, target) -> atomic checked download.
Adapter endpoints are intentionally NOT guessed from the createTask documentation.
"""
from __future__ import annotations

import json
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
        if width<=0 or height<=0 or width>16000 or height>16000 or width*height>40_000_000:
            raise ValueError("local image pixel budget exceeded")
        if kling and (width<300 or height<300 or not .4<=width/height<=2.5):
            raise ValueError("Kling reference image dimensions unsupported")
        # Decode pixels before upload: a valid-looking header is not sufficient.
        try:
            photo.load()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("local image cannot be fully decoded") from exc
    return item


def verified_video(path, *, check=None):
    if check is not None:check()
    item=Path(path)
    if not item.is_file() or item.is_symlink() or item.suffix.lower() not in (".mp4",".mov"):
        raise ValueError("Kling source video must be a regular MP4/MOV")
    try:
        from media_subprocess_runtime import run_owned_media_process
        code,output,_=run_owned_media_process(
            ["ffprobe","-v","error","-show_entries",
             "format=duration:stream=codec_type,width,height,r_frame_rate",
             "-of","json",str(item)],check,timeout=20,capture_stdout=True)
        if code:raise ValueError("nonzero ffprobe exit")
        if check is not None:check()
        metadata=json.loads(output)
        stream=next(s for s in metadata["streams"] if s.get("codec_type")=="video")
        numerator,denominator=stream["r_frame_rate"].split("/")
        fps=int(numerator)/int(denominator)
        result=VideoMetadata(item.stat().st_size,
            float(metadata["format"]["duration"]),
            int(stream["width"]),int(stream["height"]),fps,item.suffix)
        result.validate()
        return item,result
    except (OSError,TimeoutError,KeyError,ValueError,
            StopIteration,ZeroDivisionError) as exc:
        raise ValueError("Kling source media failed ffprobe requirements") from exc


class KieMediaGateway:
    def __init__(self, db, workspace, *, upload_file, create_task,
                 query_task, fetch_verified_file=None,
                 cost_gate=None, verified_price_quote=None):
        from kie_media_download import download_result
        self.db=db
        self.workspace=workspace
        self.upload_file=upload_file
        self.create_task=create_task
        self.query_task=query_task
        self.fetch_verified_file=fetch_verified_file or download_result
        self.cost_gate=cost_gate
        self.verified_price_quote=verified_price_quote

    def _authorized(self, project_id, filename):
        from project_media_sources import registered_source
        return registered_source(self.db,self.workspace,project_id,filename)

    def _upload_image_refs(self, project_id, paths, *, limit, kling=False):
        validated=[verified_image(self._authorized(project_id,p),limit_mb=limit,
                                  kling=kling) for p in paths]
        return [_url(self.upload_file(image)) for image in validated]

    def prepare_image(self, project_id, *, prompt, sources, pro=True,
                      aspect_ratio="1:1",resolution="1K",output_format=None,
                      explicit_upload_consent=False,job_id=None,intent_sha256=None):
        """No model generation; external source upload requires separate consent."""
        sources=list(sources)
        if sources and explicit_upload_consent is not True:
            raise RuntimeError("external source upload requires explicit user consent")
        # Pro supports both text-to-image and image-to-image. Nano Edit
        # requires at least one existing photograph per its OpenAPI contract.
        if len(sources)>(8 if pro else 10) or (not pro and not sources):
            raise ValueError("invalid number of Nano Banana reference sources")
        # Validate the complete request before transmitting any source bytes.
        # Preview URLs are never stored or submitted; they exercise the same
        # public contract as the final provider-upload references.
        preview_urls=["https://preflight.invalid/source" for _ in sources]
        if pro:
            nano_banana_pro(prompt,preview_urls,aspect_ratio,resolution,
                            output_format or "png")
        else:
            nano_banana_edit(prompt,preview_urls,aspect_ratio,
                             output_format or "png")
        urls=self._upload_image_refs(project_id,sources,limit=30 if pro else 10)
        if pro:
            payload=nano_banana_pro(prompt, urls,aspect_ratio,resolution,
                                    output_format or "png")
        else:
            payload=nano_banana_edit(prompt, urls,aspect_ratio,
                                     output_format or "png")
        binding={'intent_sha256':intent_sha256} if intent_sha256 is not None else {}
        return prepare(self.db,project_id,payload,job_id=job_id,**binding)

    def prepare_video(self, project_id, *, prompt, source_video=None,
                      source_images=(), reference_mode=True,
                      duration=5, resolution="720p",aspect_ratio=None,
                      audio=None, multi_prompt=(), explicit_upload_consent=False,job_id=None,
                      cancel_check=None,intent_sha256=None):
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
            local,metadata=verified_video(self._authorized(project_id,source_video),check=cancel_check)
        # Fail invalid prompt/model settings before any external upload.
        preview_images=["https://preflight.invalid/photo" for _ in images]
        preview_video="https://preflight.invalid/video" if local is not None else None
        if reference_mode:
            kling_omni_reference_to_video(
                prompt,preview_images,video_url=preview_video,metadata=metadata,
                duration=duration,resolution=resolution,aspect_ratio=aspect_ratio,
                audio=audio,customize_multi_shots=bool(multi_prompt),
                multi_prompt=multi_prompt)
        else:
            kling_omni_transformation(prompt,preview_video,metadata,
                                      preview_images,resolution,aspect_ratio)
        if cancel_check is not None:cancel_check()
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
        binding={'intent_sha256':intent_sha256} if intent_sha256 is not None else {}
        return prepare(self.db,project_id,payload,job_id=job_id,**binding)

    def start(self, project_id, job_id, *, explicit_paid_consent=False,
              approval_token=None):
        """Reserve an independently quoted maximum before any paid network I/O.

        The reservation remains committed until verified provider billing is
        reconciled. A taskId is NOT evidence of a final provider charge.
        """
        if explicit_paid_consent is not True:
            raise RuntimeError("explicit paid media consent required")
        if self.cost_gate is None or self.verified_price_quote is None or not approval_token:
            raise RuntimeError("verified price, durable budget gate and one-use approval required")
        from kie_media_jobs import _owned_row
        # Reject foreign project transactions before reserving a paid charge.
        if self.db.in_transaction:
            raise RuntimeError("foreign SQLite transaction in progress")
        row=_owned_row(self.db,project_id,job_id)
        if row[3]!="prepared":
            raise RuntimeError("Kie media task is not an unpaid draft")
        # Historical paid allocations cannot be silently ignored by a
        # newer, separate durable ledger. Require manual reconciliation.
        legacy=self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='kie_media_allocations'").fetchone()
        if legacy and self.db.execute(
                "SELECT 1 FROM kie_media_allocations LIMIT 1").fetchone():
            raise RuntimeError("legacy Kie paid allocations require reconciliation")
        quote=self.verified_price_quote(row[0],json.loads(row[1]))
        reservation=self.cost_gate.reserve(
            "kie.ai","media:"+job_id,quote,token=approval_token,unique_operation=True)
        # If the process dies now, the ledger retains the reservation.
        # Never auto-release on error: the provider may have charged.
        return submit(self.db,project_id,job_id,self.create_task,
                      enable_paid=True,payment_reservation=reservation,
                      cost_gate=self.cost_gate)

    def refresh(self, project_id, job_id, *, cancel_check=None):
        return complete(self.db,project_id,job_id,self.query_task,
                        self.fetch_verified_file,self.workspace,cancel_check=cancel_check)

    def recover(self, project_id):
        return recover_pending(self.db,project_id)

    def tasks(self, project_id, *, limit=100):
        return list_project_tasks(self.db,project_id,limit=limit)

    def discard_unpaid(self, project_id, job_id):
        return abandon_prepared(self.db,project_id,job_id)
