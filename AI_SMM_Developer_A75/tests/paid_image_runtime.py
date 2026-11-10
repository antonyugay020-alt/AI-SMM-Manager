"""Exact legacy OpenAI image request binding for the shared durable cost gate."""
import hashlib
import json
from pathlib import Path
from project_file_boundary import reject_path_link


def image_operation(project_id,task_id,source,reference,prompt):
    if not project_id or not task_id:raise ValueError('Image project/task owner required')
    fingerprints=[]
    for filename in (source,reference):
        if filename is None:
            fingerprints.append(None);continue
        path=reject_path_link(Path(filename))
        if not path.is_file():raise ValueError('Image input missing')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        fingerprints.append(digest.hexdigest())
    value=json.dumps(['gpt-image-1',fingerprints,prompt],ensure_ascii=False,separators=(',',':'))
    return 'image:'+str(project_id)+':'+str(task_id)+':'+hashlib.sha256(value.encode()).hexdigest()
