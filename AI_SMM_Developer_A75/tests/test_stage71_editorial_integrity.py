"""Stage 71: protect structural success from misleading editorial approval."""
import json
import unittest
from pathlib import Path
from editorial_integrity import content_consistency_issues
from semantic_editor import POLICY_VERSION, audit_post
from stage60_quality import quality_warnings
from version_info import APP_VERSION, STAGE_NUMBER, BUILD_LABEL
from publication_writer import week_to_markdown


class EditorialIntegrityTests(unittest.TestCase):
    def post(self, **updates):
        p=dict(id='1/Вт', topic='Принципы ухода за материалами',
            visual='Экран 1: показать рабочее место. Экран 2: показать этапы ухода. Экран 3: вопрос зрителям.',
            caption=('В работе с материалами важно понимать происхождение и условия применения. '
                     'Сначала проверьте требования изготовителя и состояние вещи. '
                     'Затем выберите способ обработки, применимый к этому материалу, '
                     'и протестируйте его на незаметном участке, если это допускается. '
                     'Если инструкция неполная, уточните рекомендации перед обработкой.'),
            cta='Сохраните памятку.', kpi='Количество сохранений', audience='Новички',
            measurement='Сохранения в статистике', materials='Макеты и фотографии')
        p.update(updates);return p

    def test_reject_growth_goal_without_provided_baseline(self):
        req=('Цены, адрес и статистика предприятия не предоставлены; '
             'не выдавай догадки за достоверные сведения.')
        p=self.post(kpi='Вовлечённость: +15%')
        self.assertTrue(any('исходная статистика' in x for x in content_consistency_issues(p, {'format':'Reels'}, req)))

    def test_avoid_false_positive_with_known_baseline_or_hypothesis(self):
        p=self.post(kpi='Увеличить сохранения на 10%')
        self.assertFalse(content_consistency_issues(p,{'format':'Reels'},'Исходное число сохранений известно и предоставлено.'))
        p['kpi']='Гипотеза: рост сохранений на 10% после замера базы'
        self.assertFalse(content_consistency_issues(p,{'format':'Reels'},'Статистика не предоставлена.'))

    def test_stories_reels_mismatch(self):
        p=self.post(caption='В этом Reel расскажем о важном шаге.'+self.post()['caption'])
        issues=content_consistency_issues(p,{'format':'Stories'},'')
        self.assertIn('Текст Stories ошибочно описывает публикацию как Reels', issues)
        p=self.post(caption='В этой сторис расскажем о важном шаге.'+self.post()['caption'])
        self.assertTrue(content_consistency_issues(p,{'format':'Reels'},''))

    def test_stories_wrong_measurement_and_no_blanket_comment_ban(self):
        p=self.post(cta='Ответьте в комментариях',measurement='Количество комментариев в посте')
        self.assertTrue(content_consistency_issues(p,{'format':'Stories'},''))
        p['measurement']='Ответы зрителей в Stories'
        self.assertFalse(content_consistency_issues(p,{'format':'Stories'},''))

    def test_unconfirmed_site_claim(self):
        p=self.post(cta='Запишитесь на нашем сайте')
        self.assertTrue(content_consistency_issues(p,{'format':'Reels'},'Сайта нет.'))
        self.assertFalse(content_consistency_issues(p,{'format':'Reels'},'Сайт есть и подтверждён.'))

    def test_format_native_short_copy_is_not_rejected_for_character_count(self):
        p=self.post(caption='Проверьте инструкцию производителя перед обработкой.')
        stories=dict(week=1,day='Вт',platform='Instagram',format='Stories',goal='Полезно')
        reel=dict(stories, format='Reels')
        md1='## Неделя 1\n'+week_to_markdown([p],[stories])
        md2='## Неделя 1\n'+week_to_markdown([p],[reel])
        self.assertFalse(any('Недостаточно развёрнутые' in w for w in quality_warnings(md1)))
        self.assertFalse(any('Недостаточно развёрнутые' in w for w in quality_warnings(md2)))

    def test_editor_prompt_contains_cross_post_topics_and_proofreading(self):
        p=self.post()
        captures=[]
        def ask(model,system,user):
            captures.append((system,json.loads(user)))
            return json.dumps({'approved':True,'issues':[]})
        reviewed,errors=audit_post(ask,'mock','Новая студия',{},
              {'format':'Reels','topic':p['topic']},p,
              ['Ранее утверждённая тема: Секреты ухода за материалом'])
        self.assertEqual(errors,[])
        self.assertEqual(reviewed,p)
        self.assertIn('реальной последовательностью',captures[0][0])
        self.assertIn('Секреты ухода за материалом',captures[0][1]['other_approved_topics'])

    def test_central_version(self):
        self.assertTrue(APP_VERSION.startswith(f'{STAGE_NUMBER}.'))
        self.assertIn(f'Stage {STAGE_NUMBER} · v{APP_VERSION}',BUILD_LABEL)
        source=Path(__file__).parent
        if not (source/'app.py').is_file():source=source.parent
        self.assertIn('BUILD_LABEL', (source/'app.py').read_text(encoding='utf-8'))
        self.assertEqual(POLICY_VERSION,'substantive_delivery_r82')


if __name__=='__main__':unittest.main()
