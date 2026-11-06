"""Fault-injection at the *real* Qt result application and dialogue message methods.

No live Qt required; extract unmodified app methods and use ProjectConnection,
which is the production connection factory. These specifically cover the former
claim=None result path that bypassed the global transaction.
"""
import ast
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from dialogue_execution import ProjectConnection

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'app.py'

def method(name):
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    entries=[m for node in tree.body if isinstance(node,ast.ClassDef)
             for m in node.body if isinstance(m,ast.FunctionDef) and m.name==name]
    assert len(entries)==1
    code=compile(ast.fix_missing_locations(ast.Module(body=entries, type_ignores=[])),str(SOURCE),'exec')
    namespace={'sqlite3':sqlite3, 'now':lambda:'now', 'uuid4':__import__('uuid').uuid4, 'available_tools':lambda:{}}
    exec(code,namespace)
    return namespace[name]

class ArchitectureResultBoundaries(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:',factory=ProjectConnection)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE t(value TEXT)')
        self.db.execute('CREATE TABLE ai_pending_tasks(project_id TEXT, request TEXT, question TEXT)')
        self.db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        self.db.execute("INSERT INTO projects VALUES('p')")
        self.db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,project_id TEXT,role TEXT,content TEXT,created_at TEXT)')
        self.db.commit()
        self.calls=[]
        from conversation_runtime import ConversationRuntime
        self.view=SimpleNamespace(core=SimpleNamespace(conversations=ConversationRuntime(self.db)),
            db=self.db,current_project='p',task_project_id='p',
            task_instruction='request',task_execution_claim=None,task_semantic_kind=None,
            _pending_ai_tasks={},_result_effects=None,
            load_manager_documents=lambda:self.calls.append('reload'),
            append_chat=lambda *x:self.calls.append(('chat',x)),
            _apply_task_result_body=self.body_success)

    def body_success(self,task):
        self.db.execute('INSERT INTO t VALUES(?)',(task['action'],))
        self.db.commit()  # legacy helper commit must be suppressed by outer boundary

    def test_legacy_result_is_atomic_not_autocommitted(self):
        method('_apply_task_result')(self.view,{'action':'respond','instruction':'request'})
        self.assertEqual(self.db.execute('SELECT value FROM t').fetchall(),[('respond',)])
        self.assertFalse(self.db.in_transaction)
        self.assertIsNone(self.view._result_effects)
        self.assertEqual(self.db._result_depth,0)

    def test_late_failure_rolls_back_both_legacy_helper_commits(self):
        def fail(task):
            self.db.execute("INSERT INTO t VALUES('first')");self.db.commit()
            self.db.execute("INSERT INTO t VALUES('second')");self.db.commit()
            raise RuntimeError('second document failed')
        self.view._apply_task_result_body=fail
        with self.assertRaisesRegex(RuntimeError,'second document'):
            method('_apply_task_result')(self.view,{'action':'respond','instruction':'request'})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM t').fetchone()[0],0)
        self.assertEqual(self.db._result_depth,0)
        self.assertFalse(self.db.in_transaction)

    def test_foreign_transaction_is_preserved_without_applying_result(self):
        self.db.execute("INSERT INTO t VALUES('foreign')")
        with self.assertRaisesRegex(ValueError,'предыдущей транзакции'):
            method('_apply_task_result')(self.view,{'action':'respond','instruction':'request'})
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT value FROM t').fetchall(),[('foreign',)])
        self.db.rollback()

    def test_missing_semantic_claim_is_blocked(self):
        self.view.task_semantic_kind='content'
        with self.assertRaisesRegex(ValueError,'контракт'):
            method('_apply_task_result')(self.view,{'action':'write','instruction':'request'})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM t').fetchone()[0],0)

    def test_missing_compound_claim_is_blocked(self):
        with self.assertRaisesRegex(ValueError,'контракт'):
            method('_apply_task_result')(self.view,{'action':'compound_documents','instruction':'request'})

    def test_mismatched_request_is_blocked_before_any_write(self):
        with self.assertRaisesRegex(ValueError,'исходному заданию'):
            method('_apply_task_result')(self.view,{'action':'respond','instruction':'other'})
        self.assertFalse(self.db.in_transaction)

    def test_missing_origin_is_blocked_before_any_write(self):
        self.view.task_project_id=None
        with self.assertRaisesRegex(ValueError,'Исходный проект'):
            method('_apply_task_result')(self.view,{'action':'respond','instruction':'request'})

    def test_project_switch_is_blocked(self):
        self.view.current_project='other'
        with self.assertRaisesRegex(ValueError,'Исходный проект'):
            method('_apply_task_result')(self.view,{'action':'respond','instruction':'request'})

    def test_failed_result_restores_existing_pending_after_local_state_mutation(self):
        self.db.execute('INSERT INTO ai_pending_tasks VALUES(?,?,?)',('p','older','resume me'))
        self.db.commit()
        self.view._pending_ai_tasks={'p':{'request':'in-memory wrong','question':'bad'}}
        def fail(task):
            self.view._pending_ai_tasks['p']={'request':'new','question':'lost'}
            self.db.execute('DELETE FROM ai_pending_tasks')
            self.db.commit()
            raise RuntimeError('failure')
        self.view._apply_task_result_body=fail
        with self.assertRaises(RuntimeError):
            method('_apply_task_result')(self.view,{'action':'respond','instruction':'request'})
        self.assertEqual(self.view._pending_ai_tasks['p'],{'request':'older','question':'resume me'})
        self.assertEqual(self.db.execute('SELECT request FROM ai_pending_tasks').fetchone(),('older',))

    def test_deferred_effects_only_run_after_successful_commit(self):
        def effects(task):
            self.db.execute("INSERT INTO t VALUES('approved')")
            self.view._result_effects.append(lambda:self.calls.append('external_effect'))
            self.db.commit()
        self.view._apply_task_result_body=effects
        method('_apply_task_result')(self.view,{'action':'respond','instruction':'request'})
        self.assertEqual(self.calls,['external_effect'])
        self.assertIsNone(self.view._result_effects)

    def test_deferred_effects_not_run_after_failure(self):
        def effects(task):
            self.db.execute("INSERT INTO t VALUES('tentative')")
            self.view._result_effects.append(lambda:self.calls.append('external_effect'))
            raise RuntimeError('fail')
        self.view._apply_task_result_body=effects
        with self.assertRaises(RuntimeError):
            method('_apply_task_result')(self.view,{'action':'respond','instruction':'request'})
        self.assertNotIn('external_effect',self.calls)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM t').fetchone()[0],0)

    def test_dialogue_reply_never_commits_foreign_transaction(self):
        self.db.execute("INSERT INTO t VALUES('foreign')")
        with self.assertRaisesRegex(RuntimeError,'transaction'):
            method('_dialogue_reply')(self.view,'reply')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.db.rollback()

    def test_dialogue_reply_commit_failure_rolls_back_own_write(self):
        self.db.execute('CREATE TRIGGER bad BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,"fault"); END')
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            method('_dialogue_reply')(self.view,'reply')
        self.assertFalse(self.db.in_transaction)
        self.assertFalse(any(x[0]=='chat' for x in self.calls if isinstance(x,tuple)))

    def test_dialogue_reply_success_stores_before_gui_display(self):
        seen=[]
        def observe(*args):
            seen.append(self.db.execute('SELECT content FROM messages').fetchone()[0]);seen.append(self.db.in_transaction)
        self.view.append_chat=observe
        method('_dialogue_reply')(self.view,'reply')
        self.assertEqual(seen,['reply',False])

    def test_dialogue_intake_foreign_transaction_never_committed(self):
        import conversation_runtime
        original=conversation_runtime.update_intake
        def should_not_run(*args):
            self.fail('Foreign transaction must block intake before callback')
        conversation_runtime.update_intake=should_not_run
        self.addCleanup(lambda:setattr(conversation_runtime,'update_intake',original))
        errors=[]
        self.view._resolved_dialogue=None
        self.view._dialogue_error=None
        self.view.task_dialogue_turn_id='turn'
        self.view.send_button=SimpleNamespace(setEnabled=lambda _:None)
        self.view.task_failed=lambda x:errors.append(x)
        self.db.execute("INSERT INTO t VALUES('foreign')")
        method('_apply_dialogue_finished')(self.view)
        self.assertEqual(self.db.execute('SELECT value FROM t').fetchall(),[('foreign',)])
        self.assertTrue(self.db.in_transaction)
        self.assertTrue(any('SQLite' in e for e in errors),errors)
        self.db.rollback()

    def test_dialogue_intake_partial_failure_rolls_back_own_write(self):
        import conversation_runtime
        original=conversation_runtime.update_intake
        def failed_write(connection,*args):
            connection.execute("INSERT INTO t VALUES('partial-intake')")
            raise sqlite3.IntegrityError('intake fault')
        conversation_runtime.update_intake=failed_write
        self.addCleanup(lambda:setattr(conversation_runtime,'update_intake',original))
        errors=[]
        self.view._resolved_dialogue=None
        self.view._dialogue_error=None
        self.view.task_dialogue_turn_id='turn'
        self.view.send_button=SimpleNamespace(setEnabled=lambda _:None)
        self.view.task_failed=lambda x:errors.append(x)
        method('_apply_dialogue_finished')(self.view)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM t').fetchone()[0],0)
        self.assertFalse(self.db.in_transaction)
        self.assertTrue(any('intake fault' in e for e in errors),errors)

    def _message_view(self):
        input_value=['Do the task']
        clears=[];statuses=[]
        self.view.message=SimpleNamespace(toPlainText=lambda:input_value[0],
            clear=lambda:clears.append('cleared'))
        self.view.ai_status=SimpleNamespace(setText=lambda value:statuses.append(value))
        self.view.save_document_if_changed=lambda:True
        self.view.source=SimpleNamespace(path=None)
        self.view.reference=SimpleNamespace(path=None)
        self.view.active_document_id=None
        self.view.active_section='chat'
        self.view.task_failed=lambda text:self.calls.append(('failed',text))
        return clears,statuses

    def test_incoming_task_and_intake_are_one_atomic_operation(self):
        import conversation_runtime
        original=conversation_runtime.record_intake
        def partially_failed_receipt(connection,project,message_id,turn_id,msg):
            self.assertEqual(connection.execute('SELECT content FROM messages').fetchone()[0],'Do the task')
            raise sqlite3.IntegrityError('receipt fault')
        conversation_runtime.record_intake=partially_failed_receipt
        self.addCleanup(lambda:setattr(conversation_runtime,'record_intake',original))
        clears,statuses=self._message_view()
        method('send_message')(self.view)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.assertEqual(clears,[])
        self.assertTrue(any('Не удалось сохранить' in value for value in statuses))

    def test_incoming_task_never_commits_another_operation(self):
        clears,statuses=self._message_view()
        self.db.execute("INSERT INTO t VALUES('other transaction')")
        method('send_message')(self.view)
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT value FROM t').fetchone()[0],'other transaction')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.assertEqual(clears,[])
        self.db.rollback()

    def test_snapshot_failure_keeps_durable_intake_for_recovery(self):
        import dialogue_runtime
        original=dialogue_runtime.dialogue_snapshot
        def failed_snapshot(*args,**kwargs):
            raise ValueError('snapshot read failed')
        dialogue_runtime.dialogue_snapshot=failed_snapshot
        self.addCleanup(lambda:setattr(dialogue_runtime,'dialogue_snapshot',original))
        clears,statuses=self._message_view()
        method('send_message')(self.view)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT content FROM messages').fetchone()[0],'Do the task')
        intake=self.db.execute('SELECT original_request,state FROM ai_task_intake').fetchone()
        self.assertEqual(intake,('Do the task','received'))
        self.assertEqual(clears,['cleared'])
        self.assertTrue(any('snapshot read failed' in entry[1] for entry in self.calls
                            if isinstance(entry,tuple) and entry[0]=='failed'),self.calls)

if __name__=='__main__':unittest.main()
