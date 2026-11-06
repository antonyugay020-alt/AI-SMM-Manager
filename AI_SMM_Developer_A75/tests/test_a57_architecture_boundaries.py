import unittest
from claim_support import _is_planning_metadata
from independent_acceptance_editor import _editor_evidence_index, _inventory_segments
from strategy_integrity import normalize_unprovided_category

class A57ArchitectureBoundaries(unittest.TestCase):
 def test_shared_field_evidence_units(self):
  strategy='# Overview\n- **Audience**: adult city-bike owners\n'
  calendar=('| Week/day | Platform and format | Topic | Goal | Visual scenario | Caption | CTA | KPI | Audience | Measurement | Materials |\n' '|---|---|---|---|---|---|---|---|---|---|---|\n' '| 1/Tue | Instagram, Carousel | Brake questions | Explain service | Future staged diagram | The workshop replaces brake pads. | Ask a question | Count questions | Riders | Count comments | Draft graphic |\n')
  catalog,render=_editor_evidence_index(strategy,calendar); inventory=_inventory_segments(strategy,calendar)
  self.assertEqual(set(catalog),{x['evidence_id'] for x in inventory})
  segments=[x for x in inventory if x['document']=='calendar']
  self.assertTrue({'Caption','Visual scenario','Materials'} <= {x['field_role'] for x in segments})
  self.assertIn('The workshop replaces brake pads.',{x['content'] for x in segments})
  self.assertIn('"field": "Caption"',render('calendar')); self.assertIn('"row": "1/Tue"',render('calendar'))
 def test_audience_and_kpi_are_planning_metadata(self):
  for x in ({'quote':'- **Аудитория**: взрослые владельцы велосипедов','field_role':'Аудитория'},{'quote':'- **Измеримые KPI**: просмотры','field_role':'Измеримые KPI'}): self.assertTrue(_is_planning_metadata(x))
 def test_unprovided_category_is_omitted(self):
  draft='**Категория:** Ремонт велосипедов\n'; request='Диагностика и замена колодок.'
  result,edits=normalize_unprovided_category(draft,request); self.assertEqual(result,'\n'); self.assertEqual(len(edits),1)
  source,edits=normalize_unprovided_category('**Категория:** Веломастерская','Категория: Веломастерская'); self.assertEqual(source,'**Категория:** Веломастерская'); self.assertFalse(edits)
if __name__=='__main__': unittest.main()
