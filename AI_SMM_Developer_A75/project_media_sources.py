"""Single project-ownership boundary shared by local and cloud media adapters."""
from pathlib import Path
from project_file_boundary import reject_path_link,require_managed_file


def registered_source(db,workspace,project_id,filename):
    if not isinstance(project_id,str) or not project_id:
        raise ValueError('Project owner required')
    if not db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():
        raise ValueError('Project owner not found')
    root=Path(workspace(project_id))
    source=reject_path_link(Path(filename)).resolve(strict=True)
    if not source.is_file():raise ValueError('Source is not a regular file')
    require_managed_file(root,source,('originals','references','processed'))
    authorized=db.execute("""SELECT 1 FROM assets WHERE project_id=? AND stored_path=?
        UNION ALL SELECT 1 FROM project_files WHERE project_id=? AND path=? LIMIT 1""",
        (project_id,str(source),project_id,str(source))).fetchone()
    if authorized is None:raise ValueError('Media source is not registered to project')
    return source
