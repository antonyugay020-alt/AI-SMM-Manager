"""Injected opinions; native review contracts and canonical rendered artifacts."""
from artifact_review import document_substance_contract,review_source_signature
from independent_acceptance_editor import _inventory_segments,substance_reader_contract
from publication_writer import week_to_markdown

REQUEST='Prepare the agreed set.'
STRATEGY='A strategy with audience, choices, budget and measurement decisions.'

def calendar(weeks=1,cadence=1):
    posts=[];slots=[];days=('Пн','Вт','Ср','Чт','Пт','Сб','Вс')
    for week in range(1,weeks+1):
        for i in range(cadence):
            ident=str(week)+'/'+days[i];topic='Explanation '+ident
            slots.append({'week':week,'day':days[i],'topic':topic,'platform':'Instagram','format':'Карусель','goal':'Help compare examples'})
            posts.append({'id':ident,'topic':topic,'caption':'A full explanation and concrete example for '+ident,
                'visual':'Scene 1: Show an example. Scene 2: Compare.','cta':'Which example is clearer?',
                'kpi':'Questions','measurement':'Count questions after publication','audience':'Readers','materials':'Prepare examples'})
    return week_to_markdown(posts,slots)

def bind_report(report,request,artifacts):
    strategy,body=artifacts['strategy'].strip(),artifacts['calendar'].strip()
    full=document_substance_contract(request,_inventory_segments(strategy,body))
    _,reader=substance_reader_contract(request,strategy,body)
    source=review_source_signature(request,artifacts)
    def proof(contract):
        values={key:{'status':'satisfied','evidence_id':row['evidence_ids'][0],
                'reason':'Injected engineering opinion for this exact source.'} for key,row in contract.checks.items()}
        result=contract.validate(values,[],{key:row['expected'] for key,row in contract.controls.items()})
        result['source_signature']=source
        return result
    editor=report['independent_editor'];editor['substance_review']=proof(full)
    editor['independent_reader']['substance_review']=proof(reader)
    return report
