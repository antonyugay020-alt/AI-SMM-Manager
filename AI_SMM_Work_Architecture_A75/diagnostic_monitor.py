"""Read-only watcher for AI SMM Studio task diagnostics. Standard library only."""
import argparse
import collections
from contextlib import closing
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import time
import tempfile


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def discover_projects(root, explicit_root=None):
    """Mirror app.py storage_base/project_dir without importing GUI dependencies."""
    locations = []
    warnings = []
    roots = []
    if explicit_root:
        roots.append(Path(explicit_root).expanduser())
    else:
        settings = root / 'storage_settings.json'
        if settings.exists():
            try:
                settings_obj=json.loads(settings.read_text(encoding='utf-8'))
                if not isinstance(settings_obj,dict):
                    raise ValueError('settings must be a JSON object')
                value = settings_obj.get('projects_root')
                if isinstance(value, str) and value.strip():
                    roots.append(Path(value).expanduser())
            except (OSError, ValueError, TypeError) as exc:
                warnings.append('Cannot read storage_settings.json: ' + str(exc))
        roots.append(root / 'projects')
        db = root / 'studio.sqlite3'
        if db.exists():
            try:
                # Open database read-only: monitoring must never modify app state.
                with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as con:
                    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_locations'").fetchall()
                    if rows:
                        for (folder,) in con.execute('SELECT folder FROM project_locations'):
                            if folder:
                                locations.append(Path(folder).expanduser())
            except (sqlite3.Error, OSError) as exc:
                warnings.append('Cannot read project locations: ' + str(exc))
    for folder in roots:
        if folder.is_dir():
            locations.extend(p for p in folder.iterdir() if p.is_dir() and p.name != 'monitor_reports')
        else:
            warnings.append('Projects root not found: ' + str(folder))
    unique = {str(p.resolve()).casefold(): p.resolve() for p in locations if p.is_dir()}
    return sorted(unique.values()), warnings, roots


def inspect_project(project, stall_seconds=180):
    drafts = project / 'drafts'
    issues = []
    runs = []
    for progress in sorted(drafts.glob('publication_progress_*.json')):
        try:
            obj = json.loads(progress.read_text(encoding='utf-8'))
            if not isinstance(obj,dict):
                raise ValueError('progress must be a JSON object')
            total = int(obj.get('total', 0))
            completed = int(obj.get('completed', 0))
            posts = obj.get('posts', {})
            if not isinstance(posts, dict):
                raise ValueError('posts must be an object')
            if completed != len(posts):
                issues.append({'severity':'error','file':progress.name,'message':'completed differs from number of saved posts'})
            runs.append({'file':progress.name,'completed':completed,'total':total,'updated_at':obj.get('updated_at')})
        except (OSError, ValueError, TypeError) as exc:
            issues.append({'severity':'error','file':progress.name,'message':'invalid progress JSON: '+str(exc)})
    for log in sorted(drafts.glob('publication_diagnostics_*.jsonl')):
        stages = collections.Counter()
        errors = collections.Counter()
        last = None
        attempts = 0
        try:
            with log.open(encoding='utf-8') as f:
                for lineno, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError('event must be an object')
                    except ValueError:
                        issues.append({'severity':'warning','file':log.name,'message':f'bad JSON at line {lineno}'})
                        continue
                    stages[str(event.get('stage', 'unknown'))] += 1
                    stage = event.get('stage')
                    if stage == 'batch_attempt':
                        attempts += 1
                        for err in (event.get('errors') if isinstance(event.get('errors'),list) else []):
                            errors[str(err)[:160]] += 1
                    if stage in ('single_quality_rejected', 'single_semantic_rejected',
                                 'semantic_quality_rejected', 'semantic_revision_rejected',
                                 'quality_unresolved'):
                        for err in (event.get('issues')[:6] if isinstance(event.get('issues'),list) else []):
                            errors[str(err)[:160]] += 1
                    last = event
            age = max(0, time.time() - log.stat().st_mtime)
        except OSError as exc:
            issues.append({'severity':'error','file':log.name,'message':str(exc)})
            continue
        if last and age > stall_seconds and last.get('stage') not in ('task_done','task_failed','task_pending'):
            issues.append({'severity':'info','file':log.name,'message':f'no new events for {int(age)}s; not proof of a hang'})
        if errors:
            most, count = errors.most_common(1)[0]
            issues.append({'severity':'warning','file':log.name,'message':f'most frequent rejection ({count}): {most}'})
        runs.append({'file':log.name,'stages':dict(stages),'attempts':attempts,
                     'common_errors':errors.most_common(10),'last_event':last,'last_modified_age_seconds':int(age)})
    if not runs:
        issues.append({'severity':'info','message':'no publication progress/diagnostics files yet'})
    return {'time':utc_now(),'project':str(project),'runs':runs,'issues':issues}


def atomic_json(target, data, retries=5, delay=0.2):
    """Write a complete JSON file; tolerate transient Windows file locks."""
    from durable_json import atomic_write_json
    atomic_write_json(target, data, retries=retries, delay=delay)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only AI SMM diagnostic monitor')
    parser.add_argument('--root', help='Explicit projects directory (overrides auto-discovery)')
    parser.add_argument('--studio-root', help='AI SMM Studio data directory (default: ~/Documents/AI SMM Studio)')
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--stall-seconds', type=int, default=180)
    args = parser.parse_args(argv)
    root = Path(args.studio_root).expanduser() if args.studio_root else Path.home() / 'Documents' / 'AI SMM Studio'
    status_file = Path(__file__).resolve().parent / 'monitor_reports' / 'monitor_status.json'
    print('AI SMM Diagnostic Monitor 58.2', flush=True)
    print('Status report:', status_file, flush=True)
    print('Studio data:', root, flush=True)
    try:
        while True:
            projects, warnings, roots = discover_projects(root, args.root)
            status = {'time':utc_now(),'studio_root':str(root),'searched_roots':[str(p) for p in roots],
                      'projects_found':len(projects),'projects':[str(p) for p in projects], 'warnings':warnings}
            if not projects:
                status['error'] = 'No project folders found. Check Studio storage settings or pass --root.'
                print('ERROR:', status['error'], flush=True)
            else:
                print(f"[{status['time']}] Watching {len(projects)} project(s)", flush=True)
            for warning in warnings:
                print('NOTICE:', warning, flush=True)
            for project in projects:
                report = inspect_project(project, args.stall_seconds)
                report_path = project / 'monitor_reports' / 'latest_diagnostics.json'
                try:
                    atomic_json(report_path, report)
                    print('  Diagnostics:', report_path, flush=True)
                except OSError as exc:
                    print('  ERROR: Cannot save diagnostics:', exc, flush=True)
                print('  Project:', project, flush=True)
                for issue in report['issues']:
                    if issue['severity'] != 'info':
                        print(f"  {issue['severity'].upper()}: {issue['message']}", flush=True)
            try:
                atomic_json(status_file, status)
            except OSError as exc:
                print('ERROR: Cannot save monitor status:', exc, flush=True)
            if args.once:
                return 0 if projects else 2
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        print('Monitor stopped.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
