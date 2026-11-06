"""SQLite-owned text projections: persistence precedes a replayable filesystem export.

Applies to user-edited texts. Both the file registry row and immutable payload
are committed together; a crash after commit can be replayed without the GUI.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from project_file_boundary import require_managed_file, require_managed_folder

_NAME = re.compile(r'[0-9a-f]{32}\.txt\Z')

def install(db):
    db.execute("""CREATE TABLE IF NOT EXISTS project_text_projections (
        file_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        filename TEXT NOT NULL, content TEXT NOT NULL, sha256 TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done')),
        attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '')""")
    db.execute('CREATE INDEX IF NOT EXISTS project_text_pending ON project_text_projections(state,project_id)')

def register(db, *, file_id, project_id, filename, content, full_path, timestamp):
    if not db.in_transaction:
        raise RuntimeError('Registration must be inside an owned SQLite transaction')
    if not _NAME.fullmatch(filename) or file_id != filename[:-4]:
        raise ValueError('Text identity must match its immutable filename')
    if not re.fullmatch(r'[a-f0-9]{32}', project_id):
        raise ValueError('Invalid project identity')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('Empty text')
    sha=hashlib.sha256(content.encode('utf-8')).hexdigest()
    install(db)
    db.execute('INSERT INTO project_files VALUES (?,?,?,?,?,?)',
               (file_id,project_id,'text',filename,str(full_path),timestamp))
    db.execute("""INSERT INTO project_text_projections
        (file_id,project_id,filename,content,sha256) VALUES (?,?,?,?,?)""",
        (file_id,project_id,filename,content,sha))

def drain(db, project_dir, *, project_id=None, limit=100):
    if db.in_transaction: raise RuntimeError('Projection requires a clean transaction')
    if type(limit) is not int or not 1 <= limit <= 1000: raise ValueError('Invalid limit')
    install(db)
    db.commit()
    where=" WHERE state='pending'"
    args=[]
    if project_id:
        where+=' AND project_id=?';args.append(project_id)
    rows=db.execute('SELECT file_id,project_id,filename,content,sha256 FROM project_text_projections'+where+' ORDER BY rowid LIMIT ?',(*args,limit)).fetchall()
    completed=failed=0
    for file_id,pid,name,content,sha in rows:
        try:
            if not _NAME.fullmatch(name) or name[:-4]!=file_id: raise ValueError('Corrupt projection identity')
            encoded=content.encode('utf-8')
            if hashlib.sha256(encoded).hexdigest()!=sha: raise ValueError('Corrupt projection payload')
            base=Path(project_dir(pid))
            folder=require_managed_folder(base,'texts')
            target=require_managed_file(base,folder/name,('texts',))
            registry=db.execute('SELECT path FROM project_files WHERE id=? AND project_id=? AND kind=? AND display_name=?',
                                (file_id,pid,'text',name)).fetchone()
            if not registry or registry[0]!=str(target): raise ValueError('Project file registry conflict')
            folder.mkdir(parents=True,exist_ok=True)
            require_managed_file(base,target,('texts',))
            if target.exists():
                if not target.is_file() or target.read_bytes()!=encoded:
                    raise ValueError('Existing file content conflict; refusing overwrite')
            else:
                import os
                tmp=folder/('.'+name+'.'+file_id+'.tmp')
                require_managed_file(base,tmp,('texts',))
                with tmp.open('xb') as out:
                    out.write(encoded);out.flush();os.fsync(out.fileno())
                try:
                    if target.exists(): raise ValueError('Destination was created concurrently')
                    os.link(tmp,target)  # no-clobber atomic publish
                finally:
                    tmp.unlink(missing_ok=True)
            if target.read_bytes()!=encoded: raise ValueError('Projection read-back failed')
            db.execute("UPDATE project_text_projections SET state='done',error='' WHERE file_id=? AND state='pending'",(file_id,))
            db.commit();completed+=1
        except Exception as exc:
            if db.in_transaction:db.rollback()
            db.execute("UPDATE project_text_projections SET attempts=attempts+1,error=? WHERE file_id=? AND state='pending'",(str(exc)[:500],file_id))
            db.commit();failed+=1
    return dict(completed=completed,failed=failed,pending=db.execute("SELECT count(*) FROM project_text_projections WHERE state='pending'").fetchone()[0])
