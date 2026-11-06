import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from strategy_recovery import produce_checked_strategy


class A50ValidatedBoundaryRecoveryTests(unittest.TestCase):
    def test_saved_rejected_draft_separates_scope_before_model_repair(self):
        draft=('```markdown\n# Strategy\n\n## Goals\nExplain the confirmed service.\n\n'
               '## Контент-план (2 публикации)\n### Публикация 1\n'
               '#### Уникальный полный текст:\nPost one.\n### Публикация 2\n'
               '#### Уникальный полный текст:\nPost two.\n\n## Risks\nVerify unknown facts.\n```')
        repaired=[]; checkpoints=[]
        def forbidden_repair(*args):
            repaired.append(args)
            self.fail('Scope separation must be evaluated before asking the model to repair')
        result=produce_checked_strategy('Create strategy and calendar',2,
            lambda *a:self.fail('A persisted rejected draft must not be regenerated'),
            lambda request,text:(True,''),existing=None,repair=forbidden_repair,
            draft={'content':draft,'reason':'ready post copy was embedded in the strategy'},
            checkpoint=lambda content,valid,reason:checkpoints.append((content,valid,reason)))
        self.assertTrue(result[1],result)
        self.assertNotIn('Post one',result[0])
        self.assertNotIn('Публикация 1',result[0])
        self.assertIn('## Goals',result[0])
        self.assertIn('## Risks',result[0])
        self.assertEqual(repaired,[])
        self.assertTrue(checkpoints)
        self.assertTrue(checkpoints[-1][1])
        self.assertNotIn('Post two',checkpoints[-1][0])


if __name__=='__main__':
    unittest.main()
