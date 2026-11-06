import ast
import pathlib
import unittest

class ReviewerCoverageRegression(unittest.TestCase):
    def test_every_model_in_family_is_probed(self):
        source=(pathlib.Path(__file__).parent/'windows_acceptance.py').read_text(encoding='utf-8')
        fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='reviewer_model_probe')
        call_sites=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='probe_qualification']
        self.assertGreaterEqual(len(call_sites),2)
        parent_map={child:node for node in ast.walk(fn) for child in ast.iter_child_nodes(node)}
        self.assertTrue(any(any(isinstance(p,ast.For) and isinstance(p.target,ast.Name) and p.target.id=='model' for p in iter_parents(n,parent_map)) for n in call_sites))
        self.assertIn('setdefault(_model_family(name),[]).append(name)',source)
        self.assertIn('len({_model_family(name) for name in eligible})',source)

def iter_parents(node,parents):
    while node in parents:
        node=parents[node]
        yield node

class ReviewerProbeFailureIsolation(unittest.TestCase):
    def test_failed_probe_does_not_skip_sibling_model(self):
        import importlib, sys, types
        from unittest.mock import patch
        with patch.dict(sys.modules, {
            'generic_text_review': types.SimpleNamespace(
                _model_family=lambda name: name.split(':')[0],
                list_installed_ollama_models=lambda: ['alpha:bad','alpha:good','beta:good']),
            'review_qualification': types.SimpleNamespace(
                probe_qualification=lambda model,**kw: (_ for _ in ()).throw(RuntimeError('offline')) if model=='alpha:bad' else
                    {'model':model,'pass':True,'author_response_compatible':True},
                qualification_envelope=lambda rows: {'results':rows}, CERT_ENV='TEST_REVIEW_CERT')
        }):
            import windows_acceptance as wa
            calls=[]
            with patch.object(wa,'record',side_effect=lambda *a,**kw:calls.append((a,kw))), \
                 patch.object(wa,'authors_with_review_capacity',return_value=(['alpha:good'],[])), \
                 patch.dict(wa.os.environ,{},clear=False):
                old=wa.RESULT
                try:
                    wa.RESULT={'checks':{}}
                    self.assertTrue(wa.reviewer_model_probe())
                finally:
                    wa.RESULT=old
            final=[kw for a,kw in calls if a[0]=='reviewer_models'][0]
            self.assertEqual(final['eligible'],2)
            self.assertEqual(final['calibrated'],3)
            self.assertIn('alpha:good',final['reviewer_models'])
            self.assertNotIn('alpha:bad',final['reviewer_models'])
