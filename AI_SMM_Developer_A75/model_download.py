"""Resumable Hugging Face model fetch with bounded retries and heartbeat.
Cache stays in the user's standard HF cache so partial files survive restarts.
"""
import argparse, json, os, sys, threading, time, random
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def emit(message):
    print(json.dumps({'stage': message}, ensure_ascii=False), flush=True)

def size_of_cache(repo):
    root=Path(os.environ.get('HF_HUB_CACHE') or Path(os.environ.get('HF_HOME',Path.home()/'.cache'/'huggingface'))/'hub')
    directory=root/('models--'+repo.replace('/','--'))
    total=0; partial=0
    if directory.exists():
        for folder,dirs,files in os.walk(directory):
            for name in files:
                try:
                    sz=(Path(folder)/name).stat().st_size
                    total+=sz
                    if name.endswith('.incomplete'):partial+=sz
                except OSError:pass
    return total,partial

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--model',default='Qwen/Qwen-Image-Edit-2509');args=ap.parse_args()
    from huggingface_hub import snapshot_download, try_to_load_from_cache
    # Fully cached snapshots can be reused without checking the network.
    from huggingface_hub import scan_cache_dir
    try:
        info=scan_cache_dir()
        repos=[r for r in info.repos if r.repo_id == args.model and r.repo_type == 'model']
        for repo in repos:
            for revision in sorted(repo.revisions, key=lambda x: x.last_modified, reverse=True):
                root=Path(revision.snapshot_path)
                if (root/'model_index.json').is_file() and (root/'transformer').is_dir() and (root/'text_encoder').is_dir() and not list(root.rglob('*.incomplete')):
                    emit('Использую локальный кэш модели; проверка сети пропущена.')
                    print(json.dumps({'model_path':str(root)},ensure_ascii=False),flush=True)
                    return
    except Exception as exc:
        emit('Локальный кэш требует проверки: '+str(exc)[:140])
    done=threading.Event()
    def heartbeat():
        while not done.wait(15):
            total,partial=size_of_cache(args.model)
            emit(f'Загрузка модели: в кэше {total/2**30:.2f} ГБ; незавершённых файлов {partial/2**30:.2f} ГБ. Ожидание сети/загрузка…')
    t=threading.Thread(target=heartbeat,daemon=True);t.start()
    try:
        # HF Hub uses .incomplete files for resumption. Never clear the cache on a network failure.
        for attempt in range(1,7):
            emit(f'Проверка и докачка {args.model}: попытка {attempt}/6. Уже загруженные файлы сохраняются.')
            try:
                path=snapshot_download(repo_id=args.model, max_workers=2)
                emit('Модель загружена и проверена Hugging Face Hub.')
                print(json.dumps({'model_path':str(path)},ensure_ascii=False),flush=True)
                return
            except (KeyboardInterrupt,SystemExit):raise
            except Exception as exc:
                if attempt==6:raise RuntimeError(f'Не удалось скачать модель за 6 попыток: {type(exc).__name__}: {exc}') from exc
                delay=min(90,5*2**(attempt-1))+random.uniform(0,2)
                emit(f'Сеть/загрузка: {type(exc).__name__}: {str(exc)[:200]}. Повтор через {int(delay)} сек, без удаления скачанного.')
                time.sleep(delay)
    finally:done.set();t.join(timeout=1)
if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),flush=True)
        sys.exit(1)
