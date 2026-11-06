"""Adversarial GUI callback tests: cross-project identity and transaction ownership."""
import ast
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

APP=Path(__file__).with_name('app.py')

def get_method(name, globals_=None):
    tree=ast.parse(APP.read_text(encoding='utf-8'))
    studio=next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='Studio')
    methods=[m for m in studio.body if isinstance(m,ast.FunctionDef) and m.name==name]
    assert len(methods)==1
    env=dict(globals_ or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])),str(APP),'exec'),env)
    return env[name]

class _Widget:
    def __init__(self):self.records=[]
    def setEnabled(self,*a):self.records.append(a)
    def setText(self,*a):self.records.append(a)
    def setVisible(self,*a):self.records.append(a)

class GuiCallbackBoundaries(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE messages(project_id TEXT,role TEXT,content TEXT,created_at TEXT)')
        self.db.execute('CREATE TABLE sentinel(value TEXT)')
        self.db.executemany('INSERT INTO projects VALUES(?)',[('alpha',),('beta',)])
        self.db.commit()
        self.chats=[];self.finishes=[]
        self.ui=SimpleNamespace(db=self.db,current_project='beta',task_project_id='alpha',
           task_worker=object(),send_button=_Widget(),ai_status=_Widget(),
           technical_log=_Widget(),log_toggle=_Widget(),
           append_chat=lambda *a:self.chats.append(a),
           record_task_lifecycle=lambda *a,**kw:None,
           finish_worker=lambda:self.finishes.append(True))
        from application_core import ApplicationRuntime
        self.ui.core=ApplicationRuntime(self.db,lambda pid:Path('/tmp')/pid)
        self.env={'sqlite3':sqlite3,'now':lambda:'now'}

    def _begin_other_txn(self):
        self.db.execute('INSERT INTO sentinel VALUES(?)',('keep pending',))
        self.assertTrue(self.db.in_transaction)

    def test_failed_worker_does_not_commit_other_transaction(self):
        self.ui.current_project='alpha'
        self._begin_other_txn()
        get_method('task_failed',self.env)(self.ui,'worker rejected')
        self.assertTrue(self.db.in_transaction,'Failure report committed unrelated work')
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT * FROM sentinel').fetchall(),[])
        self.assertEqual(self.db.execute('SELECT * FROM messages').fetchall(),[])

    def test_ai_completion_does_not_attach_to_active_foreign_project(self):
        get_method('ai_done',self.env)(self.ui,'draft')
        self.assertEqual(self.db.execute('SELECT project_id FROM messages').fetchall(),[('alpha',)])
        self.assertEqual(self.finishes,[True])
        self.assertFalse(any('draft' in ' '.join(map(str,c)) and 'AI' in c for c in self.chats))

    def test_ai_completion_does_not_commit_other_transaction(self):
        self._begin_other_txn()
        get_method('ai_done',self.env)(self.ui,'not accepted')
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT * FROM sentinel').fetchall(),[])
        self.assertEqual(self.db.execute('SELECT * FROM messages').fetchall(),[])

    def test_result_never_applies_into_a_different_project_without_a_claim(self):
        self.ui.task_execution_claim=None
        self.ui._apply_task_result_body=lambda task:self.finishes.append('wrong project')
        with self.assertRaisesRegex(ValueError, 'проект|Проект'):
            get_method('_apply_task_result',self.env)(self.ui, {'action':'compound_documents'})
        self.assertEqual(self.finishes,[])

    def test_result_never_applies_into_a_different_project_with_claim(self):
        self.ui.task_execution_claim=SimpleNamespace(project_id='alpha')
        self.ui._apply_task_result_body=lambda task:self.finishes.append('wrong project')
        with self.assertRaisesRegex(ValueError, 'проект|Проект'):
            get_method('_apply_task_result',self.env)(self.ui, {'action':'compound_documents'})
        self.assertEqual(self.finishes,[])

    def test_failed_worker_records_failure_when_database_is_available(self):
        self.ui.current_project='alpha'
        get_method('task_failed',self.env)(self.ui,'worker rejected')
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT project_id,role FROM messages').fetchall(), [('alpha','assistant')])

    def test_ai_completion_records_into_origin_when_active_project_matches(self):
        self.ui.current_project='alpha'
        get_method('ai_done',self.env)(self.ui,'final text')
        self.assertEqual(self.db.execute('SELECT project_id,content FROM messages').fetchall(),[('alpha','final text')])
        self.assertIn(('AI','final text'),self.chats)

    def test_dialogue_finished_refuses_mismatched_project_before_intake_commit(self):
        self.ui.task_dialogue_turn_id='turn1'
        self.ui._resolved_dialogue=SimpleNamespace(project_id='alpha')
        self.ui._dialogue_error=None
        self.ui.dialogue_worker=object()
        self.ui.task_failed=lambda reason:self.chats.append(('BLOCKED',reason))
        get_method('_apply_dialogue_finished',self.env)(self.ui)
        self.assertTrue(any('Проект' in x[1] for x in self.chats if x[0]=='BLOCKED'))
        self.assertEqual(self.db.execute('SELECT * FROM messages').fetchall(),[])

    def test_project_switch_clears_pending_destructive_command(self):
        noop=lambda *args,**kwargs:None
        widget=SimpleNamespace(clear=noop,hide=noop,show_file=noop,setText=noop)
        self.ui._switching_project=False
        self.ui._pending_project_delete={'scope':'documents','query':'last'}
        self.ui.save_document_if_changed=lambda:True
        self.ui.project_ids=['beta','alpha']
        self.ui.projects=SimpleNamespace(currentIndex=lambda:0,setCurrentIndex=noop,blockSignals=lambda *x:False)
        self.ui.assets=widget
        self.ui.source=widget
        self.ui.mask_canvas=SimpleNamespace(set_image=noop)
        self.ui.result=widget
        self.ui.reference=widget
        self.ui.reference_panel=widget
        self.ui.source_list=widget
        self.ui.source_count=widget
        self.ui.result_list=widget
        self.ui.text_editor=widget
        self.ui.chat=widget
        self.ui.heading=widget
        self.ui.load_manager_documents=noop
        self.ui.show_section=noop
        self.ui.working_sources=[]
        self.ui.asset_paths=[]
        module=types.ModuleType('project_operation_guard')
        module.project_operation_busy=lambda instance:False
        previous=sys.modules.get('project_operation_guard')
        sys.modules['project_operation_guard']=module
        try:get_method('select_project',self.env)(self.ui,-1)
        finally:
            if previous is None:sys.modules.pop('project_operation_guard',None)
            else:sys.modules['project_operation_guard']=previous
        self.assertIsNone(self.ui.current_project)
        self.assertIsNone(self.ui._pending_project_delete)

    def test_project_control_cannot_commit_other_transaction(self):
        self.ui._pending_project_delete={'scope':'assets'}
        self.ui.current_project='alpha'
        self._begin_other_txn()
        module=types.ModuleType('project_control')
        module.parse_command=lambda msg:None
        module.choose_documents=lambda *a:[]
        previous=sys.modules.get('project_control')
        sys.modules['project_control']=module
        try:
            self.assertTrue(get_method('handle_project_control',self.env)(self.ui,'отмена'))
        finally:
            if previous is None:sys.modules.pop('project_control',None)
            else:sys.modules['project_control']=previous
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT * FROM sentinel').fetchall(),[])
        self.assertEqual(self.db.execute('SELECT * FROM messages').fetchall(),[])

if __name__=='__main__': unittest.main()


def test_ai_completion_without_origin_never_falls_back_to_current_project():
    db=sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO projects VALUES (?)',('beta',))
        db.commit()
        from application_core import ApplicationRuntime
        calls=[]
        ui=SimpleNamespace(current_project='beta',task_project_id=None,
            core=ApplicationRuntime(db, lambda pid:Path('/tmp')/pid),
            append_chat=lambda *args:calls.append(args),finish_worker=lambda:calls.append(('finished',)))
        get_method('ai_done')(ui,'late response')
        assert not db.execute('SELECT 1 FROM sqlite_master WHERE name="messages"').fetchone()
        assert ('finished',) in calls
        assert any('Missing immutable AI worker project identity' in str(part)
                   for item in calls for part in item)
    finally:db.close()
