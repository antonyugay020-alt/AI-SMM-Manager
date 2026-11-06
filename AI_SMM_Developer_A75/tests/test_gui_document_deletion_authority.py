"""Qt-free proof that GUI document deletion uses the verified backup authority.

Execute the actual production handler AST without importing PySide6.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import ast
import pytest


def _handler():
    source = Path(__file__).with_name('app.py')
    syntax = ast.parse(source.read_text(encoding='utf-8'))
    matches = [method for cls in syntax.body if isinstance(cls, ast.ClassDef)
               for method in cls.body if isinstance(method, ast.FunctionDef)
               and method.name == 'delete_manager_document']
    assert len(matches) == 1
    return ast.fix_missing_locations(ast.Module(body=matches, type_ignores=[]))


def _case(outcome):
    backup = Mock(side_effect=outcome if isinstance(outcome, Exception) else None,
                  return_value=SimpleNamespace(backup=Path('/verified/backup.zip')))
    notifications = []
    message_box = SimpleNamespace(Yes=1, No=0, question=lambda *a:1,
                                  information=lambda *a:notifications.append(('info',a)),
                                  warning=lambda *a:notifications.append(('warning',a)))
    scope = {'QMessageBox': message_box}
    exec(compile(_handler(), '<production-gui-delete>', 'exec'),scope)
    view = SimpleNamespace(current_project='a'*32, active_document_id='b'*32,
        document_title=SimpleNamespace(text=lambda:'Document'),
        core=SimpleNamespace(controls=SimpleNamespace(backup_and_delete=backup)),
        load_manager_documents=Mock(),
        statusBar=lambda:SimpleNamespace(showMessage=lambda message:notifications.append(('status',message))))
    scope['delete_manager_document'](view)
    backup.assert_called_once_with('a'*32,'documents',selected_ids=('b'*32,))
    return view,notifications


def test_gui_deletion_backup_failure_preserves_selected_document():
    view,notes = _case(OSError('backup disk unavailable'))
    assert view.active_document_id == 'b'*32
    view.load_manager_documents.assert_not_called()
    assert [kind for kind,_ in notes] == ['warning']


def test_gui_deletion_success_requires_backup_receipt():
    view,notes = _case(None)
    assert view.active_document_id is None
    view.load_manager_documents.assert_called_once_with()
    assert [kind for kind,_ in notes] == ['status']
    assert '/verified/backup.zip' in notes[0][1]


def test_all_gui_document_deletion_routes_use_same_authority():
    tree = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
    gui_methods=[node for cls in tree.body if isinstance(cls,ast.ClassDef)
                 for method in cls.body if isinstance(method,ast.FunctionDef)
                 and method.name in ('delete_manager_document','execute_project_control','handle_project_delete')
                 for node in ast.walk(method) if isinstance(node,ast.Call)]
    assert any(isinstance(call.func,ast.Attribute) and call.func.attr=='backup_and_delete' for call in gui_methods)
    assert not any(isinstance(call.func,ast.Name) and call.func.id=='delete_project_data' for call in gui_methods)
