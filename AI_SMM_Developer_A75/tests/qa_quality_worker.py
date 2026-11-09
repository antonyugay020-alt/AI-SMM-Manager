"""Bounded, owned independent local quality review for Windows QA.

The Qt parent never imports PyTorch, vision image processing or media codecs
through this adapter. The subprocess holds its own lease, is killed with the
owning Windows Job Object on timeout, and cannot call a paid provider.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def review_child(payload_path):
    from durable_json import atomic_write_json
    from local_media_quality import LocalVision,objective_quality
    payload=json.loads(Path(payload_path).read_text(encoding='utf-8'))
    if not isinstance(payload,dict) or set(payload)!={'case','output','folder','vision_model','vision_digest'}:
        raise ValueError('Invalid local quality request')
    folder=Path(payload['folder']).resolve()
    output=Path(payload['output']).resolve()
    if not folder.is_dir() or not output.is_file():
        raise ValueError('Local quality inputs missing')
    vision=LocalVision(payload['vision_model'])
    if vision.installed()!=payload['vision_digest']:
        raise ValueError('Vision model digest changed after qualification')
    try:
        metrics=objective_quality(payload['case'],output,folder)
        verdict=vision.review(payload['case'],output,folder,metrics)
        atomic_write_json(folder/'quality.json',{'metrics':metrics,**verdict})
        return 0
    except Exception as exc:
        import traceback
        atomic_write_json(folder/'quality.json',
            {'status':'BLOCKED','error_type':type(exc).__name__,
             'reason':str(exc)[:800],'traceback':traceback.format_exc()[-8192:]})
        raise


class BoundedQualityReview:
    """Run qualified vision+objective checks with finite Qt-friendly ownership."""
    def __init__(self,model,digest,*,timeout=900):
        if not isinstance(model,str) or not model.strip():
            raise ValueError('Installed vision model required')
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Qualified vision digest required')
        if not isinstance(timeout,(int,float)) or not 0<timeout<=3600:
            raise ValueError('Invalid quality review deadline')
        self.model=model
        self.digest=digest
        self.timeout=timeout

    def run_with_pump(self,case,output,folder,*,pump):
        from durable_json import atomic_write_json
        from native_process_runtime import owned_native_process,native_command
        folder=Path(folder)
        folder.mkdir(parents=True,exist_ok=True)
        payload=folder/'quality_request.json'
        atomic_write_json(payload,{'case':case,'output':str(Path(output).resolve()),
            'folder':str(folder.resolve()),'vision_model':self.model,'vision_digest':self.digest})
        log_path=folder/'quality_process.log'
        started=time.monotonic()
        child=None
        try:
            with log_path.open('wb') as log:
                cmd=native_command(sys.executable,Path(__file__),'--request',payload)
                with owned_native_process(cmd,env=os.environ,stdout=log,stderr=log) as process:
                    child=process
                    while process.poll() is None:
                        if time.monotonic()-started>self.timeout:
                            raise TimeoutError('LOCAL_QUALITY_REVIEW_TIMEOUT')
                        pump()
                        time.sleep(.03)
                    if process.returncode!=0:
                        raise RuntimeError('LOCAL_QUALITY_REVIEW_PROCESS_FAILED')
            report=json.loads((folder/'quality.json').read_text(encoding='utf-8'))
            if (not isinstance(report,dict) or report.get('status') not in ('PASS','BLOCKED')
                    or report.get('model')!=self.model or report.get('digest')!=self.digest):
                if report.get('status')=='BLOCKED' and report.get('model') is None:
                    # Objective metric errors fail closed rather than masquerading
                    # as a completed semantic reviewer verdict.
                    raise RuntimeError('LOCAL_QUALITY_OBJECTIVE_CHECK_FAILED')
                raise RuntimeError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
            return report
        except BaseException as exc:
            # On timeout, owned_native_process already closed the Windows
            # Job Object, terminating only this QA child and its descendants.
            tail=''
            try:
                with log_path.open('rb') as stream:
                    size=stream.seek(0,2)
                    stream.seek(max(0,size-16384))
                    tail=stream.read().decode('utf-8','replace')[-8192:]
            except OSError:
                pass
            atomic_write_json(folder/'quality_process_error.json',
                {'error_type':type(exc).__name__,'reason':str(exc)[:500],
                 'pid':getattr(child,'pid',None),'log_tail':tail,
                 'elapsed_s':round(time.monotonic()-started,3)})
            raise


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',required=True)
    args=parser.parse_args()
    return review_child(args.request)


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        import traceback
        print(json.dumps({'error_type':type(exc).__name__,
            'reason':str(exc)[:300],'traceback':traceback.format_exc()[-8192:]}),
            file=sys.stderr,flush=True)
        raise SystemExit(2)
