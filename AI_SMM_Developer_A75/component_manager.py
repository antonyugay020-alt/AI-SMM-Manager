"""Local component discovery and opt-in launch; no downloads or installers."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

URL = 'http://127.0.0.1:8188'

def api(path, timeout=3):
    with urllib.request.urlopen(URL+path, timeout=timeout) as r:
        return json.load(r)

def get_comfy_checkpoints():
    info=api('/object_info/CheckpointLoaderSimple',timeout=5)
    return info['CheckpointLoaderSimple']['input']['required']['ckpt_name'][0]

def comfy_path():
    # Common install roots, plus previously used portable F: path.
    roots=[Path('F:/ComfyUI_windows_portable'),Path('D:/AI/ComfyUI_windows_portable'),
           Path.home()/'ComfyUI_windows_portable',Path.home()/'Downloads'/'ComfyUI_windows_portable',
           Path.home()/'Desktop'/'ComfyUI_windows_portable']
    for drive in 'CDEFGHIJ':
        roots.extend((Path(f'{drive}:/ComfyUI_windows_portable'),Path(f'{drive}:/AI/ComfyUI_windows_portable')))
    for root in dict.fromkeys(roots):
        if (root/'run_nvidia_gpu.bat').is_file() or (root/'run_cpu.bat').is_file():
            return root
    return None

def ollama_info():
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=3) as r:
            models=json.load(r).get('models',[])
        return 'работает; модели: '+(', '.join(x.get('name','?') for x in models) or 'не найдены')
    except Exception:
        return 'сервер не отвечает' + ('; команда найдена' if shutil.which('ollama') else '')

def inspect_components():
    try:
        choices=get_comfy_checkpoints()
        status='работает; checkpoints: '+(', '.join(choices) or 'нет')
    except Exception:
        status='сервер не отвечает'
    path=comfy_path()
    return {
        'Ollama':ollama_info(),
        'ComfyUI (сервер)':status,
        'ComfyUI (папка)':str(path) if path else 'не найден',
        'FFmpeg':'найден' if shutil.which('ffmpeg') else 'не найден (не обязателен для фото)',
        'Python':'установлен (запущено приложение)',
    }

def ensure_comfyui_running(root):
    try:
        get_comfy_checkpoints()
        return
    except Exception: pass
    root=Path(root)
    bat=root/'run_nvidia_gpu.bat'
    if not bat.is_file(): bat=root/'run_cpu.bat'
    if not bat.is_file(): raise FileNotFoundError('Не найден скрипт запуска ComfyUI')
    if os.name != 'nt': raise RuntimeError('Автозапуск portable ComfyUI доступен только в Windows')
    # Launch ComfyUI Python directly, not its .bat wrapper. This prevents
    # batch-script side effects and explicitly disables browser auto-launch.
    python_exe=root/'python_embeded'/'python.exe'
    main_py=root/'ComfyUI'/'main.py'
    if not python_exe.is_file() or not main_py.is_file():
        raise FileNotFoundError('Не найдены python_embeded/python.exe или ComfyUI/main.py в portable ComfyUI')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'DETACHED_PROCESS',0)
    log_dir=Path(os.environ['AI_SMM_TEST_ROOT'])/'logs' if os.environ.get('AI_SMM_TEST_ROOT') else Path.home()/'Documents'/'AI SMM Studio'/'logs'
    log_dir.mkdir(parents=True,exist_ok=True)
    log_path=log_dir/'comfyui.log'
    log=open(log_path,'ab',buffering=0)
    try:
        proc=subprocess.Popen([str(python_exe),'-s',str(main_py),
            '--windows-standalone-build','--disable-auto-launch',
            '--listen','127.0.0.1','--port','8188'],
            cwd=str(root/'ComfyUI'),creationflags=flags,
            stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
    finally:
        log.close()
    for _ in range(45):
        time.sleep(2)
        try:
            get_comfy_checkpoints()
            return
        except Exception: pass
    raise TimeoutError(f'ComfyUI не ответил за 90 секунд. Лог: {log_path}; код выхода: {proc.poll()}')
