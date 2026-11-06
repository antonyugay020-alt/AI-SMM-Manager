"""Fault injection for asynchronous result ownership (no Qt import necessary)."""
import ast
import sqlite3
import tempfile
import unittest
from types import MethodType
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone


def _load_callbacks():
    source = Path(__file__).with_name('app.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    studio = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Studio')
    methods = [n for n in studio.body if isinstance(n, ast.FunctionDef) and n.name in ('vision_done', 'video_done', '_semantic_media_publication', '_semantic_media_accepted')]
    module = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
    return compile(module, '<studio-async-callbacks>', 'exec')


CALLBACKS = _load_callbacks()


class _Status:
    def __init__(self):
        self.messages = []
    def showMessage(self, message, *args):
        self.messages.append(message)


class _UI:
    def __init__(self, db):
        self.db = db
        self.current_project = 'beta'
        self.status = _Status()
        self.chat = []
        self.ready = []
        self.finished = 0
    def statusBar(self):
        return self.status
    def append_chat(self, label, content):
        self.chat.append((label, content))
    def register_ready_result(self, path, *, project_id=None):
        if project_id is not None and project_id!=self.current_project:
            raise ValueError("wrong project")
        self.ready.append(path)
    def finish_worker(self):
        self.finished += 1


class AsyncProjectOwnership(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(lambda: self.db.close())
        self.db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE messages (project_id TEXT,role TEXT,content TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE project_files (id TEXT, project_id TEXT,kind TEXT,display_name TEXT,path TEXT,created_at TEXT)')
        self.db.executemany('INSERT INTO projects VALUES (?)', [('alpha',), ('beta',)])
        self.db.commit()
        self.ui = _UI(self.db)
        from application_core import ApplicationRuntime
        self.ui.core = ApplicationRuntime(self.db, lambda pid: self.root / pid)
        context = {'Path': Path, 'sqlite3': sqlite3, 'uuid4': uuid4,
                   'now': lambda: datetime.now(timezone.utc).isoformat(),
                   'project_dir': lambda pid: self.root / pid}
        exec(CALLBACKS, context)
        self.vision = context['vision_done']
        self.video = context['video_done']
        self.ui._semantic_media_publication = MethodType(context['_semantic_media_publication'],self.ui)
        self.ui._semantic_media_accepted = MethodType(context['_semantic_media_accepted'],self.ui)
        self.alpha_dir = self.root / 'alpha' / 'processed'
        self.alpha_dir.mkdir(parents=True)
        (self.root / 'beta' / 'processed').mkdir(parents=True)
        self.file = self.alpha_dir / 'result.mp4'
        self.file.write_bytes(b'video-test')

    def test_vision_retains_original_project(self):
        self.vision(self.ui, 'verified content', project_id='alpha')
        self.assertEqual(self.db.execute('SELECT project_id FROM messages').fetchall(), [('alpha',)])
        self.assertEqual(self.ui.chat, [])
        self.assertEqual(self.ui.finished, 1)

    def test_vision_missing_project_is_blocked(self):
        self.vision(self.ui, 'content', project_id='deleted')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)
        self.assertTrue(self.ui.status.messages)
        self.assertEqual(self.ui.finished, 1)

    def test_vision_does_not_commit_other_transaction(self):
        self.db.execute('INSERT INTO messages VALUES (?,?,?,?)', ('beta', 'user', 'pending', 'now'))
        self.vision(self.ui, 'not saved', project_id='alpha')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT project_id FROM messages').fetchall(), [('beta',)])
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)

    def test_video_retains_original_project(self):
        self.video(self.ui, str(self.file), project_id='alpha')
        self.assertEqual(self.db.execute('SELECT project_id FROM project_files').fetchall(), [('alpha',)])
        self.assertEqual(self.ui.ready, [])

    def test_video_rejects_foreign_project_path(self):
        self.video(self.ui, str(self.file), project_id='beta')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0], 0)
        self.assertTrue(self.ui.status.messages)

    def test_video_rejects_symlink(self):
        alias = self.alpha_dir / 'alias.mp4'
        try:
            alias.symlink_to(self.file)
        except (OSError, NotImplementedError):
            self.skipTest('Cannot create symlinks')
        self.video(self.ui, str(alias), project_id='alpha')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0], 0)

    def test_video_rejects_linked_project_folder(self):
        (self.root / 'beta' / 'processed').rmdir()
        (self.root / 'beta').rmdir()
        try:
            (self.root / 'beta').symlink_to(self.root / 'alpha', target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Cannot create directory symlinks')
        self.video(self.ui, str(self.file), project_id='beta')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0], 0)
        self.assertTrue(self.ui.status.messages)

    def test_video_rejects_empty_file(self):
        self.file.write_bytes(b'')
        self.video(self.ui, str(self.file), project_id='alpha')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0], 0)

    def test_video_busy_sqlite_preserves_other_write(self):
        self.db.execute('INSERT INTO messages VALUES (?,?,?,?)', ('beta', 'user', 'pending', 'now'))
        self.video(self.ui, str(self.file), project_id='alpha')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM project_files').fetchone()[0], 0)
        self.db.rollback()

    def test_video_valid_current_project_updates_ui(self):
        self.ui.current_project = 'alpha'
        self.video(self.ui, str(self.file), project_id='alpha')
        self.assertEqual(self.ui.ready, [str(self.file.resolve())])
        self.assertEqual(self.db.execute('SELECT kind FROM project_files').fetchall(), [('video',)])

    def test_signals_capture_original_project(self):
        code = Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        self.assertIn('self.vision_done(response, project_id=pid)', code)
        self.assertIn('self.video_done(path, project_id=pid)', code)


if __name__ == '__main__':
    unittest.main()


def test_video_completion_without_origin_does_not_publish_into_selected_project():
    import tempfile
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as tmp:
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
            db.execute('INSERT INTO projects VALUES (?)',('beta',))
            db.commit()
            ui=_UI(db)
            ui.core=SimpleNamespace(publish_result=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('publication invoked')))
            ui._semantic_media_publication=lambda *_: (_ for _ in ()).throw(AssertionError('side effect invoked'))
            context={'Path':Path}
            exec(CALLBACKS,context)
            context['video_done'](ui,str(Path(tmp)/'video.mp4'))
            assert ui.status.messages and 'identity' in ui.status.messages[-1]
        finally:db.close()
