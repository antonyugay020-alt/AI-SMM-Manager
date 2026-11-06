import unittest
from unittest.mock import patch
import generic_text_review as g

class UnavailableReviewRetry(unittest.TestCase):
    def execute(self, second):
        calls=[]
        first={'pass':False,'status':'BLOCKED','review_errors':{'factual_grounding':'unavailable'},
               'reviewer_models':{'requirements':'familyA','completeness':'familyA',
                                  'factual_grounding':'familyB','format_and_tone':'familyA'},'issues':[]}
        def reviewer(req,draft,context,**kw):
            calls.append((draft,kw.get('previous_reviewers')))
            return first if len(calls)==1 else second
        with patch.object(g,'review_text',reviewer):
            answer=g.run_quality_cycle('Создай пост',{'action':'write','answer':'Первый текст'},
                {},reviewer=reviewer)
        return answer,calls,first
    def test_secondary_independent_review_can_complete_same_draft(self):
        result,calls,initial=self.execute({'pass':True,'status':'PASS','review_errors':{},'issues':[]})
        self.assertEqual(result['action'],'write')
        self.assertEqual(result['answer'],'Первый текст')
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1][0],calls[0][0])
        self.assertEqual(calls[1][1],initial['reviewer_models'])
        self.assertEqual(result['quality_review']['attempts'],2)
        self.assertFalse(result['quality_review']['repair_applied'])
    def test_secondary_incomplete_review_cannot_approve(self):
        result,calls,_=self.execute({'pass':False,'status':'BLOCKED',
            'review_errors':{'requirements':'timeout'},'issues':[]})
        self.assertEqual(len(calls),2)
        self.assertEqual(result['action'],'write_blocked')
        self.assertFalse(result['quality_review']['pass'])
        self.assertEqual(result['answer'],'Первый текст')
