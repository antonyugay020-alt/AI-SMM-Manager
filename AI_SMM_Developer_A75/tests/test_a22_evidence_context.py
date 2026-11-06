import unittest

from evidence_context import build_evidence_context, coerce_evidence_context
from factual_claim_audit import scan_unsupported_business_claims
from project_knowledge import load_project_knowledge
import sqlite3
from contextlib import closing


class EvidenceContextArchitectureTests(unittest.TestCase):
    def test_only_explicitly_verified_materials_become_fact_sources(self):
        request = 'Подготовь материалы для проекта.'
        context = {
            'existing_documents': [{'kind': 'strategy', 'content': 'AI draft says 20 years.'}],
            'verified_project_materials': [
                {'title': 'About', 'content': 'Компания работает с 2012 года.', 'verified': True},
                {'title': 'Unreviewed', 'content': 'У нас 1000 клиентов.', 'verified': False},
            ],
        }
        ledger = build_evidence_context(request, context)
        rendered = ledger.render()
        self.assertIn('Компания работает с 2012 года.', rendered)
        self.assertNotIn('20 years', rendered)
        self.assertNotIn('1000 клиентов', rendered)

    def test_unknowns_and_conflicts_are_explicit_and_never_authorize_claims(self):
        ledger = build_evidence_context('Разработай план.',
            {'unknowns': ['Цены не сообщены'], 'fact_conflicts': ['Разные адреса в источниках']})
        prompt = ledger.render()
        self.assertIn('ЯВНО НЕИЗВЕСТНО', prompt)
        self.assertIn('КОНФЛИКТУЮЩИЕ ИСТОЧНИКИ', prompt)
        self.assertNotIn('Цены не сообщены', '\n'.join(s.content for s in ledger.sources))

    def test_context_is_request_bound_and_refuses_silent_cross_task_reuse(self):
        ledger = build_evidence_context('Задание A.')
        with self.assertRaisesRegex(ValueError, 'другому запросу'):
            coerce_evidence_context(ledger.to_dict(), 'Задание B.')

    def test_verified_project_evidence_reaches_the_shared_claim_gate(self):
        request = 'Подготовь план для компании.'
        doc = 'У нас работает комфортная обстановка для клиентов.'
        ledger = build_evidence_context(request, {'verified_materials': [
            {'title': 'Подтверждение владельца', 'text': doc, 'user_confirmed': True}]})
        findings = scan_unsupported_business_claims(request, doc, '', ledger)
        self.assertEqual(findings, [])

    def test_unverified_prior_ai_draft_does_not_authorize_a_claim(self):
        request = 'Подготовь план для компании.'
        doc = 'Наши занятия проходят комфортно для клиентов.'
        ledger = build_evidence_context(request, {'existing_documents': [
            {'kind': 'strategy', 'content': doc, 'valid': True}]})
        findings = scan_unsupported_business_claims(request, doc, '', ledger)
        self.assertTrue(findings)

    def test_project_materials_are_retrieved_for_new_tasks_without_reupload(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE projects (id TEXT, description TEXT)')
            db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, project_id TEXT, role TEXT, content TEXT, created_at TEXT)')
            db.execute('CREATE TABLE manager_documents (id TEXT, project_id TEXT, kind TEXT, title TEXT, content TEXT, updated_at TEXT)')
            db.execute('INSERT INTO projects VALUES (?,?)', ('p', 'Проект, описание от владельца'))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?)', (1,'p','user','Услуга проводится только по записи.','1'))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?)', (2,'p','assistant','Публикуйте по вторникам.','2'))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?)', (3,'p','user','Новое задание.','3'))
            db.execute('INSERT INTO manager_documents VALUES (?,?,?,?,?,?)',
                       ('d','p','strategy','Сохранённая стратегия','Существующая цель проекта.','4'))
            result = load_project_knowledge(db,'p',current_message='Новое задание.')
            self.assertEqual(len(result['verified_project_materials']), 2)
            self.assertIn('Услуга проводится только по записи.',
                          [s['content'] for s in result['verified_project_materials']])
            self.assertEqual(len(result['existing_documents']), 1)
            self.assertEqual(result['existing_documents'][0]['authority'], 'prior_project_document_context_only')
            self.assertNotIn('Публикуйте по вторникам.',
                             [s['content'] for s in result['verified_project_materials']])

    def test_the_same_fact_ledger_reaches_strategy_and_final_review(self):
        from production_pipeline import run_document_task
        import test_windows_acceptance_v4 as fixtures
        material={'title':'Подтверждение','content':'Срок запуска не задан.',
                  'verified':True,'source_type':'owner_confirmation'}
        seen=[]
        def strategy_review(request, strategy, *, evidence_context=None):
            seen.append(evidence_context)
            return {'pass':True,'issues':[]}
        def final_review(request, strategy, calendar, *, evidence_context=None):
            seen.append(evidence_context)
            return {'pass':True,'issues':[]}
        def execute(request, context, progress=None):
            self.assertIn('Срок запуска не задан.', context['evidence_context']['sources'][-1]['content'])
            context['strategy_reviewer']('candidate strategy')
            return fixtures.FinalEditorialGateTests.prepared()
        result=run_document_task('Создай материалы',
            {'verified_project_materials':[material]},execute=execute,
            strategy_reviewer=strategy_review,final_reviewer=final_review)
        self.assertEqual(result['content_delivery']['independent_editor']['status'],'PASS')
        self.assertEqual(len(seen),2)
        self.assertEqual(seen[0],seen[1])


if __name__ == '__main__':
    unittest.main()
