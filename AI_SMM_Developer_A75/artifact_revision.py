"""Host-owned localized edits for arbitrary named text fields.

A model supplies replacement text only. The host owns spans, identity and
merging; all bytes outside the issued spans remain immutable. This contract
cannot approve meaning or certify completeness; independent review follows.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class RevisionSpan:
    ident: str
    field: str
    start: int
    end: int
    original: str


@dataclass(frozen=True)
class ArtifactRevisionContract:
    fields: tuple
    spans: tuple

    @classmethod
    def create(cls, fields, issues, allowed_fields):
        if not isinstance(fields,dict) or not isinstance(issues,list):
            raise ValueError('Нет исходного артефакта или замечаний')
        spans=[]
        for field in dict.fromkeys(allowed_fields):
            text=fields.get(field)
            if not isinstance(text,str) or not text.strip():
                raise ValueError('Нет исходного текстового поля '+str(field))
            ranges=[]
            for issue in issues:
                if not isinstance(issue,dict):continue
                if issue.get('field',issue.get('field_type'))!=field:continue
                quote=issue.get('quote')
                if not isinstance(quote,str) or not quote or quote not in text:
                    raise ValueError('Замечание не связано с текущим полем')
                if text.count(quote)!=1:
                    ranges=[(0,len(text))];break
                start=text.index(quote);ranges.append((start,start+len(quote)))
            if not ranges:ranges=[(0,len(text))]
            merged=[]
            for start,end in sorted(ranges):
                if merged and start<=merged[-1][1]:
                    merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
                else:merged.append((start,end))
            for start,end in merged:
                spans.append(RevisionSpan(f'P{len(spans)+1:03d}',field,start,end,text[start:end]))
        if not spans:raise ValueError('Нет разрешённых фрагментов для исправления')
        return cls(tuple(fields.items()),tuple(spans))

    def payload(self):
        return [{'id':s.ident,'field':s.field,'original':s.original} for s in self.spans]

    def schema(self):
        return {'type':'object','additionalProperties':False,'required':['replacements'],
            'properties':{'replacements':{'type':'object','additionalProperties':False,
                'required':[s.ident for s in self.spans],
                'properties':{s.ident:{'type':'string','minLength':1} for s in self.spans}}}}

    def apply(self, value):
        if not isinstance(value,dict) or set(value)!={'replacements'}:
            raise ValueError('Ожидались только replacements')
        replacements=value['replacements']
        if not isinstance(replacements,dict) or set(replacements)!={s.ident for s in self.spans}:
            raise ValueError('Правка содержит невыданный или пропущенный фрагмент')
        result=dict(self.fields)
        for span in sorted(self.spans,key=lambda s:(s.field,-s.start)):
            text=replacements[span.ident]
            if not isinstance(text,str) or not text.strip() or not any(c.isalnum() for c in text):
                raise ValueError('Правка не может удалять содержимое без замены')
            if text==span.original:raise ValueError('Правка не изменила отклонённый фрагмент')
            body=result[span.field]
            result[span.field]=body[:span.start]+text+body[span.end:]
        return result
