"""SQLite-authoritative project provisioning, reconciled independently of Qt."""
from pathlib import Path
from uuid import uuid4
import re
from sqlite_transaction_core import own
from project_file_boundary import reject_path_link

PROJECT_FOLDERS=('originals','references','processed','ready','texts','strategies','content_plans','diagrams','reports','pdf','video','versions')
_ID=re.compile(r"[0-9a-f]{32}\Z")

def register_project(db, base, name, category, description, timestamp, *, project_id=None):
    """Commit registry and immutable storage location before touching the filesystem."""
    if db.in_transaction: raise RuntimeError('Active transaction is owned by another operation')
    pid=project_id or uuid4().hex
    if not _ID.fullmatch(pid): raise ValueError('Invalid project ID')
    if not isinstance(name,str) or not name.strip(): raise ValueError('Project name required')
    storage_root = Path(base)
    if not storage_root.is_absolute() or '..' in storage_root.parts:
        raise ValueError('Project storage base must be absolute and canonical')
    reject_path_link(storage_root)
    location=storage_root/pid
    if location.exists() or location.is_symlink():
        raise FileExistsError('Project folder is already occupied')
    with own(db):
        db.execute('INSERT INTO projects(id,name,created_at,category,description) VALUES(?,?,?,?,?)',
                   (pid,name.strip(),timestamp,category,description))
        db.execute('INSERT INTO project_locations(project_id,folder) VALUES(?,?)',(pid,str(location)))
    return pid

def reconcile_project(db, project_id):
    """Idempotent projection. Never delete or overwrite pre-existing user content."""
    if db.in_transaction: raise RuntimeError('Active transaction is owned by another operation')
    if not _ID.fullmatch(project_id): raise ValueError('Invalid project ID')
    row=db.execute('SELECT folder FROM project_locations WHERE project_id=? AND EXISTS(SELECT 1 FROM projects WHERE id=?)',
                   (project_id,project_id)).fetchone()
    if not row: raise ValueError('Project not registered')
    base=Path(row[0])
    if not base.is_absolute() or '..' in base.parts:
        raise ValueError('Unsafe registered project directory')
    # Preflight ALL paths before creating any directory. A linked parent or
    # later linked subfolder must not cause a partially projected project.
    reject_path_link(base)
    for folder in PROJECT_FOLDERS:
        reject_path_link(base/folder)
    base.mkdir(parents=True,exist_ok=True)
    if not base.is_dir(): raise ValueError('Unsafe project directory')
    for folder in PROJECT_FOLDERS:
        target=base/folder
        target.mkdir(exist_ok=True)
        if not target.is_dir(): raise ValueError('Invalid project subdirectory')
    return base

def reconcile_projects(db, *, limit=100000):
    if db.in_transaction: raise RuntimeError('Active transaction')
    if not isinstance(limit,int) or limit<1: raise ValueError('Invalid limit')
    rows=db.execute('SELECT id FROM projects ORDER BY created_at,id LIMIT ?', (limit,)).fetchall()
    result={'completed':0,'failed':{}}
    for (pid,) in rows:
        try: reconcile_project(db,pid); result['completed']+=1
        except (OSError, ValueError) as exc: result['failed'][pid]=str(exc)
    return result
