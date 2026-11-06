#!/usr/bin/env python3
"""Strict external-model review of a Git diff. NOT a substitute for tests or human judgment.

Providers:
  anthropic: ANTHROPIC_API_KEY and ANTHROPIC_MODEL
  compatible: ALT_REVIEW_API_KEY, ALT_REVIEW_MODEL, ALT_REVIEW_API_URL
Missing credentials or malformed verdict is BLOCKED, never PASS.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
REVIEW_SYSTEM=(
    'You are an INDEPENDENT software architecture reviewer for a Windows PySide6 AI SMM Manager. '
    'Inspect supplied code diffs, prioritize integration defects across state, SQLite, background workers, '
    'model boundaries, identity, file transactions, Qt lifecycle, recovery, and weakened tests. '
    'Do not make up defects. Respond ONLY as JSON: '
    '{"verdict":"APPROVE|REJECT|NEEDS_INFORMATION",'
    '"critical_findings":[{"file":"path","reason":"explanation"}],'
    '"confidence":"high|medium|low","notes":"short text"}. '
    'If input is incomplete, set NEEDS_INFORMATION. '
    'Never treat the tests included in the diff as proof that the system passed Windows E2E.'
)


def parse_verdict(raw: str):
    obj=json.loads(raw)
    if not isinstance(obj,dict):raise ValueError('review is not an object')
    if obj.get('verdict') not in ('APPROVE','REJECT','NEEDS_INFORMATION'):
        raise ValueError('invalid verdict')
    findings=obj.get('critical_findings')
    if not isinstance(findings,list) or len(findings)>100:
        raise ValueError('invalid findings list')
    for item in findings:
        if (not isinstance(item,dict) or not isinstance(item.get('file'),str)
                or not isinstance(item.get('reason'),str) or not item['reason'].strip()):
            raise ValueError('malformed finding')
    if obj['verdict']=='APPROVE' and findings:
        raise ValueError('approved verdict contradicts critical findings')
    if obj.get('confidence') not in ('high','medium','low'):
        raise ValueError('missing confidence')
    return obj


def request_review(provider: str, diff: str):
    if provider=='anthropic':
        key=os.environ.get('ANTHROPIC_API_KEY','')
        model=os.environ.get('ANTHROPIC_MODEL','')
        if not key or not model:raise RuntimeError('ANTHROPIC_API_KEY / ANTHROPIC_MODEL missing')
        url='https://api.anthropic.com/v1/messages'
        headers={'x-api-key':key,'anthropic-version':'2023-06-01'}
        payload={'model':model,'max_tokens':2200,'system':REVIEW_SYSTEM,
                 'messages':[{'role':'user','content':diff}]}
    elif provider=='compatible':
        key=os.environ.get('ALT_REVIEW_API_KEY','')
        model=os.environ.get('ALT_REVIEW_MODEL','')
        url=os.environ.get('ALT_REVIEW_API_URL','')
        if not key or not model or not url:raise RuntimeError('ALT_REVIEW_API_* / ALT_REVIEW_MODEL missing')
        if not url.startswith('https://'):raise RuntimeError('review API must use HTTPS')
        headers={'Authorization':'Bearer '+key}
        payload={'model':model,'temperature':0,'messages':[
            {'role':'system','content':REVIEW_SYSTEM},{'role':'user','content':diff}]}
    else:raise ValueError('unknown provider')
    request=urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),method='POST',headers={
        **headers,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=180) as response:
            body=json.loads(response.read(2_000_000).decode('utf-8'))
    except urllib.error.HTTPError as exc:
        # Do not print response body: providers occasionally echo user data or secrets.
        raise RuntimeError('provider HTTP status '+str(exc.code)) from None
    if provider=='anthropic':
        segments=body.get('content',[])
        answer='\n'.join(x['text'] for x in segments if isinstance(x,dict) and x.get('type')=='text')
    else:
        choices=body.get('choices',[])
        answer=choices[0]['message']['content'] if choices else ''
    if not isinstance(answer,str) or not answer.strip():raise ValueError('empty reviewer answer')
    return parse_verdict(answer)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider',choices=('anthropic','compatible'),required=True)
    parser.add_argument('--diff',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'ci_evidence'/'independent_review.json')
    args=parser.parse_args()
    result={'provider':args.provider,'status':'BLOCKED','reason':None,'verdict':None}
    try:
        diff=args.diff.read_text(encoding='utf-8')
        if len(diff)>160000 or not diff.strip():
            raise ValueError('empty or excessive diff; review must be subdivided')
        verdict=request_review(args.provider,diff)
        result.update({'verdict':verdict,'status':'PASS' if verdict['verdict']=='APPROVE' else 'BLOCKED'})
        if result['status']!='PASS':result['reason']='reviewer did not approve changes'
    except (OSError,ValueError,RuntimeError,urllib.error.URLError,json.JSONDecodeError) as exc:
        result['reason']=str(exc)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Don't print untrusted reviewer text or any secret.
    print(f'Independent reviewer [{args.provider}]: {result["status"]}',
          f'({result["reason"]})' if result['reason'] else '')
    return 0 if result['status']=='PASS' else 2

if __name__=='__main__':raise SystemExit(main())
