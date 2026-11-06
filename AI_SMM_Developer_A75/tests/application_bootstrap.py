"""Headless schema bootstrap: one transactional migration for all persistent services."""
from pathlib import Path
import sqlite3
from sqlite_transaction_core import own

def install_schema(db):
    if db.in_transaction:
        raise RuntimeError('Schema migration requires clean transaction ownership')
    db.execute('PRAGMA foreign_keys=ON')
    with own(db):
        db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            original_name TEXT NOT NULL, stored_path TEXT NOT NULL, sha256 TEXT NOT NULL,
            imported_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id))''')
        existing = {row[1] for row in db.execute('PRAGMA table_info(projects)')}
        for col in ('category', 'description'):
            if col not in existing: db.execute(f'ALTER TABLE projects ADD COLUMN {col} TEXT DEFAULT ""')
        db.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)''')
        from task_intake import ensure_intake
        ensure_intake(db)
        db.execute('''CREATE TABLE IF NOT EXISTS project_files (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            kind TEXT NOT NULL, display_name TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS manager_documents (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
            title TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id))''')
        db.execute('CREATE TABLE IF NOT EXISTS project_locations (project_id TEXT PRIMARY KEY, folder TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS ai_dialogue_questions(project_id TEXT PRIMARY KEY,request TEXT NOT NULL,question TEXT NOT NULL,plan_json TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS ai_semantic_tasks(project_id TEXT,request TEXT,context_json TEXT,PRIMARY KEY(project_id,request))')
        db.execute('CREATE TABLE IF NOT EXISTS ai_pending_tasks (project_id TEXT PRIMARY KEY, request TEXT NOT NULL, question TEXT NOT NULL)')
        from task_artifacts import ensure_pending_revision_column
        ensure_pending_revision_column(db)
        db.execute('CREATE TABLE IF NOT EXISTS ai_task_facts (project_id TEXT PRIMARY KEY, facts_json TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS publication_jobs (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, media_path TEXT NOT NULL,
            channel TEXT NOT NULL, schedule_mode TEXT NOT NULL, publish_at TEXT NOT NULL DEFAULT '',
            caption TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Черновик',
            created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS publication_block_rules (
            project_id TEXT NOT NULL, channel TEXT NOT NULL, frequency TEXT NOT NULL,
            publish_time TEXT NOT NULL, PRIMARY KEY(project_id,channel))''')
        db.execute('''CREATE TABLE IF NOT EXISTS publication_media_review (
            project_id TEXT NOT NULL, media_path TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'Ожидает проверки',
            updated_at TEXT NOT NULL, PRIMARY KEY(project_id,media_path))''')
        db.execute('''CREATE TABLE IF NOT EXISTS batch_runs (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, prompt TEXT NOT NULL,
            engine_mode INTEGER NOT NULL, queue_json TEXT NOT NULL,
            total INTEGER NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        from batch_runtime import install as install_batches
        install_batches(db)
        from file_effect_outbox import install as install_file_effect_outbox
        install_file_effect_outbox(db)
        from task_registry import ensure_registry
        from dialogue_task_links import ensure_task_links
        from project_text_projection import install as install_text
        from media_ingest_core import install as install_media
        from project_result_retirement import install as install_retirements
        from manual_document_projection import install as install_manual
        from execution_coordinator import ExecutionCoordinator
        from question_registry import ensure_questions
        from publication_runtime import install as install_publication
        ensure_registry(db)
        ensure_task_links(db)
        install_text(db)
        install_media(db)
        install_retirements(db)
        install_manual(db)
        ensure_questions(db)
        install_publication(db)
        ExecutionCoordinator(db)._ensure()
        db.execute('CREATE TABLE IF NOT EXISTS ai_task_transitions (event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL, action TEXT NOT NULL, status_before TEXT, status_after TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)')
        db.execute('CREATE TABLE IF NOT EXISTS ai_dialogue_publications (intake_id TEXT PRIMARY KEY, message_id INTEGER NOT NULL UNIQUE, content TEXT NOT NULL, FOREIGN KEY(intake_id) REFERENCES ai_task_intake(intake_id), FOREIGN KEY(message_id) REFERENCES messages(id))')
        db.execute('CREATE TABLE IF NOT EXISTS result_publication_events (operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, payload_hash TEXT NOT NULL, attempt_id TEXT, dialogue_turn_id TEXT, FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)')
        db.execute('CREATE TABLE IF NOT EXISTS ai_completed_artifacts (project_id TEXT NOT NULL, artifact_id TEXT NOT NULL, request_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id,artifact_id))')
        db.execute("CREATE INDEX IF NOT EXISTS ai_task_transitions_by_task ON ai_task_transitions(project_id,task_id,created_at)")
        if 'dialogue_turn_id' not in {r[1] for r in db.execute('PRAGMA table_info(result_publication_events)')}:
            db.execute('ALTER TABLE result_publication_events ADD COLUMN dialogue_turn_id TEXT')
    return db

def open_application_database(database,storage_root,*,factory=None):
    database=Path(database)
    database.parent.mkdir(parents=True,exist_ok=True)
    if factory is None:
        from dialogue_execution import ProjectConnection
        factory=ProjectConnection
    db=sqlite3.connect(database,factory=factory)
    try:
        install_schema(db)
        from project_deletion import recover_deletions
        folders=[r[0] for r in db.execute('SELECT folder FROM project_locations')]
        recover_deletions(db,folders,storage_root)
    except BaseException:
        db.close()
        raise
    return db
