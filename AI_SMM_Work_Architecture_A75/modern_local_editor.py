"""Optional modern local instruction-based image editor.
Run in separate process to isolate PyTorch/VRAM failures from GUI.
Requires separately installed diffusers, transformers, torch and model weights.
"""
import argparse, json, os, sys, traceback, gc, time, re
if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from pathlib import Path
from PIL import Image, ImageOps

def log(stage):
    print(json.dumps({"stage":stage},ensure_ascii=False),flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',required=True)
    p.add_argument('--reference',default='')
    p.add_argument('--prompt',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--model',default='Qwen/Qwen-Image-Edit-2509')
    p.add_argument('--model-path',default='')
    p.add_argument('--steps',type=int,default=12)
    p.add_argument('--max-side',type=int,default=512)
    p.add_argument('--offload',choices=['sequential','model','group'],default='sequential')
    args=p.parse_args()
    from reference_policy import reference_requested
    if args.reference and not reference_requested(args.prompt):
        raise ValueError('Нет явного разрешения использовать эталон.')
    output_path=Path(args.output).resolve()
    protected=[Path(args.source).resolve()]
    if args.reference: protected.append(Path(args.reference).resolve())
    if any(output_path == path or (output_path.exists() and path.exists() and output_path.samefile(path)) for path in protected):
        raise ValueError('Результат не может перезаписывать исходник или эталон.')
    import torch
    # SDPA already manages attention memory; forcing attention slicing adds overhead.
    torch.backends.cuda.matmul.allow_tf32 = True
    from diffusers import QwenImageEditPlusPipeline
    if not torch.cuda.is_available():
        raise RuntimeError('Для локальной Qwen Image Edit нужна NVIDIA CUDA. Выберите облачный режим для другого GPU/CPU.')
    if not args.model_path:
        raise RuntimeError('Модель не подготовлена загрузчиком. Сначала завершите скачивание.')
    import psutil
    mem=psutil.virtual_memory()
    free_vram, total_vram = torch.cuda.mem_get_info()
    log(f'Память: свободно RAM {mem.available/2**30:.1f} ГБ из {mem.total/2**30:.1f} ГБ; свободно VRAM {free_vram/2**30:.1f} ГБ из {total_vram/2**30:.1f} ГБ')
    if args.offload == 'model' and total_vram <= 13*2**30:
        log('Предупреждение: model offload на GPU до 12–13 ГБ может вызвать CUDA OOM; при ошибке приложение автоматически попробует sequential offload.')
    if mem.available < 8*2**30:
        raise RuntimeError('Недостаточно свободной RAM (<8 ГБ). Закройте тяжёлые приложения и повторите.')
    load_started=time.monotonic()
    log('Загружаю уже скачанные веса модели в память…')
    pipe=QwenImageEditPlusPipeline.from_pretrained(args.model_path,torch_dtype=torch.bfloat16,local_files_only=True)
    # CPU offload saves VRAM but needs sufficient system RAM and can be very slow.
    if args.offload == 'sequential':
        log('Экономия VRAM: последовательная выгрузка модулей в RAM (может работать очень медленно).')
        pipe.enable_sequential_cpu_offload()
    elif args.offload == 'group':
        # Experimental block-level group offloading. This transfers blocks rather
        # than every layer and may reduce PCIe overhead. Requires recent diffusers.
        from diffusers.hooks import apply_group_offloading
        cpu, gpu = torch.device('cpu'), torch.device('cuda')
        log('Экспериментальный group offload: блочная выгрузка с CUDA streaming.')
        apply_group_offloading(pipe.transformer, onload_device=gpu, offload_device=cpu,
                               offload_type='block_level', num_blocks_per_group=2,
                               use_stream=True)
        # Text encoder is only needed to prepare embeddings; use model-level
        # CPU offload hooks on remaining modules without moving transformer.
        for name in ('text_encoder', 'text_encoder_2'):
            module = getattr(pipe, name, None)
            if module is not None:
                apply_group_offloading(module, onload_device=gpu, offload_device=cpu,
                                       offload_type='leaf_level', use_stream=False)
        # VAE may not fit alongside active transformer blocks. Keep it offloaded.
        if getattr(pipe, 'vae', None) is not None:
            apply_group_offloading(pipe.vae, onload_device=gpu, offload_device=cpu,
                                   offload_type='leaf_level', use_stream=False)
    else:
        log('Экономия VRAM: выгрузка моделей в RAM.')
        pipe.enable_model_cpu_offload()
    # Avoid unconditional attention slicing / VAE tiling: both can slow down
    # standard SDPA and small 512px images. Enable only on a future OOM retry.
    log('Оптимизация: SDPA без принудительного attention slicing; VAE без tiling для 512px.')
    log(f'Подготовка модели: {time.monotonic()-load_started:.1f} сек')
    gc.collect()
    torch.cuda.empty_cache()
    with Image.open(args.source) as im: source=ImageOps.exif_transpose(im).convert('RGB')
    source.thumbnail((args.max_side,args.max_side))
    images=[source]
    from reference_policy import reference_requested
    if args.reference and not reference_requested(args.prompt):
        raise ValueError('Нет явного разрешения использовать эталон.')
    if args.reference and os.path.normcase(os.path.abspath(args.reference)) == os.path.normcase(os.path.abspath(args.source)):
        raise ValueError('Исходник совпадает с эталоном. Обработка запрещена.')
    if args.reference:
        with Image.open(args.reference) as im: reference=ImageOps.exif_transpose(im).convert('RGB')
        reference.thumbnail((args.max_side,args.max_side))
        images.append(reference)
    log(f'Генерация: {args.steps} шагов, сторона исходника до {args.max_side}px, offload={args.offload}. Возможна нехватка RAM/VRAM.')
    # Preserve the requested operation while reinforcing subject/background consistency.
    instruction = (args.prompt.strip() +
        '\nApply the requested edit faithfully. Preserve all elements that the user did not ask to change. '
        'For background replacement, replace the background as requested while preserving the foreground subject, pose, clothing and identity. '
        'Keep realistic perspective, shadows, lighting and texture. Do not introduce unrelated changes.')
    generation_started=time.monotonic()
    def step_progress(_pipe, step_index, timestep, callback_kwargs):
        completed=step_index+1
        elapsed=time.monotonic()-generation_started
        eta=elapsed/completed*(args.steps-completed)
        log(f'Шаг {completed}/{args.steps} · прошло {elapsed/60:.1f} мин · осталось примерно {eta/60:.1f} мин (оценка)')
        return callback_kwargs
    with torch.inference_mode():
        result=pipe(image=images,prompt=instruction,num_inference_steps=args.steps,
                    true_cfg_scale=1.0,callback_on_step_end=step_progress).images[0]
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    result.save(args.output,'PNG')
    log(f'Генерация заняла {time.monotonic()-generation_started:.1f} сек')
    log('Готово')
if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'error':str(exc),'traceback':traceback.format_exc()[-6000:]},ensure_ascii=False),flush=True)
        sys.exit(1)
