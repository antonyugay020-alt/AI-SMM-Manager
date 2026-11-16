#!/usr/bin/env python3
"""Explicit free model installer; dry-run unless --download. No remote Python/pickle.

Pinned official HF revisions. FP16 component files keep identical bytes and are
stored under standard safetensors names so one offline loader supports all four
engines without guessing variants. No model is downloaded by app startup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.request import urlopen

REPOS={
 'instruct':('timbrooks/instruct-pix2pix','31519b5cb02a7fd89b906d88731cd4d6a7bbf88d'),
 'base':('stable-diffusion-v1-5/stable-diffusion-v1-5','451f4fe16113bff5a5d2269ed5ad43b0592e9a14'),
 'inpaint':('stable-diffusion-v1-5/stable-diffusion-inpainting','8a4288a76071f7280aedbdb3253bdb9e9d5d84bb'),
 'motion':('guoyww/animatediff-motion-adapter-v1-5-2','6167b88ffe39b4441fdf2113e77b99a6f56b7906'),
 'ip':('h94/IP-Adapter','018e402774aeeddd60609b4ecdb7e298259dc729')}
ENGINES=('instruct_pix2pix','sd15_ip_adapter','sd15_inpaint','animatediff_v2v')


def plan(engine):
    roles=[('model','instruct' if engine=='instruct_pix2pix' else 'inpaint' if engine=='sd15_inpaint' else 'base')]
    if engine=='animatediff_v2v':roles.append(('motion','motion'))
    if engine in ('sd15_ip_adapter','animatediff_v2v'):roles.append(('ip_adapter','ip'))
    result=[]
    for folder,key in roles:
        repo,revision=REPOS[key]
        with urlopen('https://huggingface.co/api/models/'+repo+'/revision/'+revision+'?blobs=true',timeout=60) as response:meta=json.load(response)
        if meta['sha']!=revision:raise ValueError('Model revision changed')
        for item in meta['siblings']:
            source=item['rfilename']
            if key=='ip':
                selected=source in ('README.md','LICENSE','LICENSE.md','LICENSE.txt','models/ip-adapter_sd15.safetensors','models/image_encoder/config.json','models/image_encoder/model.safetensors')
            else:
                selected=(source in ('README.md','LICENSE','LICENSE.md','LICENSE.txt') or source=='model_index.json' or (source.endswith('.json') and not source.startswith(('onnx/','openvino/')))
                    or source.startswith('tokenizer/') and source.endswith(('.json','.txt'))
                    or source.endswith('.fp16.safetensors'))
            if not selected:continue
            destination=folder+'/'+source.replace('.fp16.safetensors','.safetensors')
            result.append({'repository':repo,'revision':revision,'source':source,'destination':destination,
                           'bytes':item['size'],'sha256':item.get('lfs',{}).get('sha256'),'git_blob':item.get('blobId')})
    if not result:raise ValueError('No safe model files')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',choices=ENGINES,required=True);parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--python',dest='runtime_python',required=True,type=Path)
    parser.add_argument('--download',action='store_true');args=parser.parse_args()
    if not args.runtime_python.is_absolute() or not args.runtime_python.is_file():raise ValueError('Existing separate Python executable required')
    files=plan(args.engine);total=sum(f['bytes'] for f in files)
    print(json.dumps({'engine':args.engine,'download_bytes':total,'file_count':len(files),'download_authorized':args.download},indent=2))
    if not args.download:return 0
    destination=args.destination.absolute()
    if destination.exists():raise ValueError('Destination must not exist; existing weights are never overwritten')
    config=destination.with_name(destination.name+'-profiles.json')
    if config.exists():raise ValueError('Refusing to overwrite an existing profile configuration')
    if any(p.is_symlink() for p in (destination,*destination.parents)):raise ValueError('Model destination cannot pass through symlinks')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(destination.parent).free<total+256*1024*1024:raise ValueError('Insufficient disk space for verified bundle')
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root=Path(temporary)/'bundle';root.mkdir();receipts={}
        for item in files:
            target=root/item['destination'];target.parent.mkdir(parents=True,exist_ok=True)
            h=hashlib.sha256();git=hashlib.sha1(('blob '+str(item['bytes'])+'\0').encode());size=0
            url='https://huggingface.co/'+item['repository']+'/resolve/'+item['revision']+'/'+item['source']
            with urlopen(url,timeout=120) as response,target.open('wb') as output:
                while block:=response.read(1024*1024):output.write(block);h.update(block);git.update(block);size+=len(block)
            if size!=item['bytes'] or (h.hexdigest()!=item['sha256'] if item['sha256'] else git.hexdigest()!=item['git_blob']):raise ValueError('Downloaded file differs from pinned metadata')
            receipts[item['destination']]={'sha256':h.hexdigest(),'bytes':size}
        manifest={'version':1,'provenance':{'engine':args.engine,'files':files},'files':receipts}
        raw=(json.dumps(manifest,indent=2)+'\n').encode();(root/'manifest.json').write_bytes(raw)
        os.replace(root,destination)
    profile={'engine':args.engine,'bundle':str(destination),'manifest_sha256':hashlib.sha256(raw).hexdigest(),
             'device':'cuda','python':str(args.runtime_python)}
    config=destination.with_name(destination.name+'-profiles.json')
    if config.exists():raise ValueError('Refusing to overwrite an existing profile configuration')
    config.write_text(json.dumps({'version':1,'profiles':{destination.name:profile}},indent=2)+'\n')
    print(json.dumps({'installed_bundle':str(destination),'profile_configuration':str(config)}))
    return 0


if __name__=='__main__':raise SystemExit(main())
