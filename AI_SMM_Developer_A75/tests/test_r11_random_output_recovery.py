"""Cross-format strategy boundary regressions, independent of business domain."""
import unittest

from strategy_integrity import separate_embedded_publication_section, strategy_integrity_issues


class RandomOutputBoundaryRecoveryTests(unittest.TestCase):
    def assert_calendar_copy_removed(self, source, *copy_markers, **kept_markers):
        cleaned, edits = separate_embedded_publication_section(source)
        self.assertEqual(len(edits), 1)
        for marker in copy_markers:
            self.assertNotIn(marker, cleaned)
        for marker in kept_markers.get('keep', ()):
            self.assertIn(marker, cleaned)
        self.assertIsNone(__import__('strategy_integrity')._embedded_post_scope(cleaned))

    def test_emoji_headings_and_modifier_order_are_handled_as_one_calendar_section(self):
        source=(
            '# Strategy\n\n## Audience and goals\nAudience segments and content objective.\n\n'
            '## 📌 Контент-план\n### 📌 Публикация 1\n#### Полный уникальный текст:\nCOPY_ALPHA\n'
            '### 📝 Публикация 2\n#### Уникальный полный текст:\nCOPY_BETA\n\n'
            '## Risks\nUnknown service claims must be checked.\n')
        self.assert_calendar_copy_removed(source,'COPY_ALPHA','COPY_BETA',
                                          keep=('## Audience and goals','## Risks'))

    def test_bare_numbered_post_blocks_are_removed_without_losing_later_strategy(self):
        source=(
            'Strategy for a service\n\n1. 📌 Публикация 1:\n'
            '   - Полный текст: COPY_GAMMA\n   - CTA: ask a question\n'
            '2. 📝 Пост 2:\n   - Уникальный полный текст публикации: COPY_DELTA\n'
            '   - CTA: discuss the topic\n\nКонтент-матрица:\n| topic | format |\n'
            'Ресурсы и измерение:\nTrack comments and questions.\n')
        self.assert_calendar_copy_removed(source,'COPY_GAMMA','COPY_DELTA',
                                          keep=('Контент-матрица:','Ресурсы и измерение:'))

    def test_only_repeated_ready_copy_is_removed(self):
        source=('## Content ideas\n1. Публикация 1: тема про выбор\n'
                '2. Публикация 2: тема про уход\n## Measurement\nCount relevant questions.\n')
        cleaned,edits=separate_embedded_publication_section(source)
        self.assertEqual(edits,[])
        self.assertEqual(cleaned,source)
        self.assertEqual(strategy_integrity_issues(cleaned,'task',per_week=2),[])


if __name__ == '__main__':
    unittest.main()
