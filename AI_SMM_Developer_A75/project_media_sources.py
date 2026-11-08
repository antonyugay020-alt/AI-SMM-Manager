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


IMAGE_EXTENSIONS=frozenset(('.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'))
VIDEO_EXTENSIONS=frozenset(('.mp4','.mov','.mkv','.avi','.webm'))


def _receipt_hash(db,owner,table,ident):
    value=None
    if table=='assets' and 'sha256' in {row[1] for row in db.execute('PRAGMA table_info(assets)')}:
        row=db.execute('SELECT sha256 FROM assets WHERE id=? AND project_id=?',(ident,owner)).fetchone()
        value=row[0] if row else None
    elif table=='project_files':
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_ingest_jobs'").fetchone():
            row=db.execute('SELECT source_sha256 FROM media_ingest_jobs WHERE id=? AND project_id=?',(ident,owner)).fetchone()
            value=row[0] if row else None
        if value is None and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='result_publication_events'").fetchone():
            row=db.execute("SELECT payload_hash FROM result_publication_events WHERE record_id=? AND project_id=? AND kind IN ('image','video') LIMIT 1",(ident,owner)).fetchone()
            value=row[0] if row else None
    return value if isinstance(value,str) and len(value)==64 and all(char in '0123456789abcdef' for char in value) else None


def media_catalog(db,owner,*,limit=200):
    """Issue project-owned resource IDs; do not read media bytes on the GUI."""
    if type(limit) is not int or not 1<=limit<=1000:raise ValueError('Invalid media catalog limit')
    result={}
    for table,path_column,name_column,prefix in [('assets','stored_path','original_name','asset'),('project_files','path','display_name','file')]:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():continue
        columns={row[1] for row in db.execute('PRAGMA table_info('+table+')')}
        if not {'id','project_id',path_column}<=columns:continue
        label=name_column if name_column in columns else 'id'
        for ident,path,name in db.execute('SELECT id,'+path_column+','+label+' FROM '+table+' WHERE project_id=? ORDER BY id LIMIT ?', (owner,limit)):
            if not isinstance(path,str) or not path:continue
            suffix=Path(path).suffix.lower()
            kind='image' if suffix in IMAGE_EXTENSIONS else 'video' if suffix in VIDEO_EXTENSIONS else None
            if kind is None:continue
            token='media:'+prefix+':'+str(ident)
            result[token]={'id':token,'record_id':str(ident),'table':table,'path_column':path_column,
                'path':str(path),'label':str(name),'label_column':label,'kind':kind,'sha256':_receipt_hash(db,owner,table,str(ident))}
            if len(result)>=limit:return result
    return result


def resolve_media_ids(db,owner,catalog,ids):
    """Validate issued identities against current rows; never accept raw paths."""
    if not isinstance(ids,list) or not ids or any(not isinstance(ident,str) for ident in ids) or len(ids)!=len(set(ids)):
        raise ValueError('Unique issued media IDs required')
    paths=[]
    for ident in ids:
        if not isinstance(ident,str) or ident not in catalog:raise ValueError('Unissued media source ID')
        item=catalog[ident]
        table=item.get('table');column=item.get('path_column')
        if (table,column) not in (('assets','stored_path'),('project_files','path')):
            raise ValueError('Invalid media source origin')
        label=item.get('label_column')
        if label not in ('id','original_name','display_name'):raise ValueError('Invalid source label origin')
        row=db.execute('SELECT '+column+','+label+' FROM '+table+' WHERE id=? AND project_id=?',(item['record_id'],owner)).fetchone()
        if row is None or str(row[0])!=item['path'] or str(row[1])!=item['label']:raise ValueError('Media source changed after dialogue analysis')
        if item.get('sha256') is not None and _receipt_hash(db,owner,table,item['record_id'])!=item['sha256']:
            raise ValueError('Media source receipt changed after dialogue analysis')
        paths.append(item['path'])
    return paths
