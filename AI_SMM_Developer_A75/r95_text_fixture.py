"""Offline opinions, native source contracts; never evidence of live model quality."""
from artifact_review import standalone_substance_contract,review_source_signature
from generic_text_review import CHECKS,validate_verdict

def text_review(request,draft):
    assignments=dict(zip(CHECKS,['fixture_editor','fixture_editor','fixture_source','fixture_editor']))
    review=validate_verdict({'pass':True,'checks':dict.fromkeys(CHECKS,True),'issues':[],
        'review_errors':{},'reviewer_models':assignments},draft)
    contract=standalone_substance_contract(request,draft.strip())
    values={key:{'status':'satisfied','evidence_id':row['evidence_ids'][0],
        'reason':'Injected engineering judgment on this exact source.'} for key,row in contract.checks.items()}
    review.update(author_model='fixture_writer',
        source_signature=review_source_signature(request,{'standalone':draft}),
        substance_review=contract.validate(values,[],{key:row['expected'] for key,row in contract.controls.items()}))
    return review
