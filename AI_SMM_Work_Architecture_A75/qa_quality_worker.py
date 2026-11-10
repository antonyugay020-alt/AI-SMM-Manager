"""Bounded, owned independent local quality review for Windows QA.

The Qt parent never imports PyTorch, vision image processing or media codecs
through this adapter. The subprocess holds its own lease, is killed with the
owning Windows Job Object on timeout, and cannot call a paid provider.
"""
import argparse
import json
import os
from qa_receipt_provenance import make_request,validate_output_binding,validate_provenance,validate_media_metrics
from pathlib import Path
import sys
import time


def review_child(payload_path):
    from durable_json import atomic_write_json
    from local_media_quality import LocalVision,objective_quality,ObjectiveQualityRejected
    payload=json.loads(Path(payload_path).read_text(encoding='utf-8'))
    if not isinstance(payload,dict) or set(payload)!={'case','output','folder','vision_model','vision_digest','request_id','output_sha256','source_sha256','case_sha256'}:
        raise ValueError('Invalid local quality request')
    folder=Path(payload['folder']).resolve()
    output=Path(payload['output']).resolve()
    if not folder.is_dir() or not output.is_file():
        raise ValueError('Local quality inputs missing')
    validate_output_binding(payload,output,payload['case'])
    vision=LocalVision(payload['vision_model'])
    if vision.installed()!=payload['vision_digest']:
        raise ValueError('Vision model digest changed after qualification')
    try:
        metrics=objective_quality(payload['case'],output,folder)
        verdict=vision.review(payload['case'],output,folder,metrics)
        validate_output_binding(payload,output,payload['case'])
        atomic_write_json(folder/'quality.json',{
            **verdict,'metrics':metrics,'request_id':payload['request_id'],
            'output_sha256':payload['output_sha256'],
            'source_sha256':payload['source_sha256'],'case_sha256':payload['case_sha256']})
        return 0
    except ObjectiveQualityRejected as exc:
        # Quality is a per-engine verdict. Keep the exact failure evidence,
        # return normally so later independent engines are still inspected.
        # The supervisor's final status remains BLOCKED.
        validate_output_binding(payload,output,payload['case'])
        atomic_write_json(folder/'quality.json',{'status':'BLOCKED',
            'model':vision.model,'digest':vision.digest,
            'request_id':payload['request_id'],'output_sha256':payload['output_sha256'],
            'source_sha256':payload['source_sha256'],'case_sha256':payload['case_sha256'],
            'objective_rejection':str(exc)[:800],
            'scope':'failed objective media quality; not a semantic PASS'})
        return 0
    except Exception as exc:
        import traceback
        atomic_write_json(folder/'quality.json',
            {'status':'BLOCKED','error_type':type(exc).__name__,
             'reason':str(exc)[:800],'traceback':traceback.format_exc()[-8192:]})
        raise


def validate_quality_receipt(report,case,model,digest):
    """No semantic PASS without objective output proof and a complete verdict.

    The child is separately owned, but the supervisor independently checks
    that a PASS contains evidence for the relevant image/video and reference
    controls. Incomplete/uncertain responses cannot certify a Windows run.
    """
    if not isinstance(report,dict) or report.get('status') not in ('PASS','BLOCKED'):
        raise ValueError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
    if report.get('model')!=model or report.get('digest')!=digest:
        raise ValueError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
    if report['status']=='BLOCKED' and isinstance(report.get('objective_rejection'),str):
        if not report['objective_rejection'].strip():
            raise ValueError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
        return report
    metrics=report.get('metrics')
    verdict=report.get('verdict')
    if not isinstance(metrics,dict) or not metrics:
        raise ValueError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
    if not isinstance(verdict,dict) or set(verdict)!={
            'verdict','instruction','preservation','reference_or_mask',
            'temporal','observations'}:
        raise ValueError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
    required=['instruction','preservation']
    sources=case.get('sources',[])
    if isinstance(sources,list) and len(sources)>1:
        required.append('reference_or_mask')
    if case.get('task')=='video_generative':
        required.append('temporal')
        if not isinstance(metrics.get('sampled_frames'),list) or not metrics.get('sampled_frames'):
            raise ValueError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
    else:
        if (type(metrics.get('width')) is not int or type(metrics.get('height')) is not int
                or not isinstance(metrics.get('contrast'),(int,float))
                or not isinstance(metrics.get('mean_absolute_change'),(int,float))):
            raise ValueError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
    validate_media_metrics(case,metrics)
    if report['status']=='PASS':
        if (verdict.get('verdict')!='PASS'
                or any(verdict.get(k)!='PASS' for k in required)
                or any(verdict.get(k) not in ('PASS','NOT_APPLICABLE')
                       for k in ('instruction','preservation','reference_or_mask','temporal'))
                or not isinstance(verdict.get('observations'),str)
                or not 10<=len(verdict['observations'].strip())<=4000):
            raise ValueError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID')
    return report


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
        from native_process_runtime import owned_native_process,native_command,wait_owned_native
        folder=Path(folder)
        folder.mkdir(parents=True,exist_ok=True)
        payload=folder/'quality_request.json'
        output=Path(output).resolve()
        request=make_request(output,case)
        # A prior run's receipt/error must never certify this invocation.
        for name in ('quality.json','quality_process_error.json'):
            (folder/name).unlink(missing_ok=True)
        atomic_write_json(payload,{'case':case,'output':str(output),
            'folder':str(folder.resolve()),'vision_model':self.model,
            'vision_digest':self.digest,**request})
        log_path=folder/'quality_process.log'
        started=time.monotonic()
        child=None
        try:
            with log_path.open('wb') as log:
                cmd=native_command(sys.executable,Path(__file__),'--request',payload)
                with owned_native_process(cmd,env=os.environ,stdout=log,stderr=log) as process:
                    child=process
                    try:
                        exit_code=wait_owned_native(process,log,timeout=self.timeout,pump=pump)
                    except TimeoutError as exc:
                        raise TimeoutError('LOCAL_QUALITY_REVIEW_TIMEOUT') from exc
                    if exit_code!=0:
                        raise RuntimeError('LOCAL_QUALITY_REVIEW_PROCESS_FAILED')
            report=json.loads((folder/'quality.json').read_text(encoding='utf-8'))
            try:
                validate_provenance(report,request,output,case)
                return validate_quality_receipt(report,case,self.model,self.digest)
            except ValueError as exc:
                raise RuntimeError('LOCAL_QUALITY_REVIEW_RECEIPT_INVALID') from exc
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
