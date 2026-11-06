"""Qt-independent, project-bound read model for the entire workspace.

Every query takes an explicit project identity. A GUI selection cannot become
an implicit authority for another project's files, messages, or documents.
Snapshots contain immutable values, never writable database cursors.
"""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class ProjectView:
    project_id: str
    name: str
    category: str
    assets: tuple[tuple[str, str], ...]
    reference_path: str | None
    result_paths: tuple[str, ...]
    messages: tuple[tuple[str, str, str], ...]


class ProjectDataRuntime:
    def __init__(self, db:sqlite3.Connection):
        if not isinstance(db,sqlite3.Connection):
            raise TypeError('Project read model requires the application database')
        self.db=db

    def _require(self, project_id):
        if not isinstance(project_id,str) or not project_id:
            raise ValueError('Missing project identity')
        row=self.db.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown project identity')

    def project_name(self, project_id):
        self._require(project_id)
        return self.db.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()[0]

    def document_exists(self, project_id, document_id):
        return self.document(project_id, document_id) is not None

    def document_title_content(self, project_id, document_id):
        row = self.document(project_id, document_id)
        return None if row is None else (row[1], row[2])

    def document_kind_title(self, project_id, document_id):
        row = self.document(project_id, document_id)
        return None if row is None else (row[0], row[1])

    def latest_document(self, project_id, *, kind=None):
        self._require(project_id)
        if kind is not None and (not isinstance(kind, str) or not kind):
            raise ValueError('Invalid document kind')
        if kind is None:
            return self.db.execute('SELECT id,content,kind,title FROM manager_documents '
                'WHERE project_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 1',
                (project_id,)).fetchone()
        return self.db.execute('SELECT id,content,kind,title FROM manager_documents '
            'WHERE project_id=? AND kind=? ORDER BY updated_at DESC,rowid DESC LIMIT 1',
            (project_id,kind)).fetchone()

    def pending_request(self, project_id):
        self._require(project_id)
        row=self.db.execute('SELECT request FROM ai_pending_tasks WHERE project_id=?',
                            (project_id,)).fetchone()
        return None if row is None else row[0]

    def counts(self, project_id=None):
        project_count=self.db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
        if project_id is None:return project_count,0,None
        self._require(project_id)
        docs=self.db.execute('SELECT COUNT(*) FROM manager_documents WHERE project_id=?',
                             (project_id,)).fetchone()[0]
        name=self.db.execute('SELECT name FROM projects WHERE id=?',(project_id,)).fetchone()[0]
        return project_count,docs,name

    def documents(self, project_id, *, kind=None):
        self._require(project_id)
        if kind is not None and (not isinstance(kind,str) or not kind):
            raise ValueError('Invalid document kind')
        predicate=' AND kind=?' if kind is not None else ''
        args=(project_id,kind) if kind is not None else (project_id,)
        return tuple(self.db.execute("""SELECT id,title,kind,created_at
            FROM manager_documents WHERE project_id=?"""+predicate+
            ' ORDER BY updated_at DESC,id DESC',args).fetchall())

    def document(self, project_id, document_id):
        self._require(project_id)
        if not isinstance(document_id,str) or not document_id:
            raise ValueError('Missing document identity')
        return self.db.execute("""SELECT kind,title,content FROM manager_documents
            WHERE project_id=? AND id=?""",(project_id,document_id)).fetchone()

    def document_for_content(self, project_id, content):
        self._require(project_id)
        return self.db.execute("""SELECT kind,title FROM manager_documents
            WHERE project_id=? AND content=? ORDER BY updated_at DESC,rowid DESC LIMIT 1""",
            (project_id,content)).fetchone()

    def workspace(self, project_id):
        self._require(project_id)
        row=self.db.execute('SELECT name,category FROM projects WHERE id=?',(project_id,)).fetchone()
        assets=tuple(self.db.execute("""SELECT original_name,stored_path FROM assets
            WHERE project_id=? ORDER BY imported_at DESC,id DESC""",(project_id,)))
        ref=self.db.execute("""SELECT path FROM project_files WHERE project_id=?
            AND kind='reference' ORDER BY created_at DESC,id DESC LIMIT 1""",(project_id,)).fetchone()
        results=tuple(r[0] for r in self.db.execute("""SELECT path FROM project_files
            WHERE project_id=? AND kind='result' ORDER BY created_at DESC,id DESC""",(project_id,)))
        messages=tuple(self.db.execute("""SELECT role,content,created_at FROM messages
            WHERE project_id=? ORDER BY id""",(project_id,)))
        return ProjectView(project_id,row[0],row[1] or '',assets,ref[0] if ref else None,results,messages)

    def deletion_counts(self, project_id, scope):
        self._require(project_id)
        if scope not in ('project','assets','chat'):
            raise ValueError('Unknown deletion scope')
        tables=('manager_documents','assets','project_files','messages','publication_jobs','batch_runs') if scope=='project' else (('assets',) if scope=='assets' else ('messages',))
        return tuple((table,self.db.execute('SELECT COUNT(*) FROM '+table+' WHERE project_id=?',
                   (project_id,)).fetchone()[0]) for table in tables)
