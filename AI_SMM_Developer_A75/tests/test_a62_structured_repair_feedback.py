import json
import unittest

from publication_writer import repair_fields, repair_field_targets


class StructuredRepairFeedbackTests(unittest.TestCase):
    def test_final_review_claim_and_provenance_reach_publication_field_repair(self):
        finding={
            'document':'calendar',
            'type':'invented_fact',
            'quote':'В ателье мы подгоняем одежду, чтобы она всегда была комфортной.',
            'claim':'В ателье мы подгоняем одежду, чтобы она всегда была комфортной.',
            'reason':'Не подтверждён фактический процесс/метод или действие компании.',
            'evidence_id':'calendar:0011',
            'field_role':'Текст/ключевые реплики',
            'section_path':['Контент-план','Неделя 1'],
        }
        post={
            'id':'1/Пт','topic':'Как проходит подгонка одежды',
            'visual':'Три слайда с вопросами для владельца ателье.',
            'caption':finding['quote'],'cta':'Уточните детали процесса.',
            'kpi':'Число вопросов','audience':'Клиенты ателье',
            'measurement':'Подсчёт вопросов','materials':'Подтвердить этапы подгонки.',
        }
        slot={'week':1,'day':'Пт','topic':post['topic']}
        self.assertIn('caption',repair_field_targets([finding]))
        seen={}

        def ask(model, system, user):
            seen['system']=system
            seen['payload']=json.loads(user)
            return json.dumps({'replacements':{'P001':'Какие детали подгонки одежды важно обсудить заранее?'}})

        repaired=repair_fields(ask,'model','Запрос ателье',{},'',slot,post,[finding])
        feedback=seen['payload']['rejection_reasons'][0]
        self.assertIn(finding['quote'],feedback)
        self.assertIn(finding['evidence_id'],feedback)
        self.assertIn(finding['field_role'],feedback)
        self.assertEqual(repaired['caption'],'Какие детали подгонки одежды важно обсудить заранее?')
        self.assertEqual(repaired['topic'],post['topic'])


if __name__=='__main__':
    unittest.main()
