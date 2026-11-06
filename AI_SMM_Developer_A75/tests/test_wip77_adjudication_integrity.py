import unittest
from semantic_editor import audit_post_with_independent_model

class AdjudicationIntegrityTests(unittest.TestCase):
    def run_case(self, replacement):
        post={'caption':'original text'}
        issue={'field':'caption','quote':'original text','reason':'needs change'}
        def audit(ask,model,request,facts,slot,post,previous_posts=()):
            return None,[issue]
        def adjudicate(*args,**kwargs):
            return replacement,[issue]
        return audit_post_with_independent_model(lambda *a:None,'author',
            ['reviewer1','reviewer2'],'request',{}, {},post,
            audit_function=audit,adjudicate_function=adjudicate)
    def test_duplicate_finding_is_not_valid_adjudication(self):
        with self.assertRaisesRegex(ValueError,'Арбитраж изменил'):
            self.run_case([{'field':'caption','quote':'original text','reason':'needs change'}])
    def test_mutated_finding_is_not_valid_adjudication(self):
        with self.assertRaisesRegex(ValueError,'Арбитраж изменил'):
            self.run_case([{'field':'caption','quote':'original text','reason':'different'}])

if __name__=='__main__':unittest.main()
