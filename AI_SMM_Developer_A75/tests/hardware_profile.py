"""Read-only hardware inventory and conservative execution guidance.
Never installs drivers, loads models, or silently changes image quality.
"""
import os
import shutil
import subprocess


def _run(command):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=7,
                           creationflags=flags, encoding='utf-8', errors='replace')
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def _ram_mb():
    if os.name == 'nt':
        try:
            import ctypes
            class Mem(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                            ('total', ctypes.c_ulonglong), ('available', ctypes.c_ulonglong),
                            ('page_total', ctypes.c_ulonglong), ('page_available', ctypes.c_ulonglong),
                            ('virtual_total', ctypes.c_ulonglong), ('virtual_available', ctypes.c_ulonglong),
                            ('extended', ctypes.c_ulonglong)]
            mem = Mem()
            mem.length = ctypes.sizeof(mem)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                return mem.total // 1048576, mem.available // 1048576
        except (OSError, AttributeError, ValueError):
            pass
    try:
        pages = os.sysconf('SC_PHYS_PAGES')
        free = os.sysconf('SC_AVPHYS_PAGES')
        size = os.sysconf('SC_PAGE_SIZE')
        return pages * size // 1048576, free * size // 1048576
    except (OSError, AttributeError, ValueError):
        return None, None


def detect_hardware():
    total_ram, free_ram = _ram_mb()
    result = {
        'gpu': None, 'vram_mb': None, 'vram_free_mb': None, 'cuda_driver': None,
        'gpus': [], 'ram_mb': total_ram, 'ram_free_mb': free_ram,
        'cpu_cores': os.cpu_count(), 'gpu_detected': False,
        'local_large_image_model_recommended': False,
        'cuda_available_in_app': None, 'guidance': []
    }
    smi = shutil.which('nvidia-smi')
    if smi:
        output = _run([smi, '--query-gpu=name,memory.total,memory.free,driver_version',
                       '--format=csv,noheader,nounits'])
        for line in output.splitlines():
            parts = [x.strip() for x in line.split(',')]
            if len(parts) != 4:
                continue
            try:
                name, total, free, driver = parts
                result['gpus'].append({'name': name, 'vram_mb': int(total),
                                       'vram_free_mb': int(free), 'driver': driver})
            except ValueError:
                continue
    if result['gpus']:
        # Pick the device with most *currently free* memory, not merely largest total.
        best = max(result['gpus'], key=lambda gpu: gpu['vram_free_mb'])
        result.update(gpu=best['name'], vram_mb=best['vram_mb'],
                      vram_free_mb=best['vram_free_mb'], cuda_driver=best['driver'],
                      gpu_detected=True)
    # Do not import torch: this probe must remain cheap and safe on machines without it.
    free = result['vram_free_mb']
    if free is None:
        result['guidance'].append('NVIDIA GPU не обнаружен. Локальные CUDA-задачи недоступны; проверьте CPU/облачные варианты.')
    elif free < 4500:
        result['guidance'].append('Мало свободной VRAM: избегайте загрузки тяжёлых моделей; освободите GPU-память.')
    elif free < 10000:
        result['guidance'].append('8–11 ГБ класс: квантованные языковые модели; тяжёлая графика только с выгрузкой в RAM или другим движком.')
    else:
        result['guidance'].append('Более 10 ГБ свободной VRAM: можно тестировать дополнительные локальные режимы с контролем памяти.')
    if free_ram is not None and free_ram < 10000:
        result['guidance'].append('Свободной RAM менее 10 ГБ: тяжёлая CPU-offload обработка может быть нестабильной.')
    result['guidance'].append('Качество изображения автоматически не снижается. Режим обработки выбирает пользователь.')
    result['local_large_image_model_recommended'] = bool(free and free >= 24000)
    return result
