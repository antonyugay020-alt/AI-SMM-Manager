"""Local model profiles and content receipts; no Torch import/download on GUI."""
import hashlib
import json
import os
from pathlib import Path
from project_file_boundary import reject_path_link
from generative_contract import ENGINES


def digest(path,check=lambda:None):
    value=hashlib.sha256()
    with Path(path).open('rb') as handle:
        while block:=handle.read(1024*1024):check();value.update(block)
    return value.hexdigest()


def read_bounded(path,maximum=2*1024*1024):
    path=reject_path_link(Path(path))
    if not path.is_file() or path.stat().st_size>maximum:raise ValueError('Invalid local model metadata')
    return path.read_bytes()


def profiles(environ=None):
    env=os.environ if environ is None else environ
    filename=env.get('AI_SMM_GENERATIVE_CONFIG')
    if not filename:return {}
    data=json.loads(read_bounded(filename))
    if not isinstance(data,dict) or set(data)!={'version','profiles'} or data['version']!=1 or not isinstance(data['profiles'],dict):raise ValueError('Invalid generative configuration')
    result={}
    for name,profile in data['profiles'].items():
        if not isinstance(name,str) or not name or not isinstance(profile,dict) or set(profile) not in ({'engine','bundle','manifest_sha256','device'},{'engine','bundle','manifest_sha256','device','python'}):
            raise ValueError('Invalid installed generative profile')
        if profile['engine'] not in ENGINES.values() or profile['device'] not in ('cuda','cpu'):raise ValueError('Unsupported local generative profile')
        if 'python' in profile and (not isinstance(profile['python'],str) or not Path(profile['python']).is_absolute() or not Path(profile['python']).is_file()):raise ValueError('Installed generative Python executable required')
        root=reject_path_link(Path(profile['bundle']))
        if not root.is_absolute() or not root.is_dir():raise ValueError('Absolute installed model bundle required')
        raw=read_bounded(root/'manifest.json')
        if hashlib.sha256(raw).hexdigest()!=profile['manifest_sha256']:raise ValueError('Installed model manifest changed')
        result[name]=dict(profile)
    return result


def verify_bundle(profile,check=lambda:None):
    root=reject_path_link(Path(profile['bundle']))
    if not root.is_absolute():raise ValueError('Absolute model bundle required')
    raw=read_bounded(root/'manifest.json')
    if hashlib.sha256(raw).hexdigest()!=profile['manifest_sha256']:raise ValueError('Model manifest differs from task receipt')
    manifest=json.loads(raw)
    if not isinstance(manifest,dict) or set(manifest)!={'version','provenance','files'} or manifest['version']!=1 or not isinstance(manifest['files'],dict) or not manifest['files']:
        raise ValueError('Invalid model file manifest')
    actual=set()
    for path in root.rglob('*'):
        check();reject_path_link(path)
        if path.is_file() and path!=root/'manifest.json':actual.add(path.relative_to(root).as_posix())
    if actual!=set(manifest['files']):raise ValueError('Installed model file set changed')
    for relative,receipt in manifest['files'].items():
        check();rel=Path(relative)
        if rel.is_absolute() or '..' in rel.parts or '\\' in relative or rel.as_posix()!=relative:raise ValueError('Invalid model relative path')
        if rel.suffix.lower() in ('.bin','.pt','.pth','.pkl','.pickle','.py'):raise ValueError('Only safe local model weights/config are accepted')
        path=reject_path_link(root/rel)
        if not isinstance(receipt,dict) or set(receipt)!={'sha256','bytes'} or path.stat().st_size!=receipt['bytes'] or digest(path,check)!=receipt['sha256']:
            raise ValueError('Model weights differ from installed receipt')
    if not (root/'model'/'model_index.json').is_file():raise ValueError('Diffusers model directory missing')
    return manifest


def configure_from_database(db):
    if os.environ.get('AI_SMM_GENERATIVE_CONFIG'):return
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_generative_settings'").fetchone():return
    row=db.execute("SELECT value FROM local_generative_settings WHERE key='config_path'").fetchone()
    if row:
        try:profiles({'AI_SMM_GENERATIVE_CONFIG':row[0]})
        except (ValueError,TypeError,OSError):return
        os.environ['AI_SMM_GENERATIVE_CONFIG']=row[0]


def describe_profile(profile):
    binding=hashlib.sha256(json.dumps(profile,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'engine':profile['engine'],'manifest_sha256':profile['manifest_sha256'],'configuration_sha256':binding}
