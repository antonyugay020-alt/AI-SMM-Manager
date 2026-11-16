import sys,pathlib,unittest,copy,tempfile,json,hashlib
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'AI_SMM_Developer_A75'/'tests'),str(ROOT/'AI_SMM_Work_Architecture_A75')]
from test_windows_acceptance_v4 import FinalEditorialGateTests
from production_pipeline import run_document_task

class HistoryTest(unittest.TestCase):
 def test_prior_decision_is_preserved_after_reapproval_and_reopen(self):
  initial=FinalEditorialGateTests.prepared()
  signature=hashlib.sha256(b'history-retention-check').hexdigest()
  initial['task_signature']=signature
  with tempfile.TemporaryDirectory() as tmp:
   state={'project_workspace':tmp}
   def executor(req,ctx,progress=None): return copy.deepcopy(initial)
   reviewers=[lambda *a,**k:{'pass':False,'issues':[{'document':'calendar','type':'quality','quote':'Missing','reason':'Incomplete'}]},lambda *a,**k:{'pass':True,'issues':[]}]
   with patch('production_pipeline.run_compound_with_retry',side_effect=lambda ex,req,s,**kw:ex(req,s)):
    rejected=run_document_task('Create two publications',state,execute=executor,strategy_reviewer=lambda *a,**k:{'pass':True,'issues':[]},final_reviewer=reviewers[0])
    accepted=run_document_task('Create two publications',state,execute=executor,strategy_reviewer=lambda *a,**k:{'pass':True,'issues':[]},final_reviewer=reviewers[1])
   drafts=pathlib.Path(tmp)/'drafts'
   current=json.loads((drafts/f'content_delivery_{signature}.json').read_text(encoding='utf-8'))
   history=list((drafts/'content_delivery_history'/signature).glob('*.json'))
   self.assertEqual(len(history),1)
   self.assertEqual(json.loads(history[0].read_text(encoding='utf-8')),rejected['content_delivery'])
   self.assertEqual(current,accepted['content_delivery'])
   self.assertEqual(current['independent_editor']['status'],'PASS')
if __name__=='__main__':unittest.main(verbosity=2)
