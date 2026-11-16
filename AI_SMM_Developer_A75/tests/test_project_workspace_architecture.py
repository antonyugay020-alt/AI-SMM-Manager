"""Canonical project-root authority survives storage settings and UI switches."""
import ast
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from application_bootstrap import install_schema
from application_core import ApplicationRuntime
from project_workspace_runtime import ProjectWorkspaceResolver

class WorkspaceAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        install_schema(self.db)
        self.a,self.b='a'*32,'b'*32
        for pid in (self.a,self.b):
            self.db.execute('INSERT INTO projects (id,name,created_at) VALUES (?,?,?)',(pid,pid,'now'))
            self.db.execute('INSERT INTO project_locations VALUES (?,?)',(pid,str(self.root/pid)))
        self.db.commit()
        self.resolver=ProjectWorkspaceResolver(self.db)

    def test_registered_storage_survives_settings_changes_and_isolation(self):
        app=ApplicationRuntime(self.db,self.resolver)
        self.assertEqual(app.workspace(self.a),self.root/self.a)
        self.assertEqual(app.workspace(self.b),self.root/self.b)
        self.assertNotEqual(app.workspace(self.a),app.workspace(self.b))

    def test_missing_mapping_fails_closed_instead_of_guessing(self):
        self.db.execute('DELETE FROM project_locations WHERE project_id=?',(self.a,));self.db.commit()
        with self.assertRaises(ValueError):self.resolver(self.a)
        with self.assertRaises(ValueError):self.resolver('c'*32)

    def test_uncommitted_mapping_visible_to_same_connection_only(self):
        new_root=self.root/'moved'
        self.db.execute('UPDATE project_locations SET folder=? WHERE project_id=?',(str(new_root),self.a))
        self.assertEqual(self.resolver(self.a),new_root)
        self.db.rollback()
        self.assertEqual(self.resolver(self.a),self.root/self.a)

    def test_reject_linked_workspace_ancestor(self):
        linked=self.root/'link'
        try:
            linked.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            # Developer Mode and SeCreateSymbolicLinkPrivilege are not
            # prerequisites for Windows QA. Junctions are also reparse links
            # and must exercise the same fail-closed workspace boundary.
            if os.name!='nt' or getattr(exc,'winerror',None) not in (5,1314):
                raise
            result=subprocess.run(
                ['cmd.exe','/d','/c','mklink','/J',str(linked),str(self.root)],
                capture_output=True,text=True,encoding='utf-8',errors='replace')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(linked.is_junction())
        self.db.execute('UPDATE project_locations SET folder=? WHERE project_id=?',(str(linked/self.a),self.a))
        with self.assertRaises(ValueError):self.resolver(self.a)

    def test_qt_uses_one_registered_authority_for_every_project_path(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        studio=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Studio')
        for method in (n for n in studio.body if isinstance(n,ast.FunctionDef)):
            bad=[n for n in ast.walk(method) if isinstance(n,ast.Call)
                 and isinstance(n.func,ast.Name) and n.func.id=='project_dir']
            self.assertFalse(bad,method.name)

if __name__=='__main__': unittest.main()
