import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from media_result_ownership import commit_owned_image
from project_operation_guard import project_operation_busy


class LocalMediaOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE project_files(id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,name TEXT,path TEXT,created_at TEXT)')
        for pid in ('a','b'):
            self.db.execute('INSERT INTO projects VALUES(?)',(pid,))
            (self.root/pid/'processed').mkdir(parents=True)
        self.db.commit()

    def img(self,pid,name='out.png'):
        file=self.root/pid/'processed'/name
        Image.new('RGB',(6,6)).save(file)
        return file

    def count(self,pid):
        return self.db.execute('SELECT COUNT(*) FROM project_files WHERE project_id=?',(pid,)).fetchone()[0]

    def test_owner_is_launch_project_not_visible_project(self):
        expected=self.img('a')
        path=commit_owned_image(self.db,'a',expected,expected,self.root/'a')
        self.assertEqual(path,str(expected.resolve()))
        self.assertEqual((self.count('a'),self.count('b')),(1,0))

    def test_bogus_other_project_output_is_rejected(self):
        expected=self.img('a')
        foreign=self.img('b')
        with self.assertRaises(ValueError):commit_owned_image(self.db,'a',expected,foreign,self.root/'a')
        self.assertEqual((self.count('a'),self.count('b')),(0,0))

    def test_duplicate_completion_is_rejected(self):
        expected=self.img('a')
        commit_owned_image(self.db,'a',expected,expected,self.root/'a')
        with self.assertRaises(ValueError):commit_owned_image(self.db,'a',expected,expected,self.root/'a')
        self.assertEqual(self.count('a'),1)

    def test_corrupt_image_rejected(self):
        expected=self.root/'a'/'processed'/'out.png'
        expected.write_bytes(b'not a PNG')
        with self.assertRaises((ValueError,OSError)):commit_owned_image(self.db,'a',expected,expected,self.root/'a')
        self.assertEqual(self.count('a'),0)

    def test_symlink_result_rejected(self):
        outside=self.img('b')
        expected=self.root/'a'/'processed'/'out.png'
        try:
            expected.symlink_to(outside)
        except OSError as exc:
            if getattr(exc, 'winerror', None) in (5,1314):
                self.skipTest('Windows symlink privilege not available')
            raise
        with self.assertRaises(ValueError):commit_owned_image(self.db,'a',expected,expected,self.root/'a')
        self.assertEqual(self.count('a'),0)

    def test_uncommitted_state_cannot_be_counted_as_saved(self):
        expected=self.img('a')
        self.db.execute('INSERT INTO projects VALUES(?)',('pending',))
        with self.assertRaises(RuntimeError):commit_owned_image(self.db,'a',expected,expected,self.root/'a')
        self.db.rollback()
        self.assertEqual(self.count('a'),0)

    def test_worker_blocking_includes_generator(self):
        class View:pass
        view=View();view.generator=object()
        self.assertTrue(project_operation_busy(view))

    def test_gui_handlers_bind_owner_before_launch(self):
        source=Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        studio=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='Studio')
        funcs={n.name:ast.unparse(n) for n in studio.body if isinstance(n,ast.FunctionDef)}
        self.assertIn('self._generator_owner = (self.current_project, str(output))',funcs['generate_image'])
        self.assertIn('self._edit_owner = (self.current_project, str(output))',funcs['process_image'])
        for method in ('generate_image', 'process_image'):
            self.assertIn('project_operation_busy(self)',funcs[method])
        for handler,attribute in [('generation_done','_generator_owner'),('image_done','_edit_owner')]:
            self.assertIn('commit_owned_image',funcs[handler])
            self.assertIn('pid, expected',funcs[handler])
            self.assertIn('if self.current_project == pid',funcs[handler])
            self.assertNotIn('uuid4()',funcs[handler])

if __name__=='__main__':unittest.main()
