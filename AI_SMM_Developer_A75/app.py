"""AI SMM Manager — desktop application (version in version_info.py)."""
from __future__ import annotations
import hashlib, json, os, shutil, sqlite3, subprocess, sys, urllib.request, urllib.error, base64, io, time, mimetypes, threading, re, html
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from cloud_credentials import get_openai_key, set_openai_key, clear_openai_key
from PySide6.QtCore import Qt, QThread, Signal, QMimeData, QUrl, QTimer, QSize
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QDrag, QBrush, QFont, QTextDocument, QIcon
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QStackedWidget, QTextEdit, QVBoxLayout,
    QWidget, QTabWidget, QGraphicsView, QGraphicsScene, QGraphicsRectItem, QCheckBox, QInputDialog, QSlider, QFrame, QPlainTextEdit, QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy, QMenu, QAbstractItemView, QScrollArea, QGridLayout)
from PySide6.QtPrintSupport import QPrinter
from PIL import Image, ImageEnhance, ImageOps, ImageStat

# Detect existing local engines first. No silent downloads, installs or external uploads.
from agent_runtime import plan_task, available_tools
from version_info import APP_VERSION, STAGE_NUMBER, BUILD_LABEL
from hardware_profile import detect_hardware
from component_manager import inspect_components, ensure_comfyui_running, get_comfy_checkpoints, comfy_path


ROOT = (Path(os.environ['AI_SMM_TEST_ROOT']).expanduser().resolve()
        if os.environ.get('AI_SMM_TEST_ROOT') else Path.home() / 'Documents' / 'AI SMM Studio')
# pythonw.exe has no console: stdout/stderr can be None. rembg's model downloader
# (tqdm) writes to stderr, and fails with NoneType.write unless a stream exists.
# Keep these streams open for the entire lifetime of the application.
ROOT.joinpath('logs').mkdir(parents=True, exist_ok=True)
_CONSOLE_FALLBACKS = []
for _stream_name in ('stdout', 'stderr'):
    if getattr(sys, _stream_name, None) is None:
        _log_stream = open(ROOT / 'logs' / ('pythonw_' + _stream_name + '.log'),
                           'a', encoding='utf-8', buffering=1)
        _CONSOLE_FALLBACKS.append(_log_stream)
        setattr(sys, _stream_name, _log_stream)
DB = ROOT / 'studio.sqlite3'

PERFORMANCE_LOG = ROOT / 'logs' / 'performance.jsonl'
_PERFORMANCE_LOCK = threading.Lock()
def record_performance(operation, elapsed_seconds, metadata=None):
    """Best-effort local-only timings; no image data, prompts or project content."""
    try:
        event={'timestamp':datetime.now(timezone.utc).isoformat(), 'operation':str(operation),
               'elapsed_seconds':round(float(elapsed_seconds),2), 'metadata':metadata or {}}
        with _PERFORMANCE_LOCK:
            with PERFORMANCE_LOG.open('a',encoding='utf-8') as handle:
                handle.write(json.dumps(event,ensure_ascii=False)+'\n')
    except (OSError,ValueError,TypeError):
        pass

STORAGE_SETTINGS = ROOT / 'storage_settings.json'
def storage_base():
    try:
        data = json.loads(STORAGE_SETTINGS.read_text(encoding='utf-8'))
        if not isinstance(data,dict): return ROOT / 'projects'
        value = str(data.get('projects_root','')).strip()
        if value: return Path(value).expanduser()
    except (OSError, ValueError, TypeError): pass
    return ROOT / 'projects'

def document_folder(kind):
    return {'strategy':'strategies','calendar':'content_plans','plans':'diagrams',
            'analytics':'reports','tasks':'texts'}.get(kind,'texts')

def safe_file_label(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:75] or 'document'


def document_export_html(title,content):
    """Readable projection of stored Markdown; source and literal code stay intact."""
    from editorial_evidence_matrix import publication_rows
    rows=publication_rows(content)
    def markdown(value):
        fragment=QTextDocument();fragment.setMarkdown(str(value))
        body=re.search(r'<body[^>]*>(.*)</body>',fragment.toHtml(),re.S)
        return body.group(1) if body else html.escape(str(value))
    parts=['<h1>'+html.escape(title)+'</h1>']
    if rows:
        # A publication is a production card. Eleven wide text columns do not
        # fit A4, and rendering raw pipes hides the boundary between fields.
        from editorial_evidence_matrix import _cells
        from publication_schema import publication_field_type
        by_source={row['raw']:row for row in rows};prose=[];table_header=False
        for line in content.splitlines():
            row=by_source.get(line)
            if row is None:
                cells=_cells(line) if line.lstrip().startswith('|') else []
                if cells and publication_field_type(cells[0])=='slot':
                    table_header=True;continue
                if table_header and cells and all(re.fullmatch(r'[-: ]+',cell or '-') for cell in cells):
                    table_header=False;continue
                table_header=False;prose.append(line);continue
            if prose:
                parts.append(markdown('\n'.join(prose)));prose=[]
            topic=row['typed_fields'].get('topic','')
            parts.append('<h2>'+
                         html.escape(row['slot']+(' — '+topic if topic else ''))+'</h2>')
            for label,value in row['fields'].items():
                if not value:continue
                role=publication_field_type(label)
                if role in ('slot','topic'):continue  # Already present in the card heading.
                if role=='visual':value=re.sub(r'(?<!\w)(Сцена\s+\d+\s*:)',r'\n\n\1',value)
                parts.append('<h3>'+html.escape(label.upper() if label in ('cta','kpi') else label.capitalize())+'</h3>'+markdown(value))
        if prose:parts.append(markdown('\n'.join(prose)))
    else:
        parts.append(markdown(content))
    return ('<html><head><style>body{font-family:Arial,sans-serif;font-size:10pt;color:#17222d;}'
            'h1{font-size:19pt;color:#153d46;}h2{font-size:15pt;color:#153d46;}'
            'h3{font-size:10pt;color:#315b64;margin-top:10px;margin-bottom:3px;}'
            'p{margin-top:3px;margin-bottom:7px;}table{border-collapse:collapse;}'
            'td,th{padding:5px;border:1px solid #aab7bc;}</style></head><body>'+
            ''.join(parts)+'</body></html>')


def render_diagram_png_bytes(scene):
    """Capture an optional diagram as durable bytes before the SQLite commit."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
    bounds=scene.itemsBoundingRect().adjusted(-12,-12,12,12)
    if bounds.width()<=0 or bounds.height()<=0:
        raise ValueError('Пустая схема документа')
    scale=min(1.5,3000/max(bounds.width(),bounds.height()))
    image=QImage(max(1,int(bounds.width()*scale)),max(1,int(bounds.height()*scale)),QImage.Format_ARGB32)
    image.fill(QColor('white'))
    painter=QPainter(image)
    try:scene.render(painter,QRectF(image.rect()),bounds)
    finally:painter.end()
    data=QByteArray()
    buffer=QBuffer(data)
    if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer,'PNG'):
        raise RuntimeError('Не удалось зафиксировать PNG-схему')
    return bytes(data)

def export_document_files(project_id, doc_id, kind, title, content, scene=None, diagram_png=None, *, workspace_root=None):
    """Regenerate an idempotent PDF/TXT/PNG projection from the SQLite source.

    Nothing is acknowledged as committed until all temporary files pass checks
    and their published targets are read back. Mid-batch failures remain pending
    in the durable export outbox; a future replay replaces the whole projection.
    """
    from artifact_staging import StagedArtifactBatch
    if workspace_root is None:
        raise ValueError('Document export requires the registered project workspace')
    from project_file_boundary import reject_path_link
    base = reject_path_link(Path(workspace_root))
    stem = safe_file_label(title) + '_' + doc_id[:8]
    txt = base / document_folder(kind) / (stem + '.txt')
    pdf = base / 'pdf' / (stem + '.pdf')
    diagram = None
    expected_text = content.encode('utf-8')
    with StagedArtifactBatch(base, (document_folder(kind), 'pdf', 'diagrams')) as batch:
        staged_txt = batch.stage(txt)
        staged_txt.write_bytes(expected_text)
        staged_pdf = batch.stage(pdf)
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(str(staged_pdf))
        printer.setPageSize(__import__('PySide6.QtGui',fromlist=['QPageSize']).QPageSize(__import__('PySide6.QtGui',fromlist=['QPageSize']).QPageSize.A4))
        doc = QTextDocument()
        doc.setHtml(document_export_html(title,content))
        doc.print_(printer)
        if scene is not None and scene.items() != []:
            bounds = scene.itemsBoundingRect().adjusted(-12,-12,12,12)
            if bounds.width() > 0 and bounds.height() > 0:
                scale = min(1.5, 3000/max(bounds.width(),bounds.height()))
                img = QImage(max(1,int(bounds.width()*scale)),max(1,int(bounds.height()*scale)),QImage.Format_ARGB32)
                img.fill(QColor('white'))
                painter=QPainter(img)
                scene.render(painter, __import__('PySide6.QtCore',fromlist=['QRectF']).QRectF(img.rect()), bounds)
                painter.end()
                diagram = base / 'diagrams' / (stem + '.png')
                staged_diagram = batch.stage(diagram)
                if not img.save(str(staged_diagram), 'PNG'):
                    raise RuntimeError('Не удалось подготовить PNG-схему')
        if scene is None and diagram_png is not None:
            if not isinstance(diagram_png, bytes) or not diagram_png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Неподтверждённые PNG-данные')
            diagram = base / 'diagrams' / (stem + '.png')
            batch.stage(diagram).write_bytes(diagram_png)
        def verify_export(path, target):
            if target == txt:
                if path.read_bytes() != expected_text:
                    raise RuntimeError('TXT не совпадает с сохранённым документом')
            elif target == pdf:
                if path.stat().st_size < 20 or not path.read_bytes().startswith(b'%PDF-'):
                    raise RuntimeError('PDF не прошёл проверку формата и размера')
            elif target == diagram:
                if not path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
                    raise RuntimeError('PNG не прошёл проверку формата')
            else:
                raise RuntimeError('Неизвестный файл экспорта')
        batch.publish(verify_export)
    return txt, pdf, diagram

CATEGORIES = ['Ресторан / кафе', 'Автосервис / услуги', 'Промышленность',
              'Салон красоты', 'Медицинский центр', 'Аптека', 'Магазин / торговля',
              'Образование', 'Недвижимость', 'Другое']
MODEL = str(os.environ.get('AI_SMM_MODEL', 'qwen3:8b')).strip() or 'qwen3:8b'
# GPT coordinates complex requests; hybrid mode routes only unambiguous casual turns to Ollama.
# The coordinator is independent of the content-generation model.
from central_dialogue_transport import default_central_model
CENTRAL_MODEL = default_central_model()
VISION_MODEL = 'gemma3:4b'
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}

def mentions_reference(instruction):
    """Opt-in only: a reference is never part of an unmentioned task."""
    from reference_policy import reference_requested
    return reference_requested(instruction)


def same_file(a, b):
    if not a or not b:
        return False
    try:
        return Path(a).resolve().samefile(Path(b).resolve())
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))

def now(): return datetime.now(timezone.utc).isoformat()

def connect():
    from application_bootstrap import open_application_database
    return open_application_database(DB,storage_base())

class PublicationGallery(QListWidget):
    """Drag media to a channel; accept Windows Explorer files into the project's ready folder."""
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setAcceptDrops(True)

    def startDrag(self, supportedActions):
        paths = [x.data(Qt.UserRole) for x in self.selectedItems()]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and event.source() is not self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and event.source() is not self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls() and event.source() is not self:
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if self.owner.import_ready_publication_files(paths):
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()


class PublicationChannelList(QListWidget):
    """Accept approved media from the gallery or another publication channel."""
    def __init__(self, channel, owner):
        super().__init__()
        self.channel = channel
        self.owner = owner
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def startDrag(self, supportedActions):
        ids = [item.data(Qt.UserRole) for item in self.selectedItems()]
        if not ids:
            return
        mime = QMimeData()
        mime.setData('application/x-ai-smm-publication-jobs',
                     json.dumps(ids).encode('utf-8'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def dragEnterEvent(self, event):
        if (event.mimeData().hasUrls() or
                event.mimeData().hasFormat('application/x-ai-smm-publication-jobs')):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat('application/x-ai-smm-publication-jobs'):
            try:
                ids = json.loads(bytes(mime.data('application/x-ai-smm-publication-jobs')).decode('utf-8'))
            except (ValueError, UnicodeError):
                event.ignore()
                return
            paths = self.owner.publication_paths_for_job_ids(ids)
            if paths and self.owner.add_media_paths_to_channel(paths, self.channel):
                event.acceptProposedAction()
            else:
                event.ignore()
        elif mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if self.owner.add_media_paths_to_channel(paths, self.channel):
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()


class DialogueWorker(QThread):
    done=Signal(object)
    failed=Signal(str)
    progress=Signal(dict)
    def __init__(self,message,snapshot,workspace,turn_id):
        super().__init__()
        self.message,self.snapshot,self.workspace,self.turn_id=message,snapshot,workspace,turn_id
    def run(self):
        from dialogue_runtime import understand_dialogue,save_turn
        from diagnostic_report import capture_task
        try:
            save_turn(self.workspace,self.turn_id,status='understanding',utterance=self.message,
                      project_id=self.snapshot['project_id'],snapshot=self.snapshot)
            from execution_time_budget import worker_model_budget
            with worker_model_budget('dialogue'), capture_task(self.workspace,self.message):
                plan=understand_dialogue(self.message,self.snapshot,model=CENTRAL_MODEL,turn_id=self.turn_id,
                    report=lambda stage,**details:self.progress.emit({'stage':stage,**details}))
            save_turn(self.workspace,self.turn_id,status='plan_prepared',completion_verified=False,decision=plan.action,
                      protocol_normalizations=list(plan.normalizations))
            self.done.emit(plan)
        except Exception as exc:
            import traceback
            try:save_turn(self.workspace,self.turn_id,status='failed',error=str(exc),traceback=traceback.format_exc())
            except Exception:pass
            self.failed.emit(str(exc))


class TaskWorker(QThread):
    done=Signal(dict)
    failed=Signal(str)
    progress=Signal(dict)
    def __init__(self, message, context):
        super().__init__()
        self.message,self.context=message,context
    def run(self):
        try:
            from compound_tasks import is_compound_request, execute_compound
            original = self.context.get('resolved_task_request', self.message)
            from text_workflow import run_text_task
            from diagnostic_report import capture_task
            from contextlib import nullcontext
            scope=capture_task(self.context['project_workspace'],original) if self.context.get('project_workspace') else nullcontext()
            from execution_time_budget import worker_model_budget
            with worker_model_budget('content'), scope:
                result=run_text_task(original,self.context,model=MODEL,
                                     progress=self.progress.emit)
            if (self.context.get('semantic_execution_kind')=='bundle') if self.context.get('semantic_authorized') else is_compound_request(original):
                outcome=(result.get('content_delivery') or {}).get('independent_editor',{})
                self.progress.emit({'stage':'final_independent_editor_done',
                    'status':outcome.get('status','UNKNOWN'),
                    'issues':[str(item.get('reason',''))[:180] for item in outcome.get('issues',[])[:5]],
                    'disproved_absence_claims':len(outcome.get('disproved_absence_claims',[]))})
            self.done.emit(result)
        except Exception as exc:self.failed.emit(str(exc))

class DiagnosticReportWorker(QThread):
    done=Signal(dict)
    failed=Signal(str)
    def __init__(self,snapshot,workspace,target):
        super().__init__()
        self.snapshot,self.workspace,self.target=snapshot,workspace,target
    def run(self):
        try:
            from diagnostic_report import build_report
            result=build_report(self.snapshot,self.workspace,ROOT,Path(__file__).resolve().parent,self.target)
            self.done.emit({**result,'project_id':self.snapshot['project']['id']})
        except Exception as exc:
            import traceback
            from diagnostic_report import append_project_event
            append_project_event(self.workspace,'report_export_exception',{'traceback':traceback.format_exc()})
            self.failed.emit(str(exc))


class VideoTrimWorker(QThread):
    """GPU-accelerated video encoding when NVENC is available; safe CPU fallback."""
    done=Signal(str)
    failed=Signal(str)
    def __init__(self, source, output, start, end):
        super().__init__()
        self.source,self.output,self.start,self.end=source,output,start,end
    def run(self):
        try:
            if not shutil.which('ffmpeg'):raise RuntimeError('FFmpeg не найден. Установите FFmpeg и добавьте в PATH.')
            if not (isinstance(self.start,(int,float)) and isinstance(self.end,(int,float)) and 0<=self.start<self.end):
                raise ValueError('Нужны корректные начало и конец фрагмента в секундах.')
            self.output.parent.mkdir(parents=True,exist_ok=True)
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            # NVENC is a separate hardware encoder; its availability is tested by FFmpeg.
            # If unavailable, fall back to the existing high-quality CPU pipeline.
            encoder_check=subprocess.run(['ffmpeg','-hide_banner','-encoders'],capture_output=True,text=True,
                timeout=15,creationflags=flags, encoding='utf-8', errors='replace')
            nvenc='h264_nvenc' in encoder_check.stdout
            common=['ffmpeg','-hide_banner','-nostdin','-y','-ss',str(self.start),'-i',str(self.source),
                '-t',str(self.end-self.start),'-map','0:v:0','-map','0:a?']
            gpu_args=['-c:v','h264_nvenc','-preset','p4','-cq','20','-b:v','0','-c:a','aac',str(self.output)]
            cpu_args=['-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac',str(self.output)]
            started=time.monotonic()
            proc=subprocess.run(common+(gpu_args if nvenc else cpu_args),capture_output=True,text=True,
                timeout=3600,creationflags=flags, encoding='utf-8', errors='replace')
            if proc.returncode and nvenc:
                # Failed GPU attempt may have left an incomplete file. Overwrite on CPU retry.
                proc=subprocess.run(common+cpu_args,capture_output=True,text=True,timeout=3600,creationflags=flags, encoding='utf-8', errors='replace')
            if proc.returncode:raise RuntimeError(proc.stderr[-1500:])
            record_performance('video_trim',time.monotonic()-started,{'gpu_encoder_attempted':nvenc})
            self.done.emit(str(self.output))
        except Exception as exc:self.failed.emit(str(exc))

class AIWorker(QThread):
    done = Signal(str)
    failed = Signal(str)
    def __init__(self, messages, model=MODEL):
        super().__init__()
        self.messages = messages
        self.model = model
    def run(self):
        try:
            data = json.dumps({'model': self.model, 'messages': self.messages, 'stream': False,
                               'options': {'num_ctx': 4096}, 'think': False}).encode('utf-8')
            req = urllib.request.Request('http://127.0.0.1:11434/api/chat', data=data,
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=240) as response:
                payload = json.load(response)
            answer = payload.get('message', {}).get('content', '').strip()
            self.done.emit(answer or 'Модель не вернула текст ответа.')
        except Exception as exc:
            self.failed.emit(str(exc))

class EditWorker(QThread):
    done = Signal(str)
    failed = Signal(str)
    def __init__(self, source, reference, output, instruction):
        super().__init__()
        self.source, self.reference, self.output, self.instruction = source, (reference if mentions_reference(instruction) else None), output, instruction
    def run(self):
        try:
            with Image.open(self.source) as raw:
                original = ImageOps.exif_transpose(raw).convert('RGB')
            # Pixel-preserving local color/tonal adjustment; no generative image changes.
            if self.reference:
                with Image.open(self.reference) as raw:
                    reference = ImageOps.exif_transpose(raw).convert('RGB')
                reference.thumbnail((900, 900))
                source_sample = original.copy()
                source_sample.thumbnail((900, 900))
                source_stat, ref_stat = ImageStat.Stat(source_sample), ImageStat.Stat(reference)
                source_channels = original.split()
                adjusted = []
                for channel, mean_s, std_s, mean_r, std_r in zip(source_channels, source_stat.mean, source_stat.stddev, ref_stat.mean, ref_stat.stddev):
                    factor = max(0.6, min(1.6, (std_r + 1) / (std_s + 1)))
                    # Blend 55% reference statistics to retain source identity and naturalness.
                    factor = 1 + (factor - 1) * 0.55
                    shift = (mean_r - mean_s) * 0.55
                    lut = [max(0, min(255, round((i - mean_s) * factor + mean_s + shift))) for i in range(256)]
                    adjusted.append(channel.point(lut))
                edited = Image.merge('RGB', adjusted)
            else:
                edited = ImageOps.autocontrast(original, cutoff=0.5, preserve_tone=True)
                edited = ImageEnhance.Contrast(edited).enhance(1.07)
            command = self.instruction.lower()
            if any(word in command for word in ('ярче', 'светлее', 'осветл')):
                edited = ImageEnhance.Brightness(edited).enhance(1.12)
            if any(word in command for word in ('темнее', 'затемн')):
                edited = ImageEnhance.Brightness(edited).enhance(0.88)
            if any(word in command for word in ('контраст', 'тени глубже')):
                edited = ImageEnhance.Contrast(edited).enhance(1.12)
            if any(word in command for word in ('насыщен', 'сочнее')):
                edited = ImageEnhance.Color(edited).enhance(1.12)
            if any(word in command for word in ('менее насыщ', 'приглуш')):
                edited = ImageEnhance.Color(edited).enhance(0.88)
            self.output.parent.mkdir(parents=True, exist_ok=True)
            edited.save(self.output, format='JPEG', quality=94, subsampling=0)
            self.done.emit(str(self.output))
        except Exception as exc:
            self.failed.emit(str(exc))

class GenerateWorker(QThread):
    done = Signal(str)
    failed = Signal(str)
    def __init__(self, source, output, prompt, checkpoint, strength, mask_png):
        super().__init__()
        self.source, self.output, self.prompt = source, output, prompt
        self.checkpoint, self.strength, self.mask_png = checkpoint, strength, mask_png

    @staticmethod
    def request(url, data=None, headers=None, timeout=30):
        req=urllib.request.Request(url,data=data,headers=headers or {})
        with urllib.request.urlopen(req,timeout=timeout) as response:return response.read()

    def upload(self,data,filename):
        boundary='----AISMM'+uuid4().hex
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode()+data+f'\r\n--{boundary}--\r\n'.encode())
        result=json.loads(self.request('http://127.0.0.1:8188/upload/image',body,{'Content-Type':'multipart/form-data; boundary='+boundary},60))
        return result['name']

    def run(self):
        try:
            from PIL import ImageFilter
            with Image.open(self.source) as im: original=ImageOps.exif_transpose(im).convert('RGB')
            with Image.open(io.BytesIO(self.mask_png)) as im: mask=im.convert('L')
            # Mask was painted on a thumbnail. Preserve alignment and fit SD1.5 memory limits.
            ratio=min(1,768/max(original.size)); w=max(64,round(original.width*ratio/64)*64); h=max(64,round(original.height*ratio/64)*64)
            original=original.resize((w,h),Image.Resampling.LANCZOS)
            mask=mask.resize((w,h),Image.Resampling.BILINEAR)
            if mask.getbbox() is None: raise ValueError('Маска пуста: выделите область кистью.')
            image_io=io.BytesIO(); original.save(image_io,'PNG')
            mask_io=io.BytesIO(); mask.save(mask_io,'PNG')
            image_name=self.upload(image_io.getvalue(),'source_'+uuid4().hex+'.png')
            mask_name=self.upload(mask_io.getvalue(),'mask_'+uuid4().hex+'.png')
            # Standard ComfyUI inpainting workflow; no third-party custom nodes.
            graph={
              '1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':self.checkpoint}},
              '2':{'class_type':'LoadImage','inputs':{'image':image_name}},
              '3':{'class_type':'LoadImage','inputs':{'image':mask_name}},
              '4':{'class_type':'ImageToMask','inputs':{'image':['3',0],'channel':'red'}},
              '5':{'class_type':'VAEEncodeForInpaint','inputs':{'pixels':['2',0],'vae':['1',2],'mask':['4',0],'grow_mask_by':6}},
              '6':{'class_type':'CLIPTextEncode','inputs':{'text':self.prompt,'clip':['1',1]}},
              '7':{'class_type':'CLIPTextEncode','inputs':{'text':'distorted person, deformed face, low quality, artifacts, extra limbs','clip':['1',1]}},
              '8':{'class_type':'KSampler','inputs':{'seed':int.from_bytes(os.urandom(4),'big'),'steps':26,'cfg':7.0,'sampler_name':'euler','scheduler':'normal','denoise':self.strength,'model':['1',0],'positive':['6',0],'negative':['7',0],'latent_image':['5',0]}},
              '9':{'class_type':'VAEDecode','inputs':{'samples':['8',0],'vae':['1',2]}},
              '10':{'class_type':'SaveImage','inputs':{'filename_prefix':'AI_SMM_Studio','images':['9',0]}}
            }
            payload=json.dumps({'prompt':graph,'client_id':uuid4().hex}).encode()
            reply=json.loads(self.request('http://127.0.0.1:8188/prompt',payload,{'Content-Type':'application/json'},60))
            if 'error' in reply: raise RuntimeError(str(reply['error']))
            pid=reply['prompt_id']
            for _ in range(180):
                time.sleep(2)
                hist=json.loads(self.request('http://127.0.0.1:8188/history/'+pid,timeout=25))
                if pid not in hist: continue
                item=hist[pid]
                if item.get('status',{}).get('status_str')=='error':
                    raise RuntimeError('ComfyUI сообщил об ошибке: '+str(item.get('status',{}).get('messages',[]))[-1500:])
                outputs=item.get('outputs',{}).get('10',{}).get('images',[])
                if not outputs: raise RuntimeError('ComfyUI завершил задачу без изображения.')
                from urllib.parse import urlencode
                output_info=outputs[0]
                params=urlencode({'filename':output_info['filename'],'subfolder':output_info.get('subfolder',''),'type':output_info.get('type','output')})
                result=Image.open(io.BytesIO(self.request('http://127.0.0.1:8188/view?'+params,timeout=60))).convert('RGB')
                result=result.resize(original.size,Image.Resampling.LANCZOS)
                # Explicitly keep unmasked pixels unchanged in the saved result.
                composite=Image.composite(result,original,mask.filter(ImageFilter.GaussianBlur(2)))
                self.output.parent.mkdir(parents=True,exist_ok=True)
                composite.save(self.output,'PNG')
                self.done.emit(str(self.output));return
            raise TimeoutError('ComfyUI не завершил обработку за 6 минут.')
        except Exception as exc: self.failed.emit(str(exc))

class BackgroundCompositeWorker(QThread):
    done=Signal(str)
    failed=Signal(str)
    def __init__(self, source, background, output, editable_mask):
        super().__init__()
        self.source,self.background,self.output,self.editable_mask=source,background,output,editable_mask
    def run(self):
        try:
            from PIL import ImageFilter
            with Image.open(self.source) as f: source=ImageOps.exif_transpose(f).convert('RGB')
            with Image.open(self.background) as f: bg=ImageOps.exif_transpose(f).convert('RGB')
            with Image.open(io.BytesIO(self.editable_mask)) as f: mask=f.convert('L')
            w,h=source.size
            # Cover background without distorting its aspect ratio.
            scale=max(w/bg.width,h/bg.height)
            nw,nh=round(bg.width*scale),round(bg.height*scale)
            bg=bg.resize((nw,nh),Image.Resampling.LANCZOS)
            left=(nw-w)//2;top=(nh-h)//2
            bg=bg.crop((left,top,left+w,top+h))
            mask=mask.resize((w,h),Image.Resampling.LANCZOS)
            # Editable region is background. Keep the source pixels exactly in protected area.
            # A narrow feather prevents harsh cut-out edges, without changing the person's interior.
            mask=mask.filter(ImageFilter.GaussianBlur(1.5))
            output=Image.composite(bg,source,mask)
            self.output.parent.mkdir(parents=True,exist_ok=True)
            output.save(self.output,'PNG')
            self.done.emit(str(self.output))
        except Exception as exc:self.failed.emit(str(exc))

def ai_python():
    return str(Path(__file__).resolve().parent / '.ai_env' / 'Scripts' / 'python.exe')

class CudaInstallWorker(QThread):
    progress=Signal(str)
    done=Signal()
    failed=Signal(str)
    def run(self):
        try:
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            started=time.monotonic()
            self.progress.emit('Проверяю существующее AI-окружение…')
            base=Path(__file__).resolve().parent
            py=ai_python()
            if Path(py).is_file():
                try:
                    probe=subprocess.run([py,'-c','import torch; assert torch.cuda.is_available(); print(torch.__version__)'],
                        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=40,creationflags=flags)
                    if probe.returncode==0:
                        self.progress.emit('CUDA PyTorch уже работает ('+probe.stdout.strip()+'). Проверяю библиотеки редактора…')
                        deps=subprocess.run([py,'-c','import diffusers,transformers,accelerate,safetensors,PIL'],
                            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=40,creationflags=flags)
                        if deps.returncode==0:
                            self.progress.emit('Все компоненты уже установлены. Повторная установка не нужна.')
                            self.done.emit();return
                        self.progress.emit('CUDA работает. Установлю только отсутствующие библиотеки редактора.')
                        cuda_ok=True
                    else:cuda_ok=False
                except Exception:cuda_ok=False
            else:cuda_ok=False
            if not Path(py).is_file():
                self.progress.emit('Создаю отдельное окружение AI на Python 3.11…')
                found=False
                for version in ('3.11','3.12'):
                    probe=subprocess.run(['py','-'+version,'-c','import sys; print(sys.executable)'],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=flags)
                    if probe.returncode==0:
                        command=['py','-'+version,'-m','venv',str(base/'.ai_env')]
                        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=flags)
                        if result.returncode:raise RuntimeError('Не удалось создать AI-окружение: '+result.stderr[-1500:])
                        found=True;break
                if not found:
                    raise RuntimeError('Нужен Python 3.11 или 3.12 x64. Установите его с python.org (или командой winget install Python.Python.3.11), затем повторите установку. Python 3.14 приложения менять не нужно.')
            def run_cmd(cmd,step):
                self.progress.emit(step)
                env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['PYTHONUTF8']='1'
                proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=flags,env=env)
                tail=[]
                for line in proc.stdout:
                    line=line.strip()
                    if line:
                        tail.append(line);tail=tail[-25:]
                        if any(x in line for x in ('Downloading','Installing','Successfully','ERROR','Looking in indexes','Requirement already satisfied','Collecting','Preparing','Building','Installing build dependencies','Using cached','━','%')):
                            self.progress.emit(f'[{int(time.monotonic()-started)} с] '+line[:350])
                if proc.wait():raise RuntimeError(step+' — ошибка:\n'+'\n'.join(tail)[-2500:])
            if not cuda_ok:
                run_cmd([py,'-m','pip','install','--upgrade','pip','--progress-bar','on'],'Обновляю pip в AI-окружении…')
                run_cmd([py,'-m','pip','install','torch','torchvision','--index-url','https://download.pytorch.org/whl/cu128','--progress-bar','on'],'Устанавливаю CUDA PyTorch в AI-окружение…')
            run_cmd([py,'-m','pip','install','diffusers>=0.35.0','transformers>=4.51,<5','accelerate','safetensors','sentencepiece','pillow','--progress-bar','on'],'Устанавливаю библиотеки редактора в AI-окружение…')
            test=subprocess.run([py,'-c','import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())'],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=flags,timeout=90)
            if test.returncode or not test.stdout.strip().endswith('True'):
                raise RuntimeError('Проверка CUDA не пройдена: '+(test.stdout+test.stderr)[-1800:])
            self.progress.emit('CUDA доступна в отдельном AI-окружении.')
            self.done.emit()
        except Exception as exc:self.failed.emit(str(exc))

class ModernLocalWorker(QThread):
    progress=Signal(str)
    done=Signal(str)
    failed=Signal(str)
    def __init__(self, source, reference, output, prompt, install=False, offload="sequential", steps=12, max_side=512, *, use_reference=None):
        super().__init__()
        self.offload,self.steps,self.max_side=offload,steps,max_side
        self.source,self.reference,self.output,self.prompt,self.install=source,(reference if (mentions_reference(prompt) if use_reference is None else use_reference) else None),output,prompt,install
        self.proc=None
        self.cancel_requested=threading.Event()
        self.started_at=time.monotonic()
    def cancel(self):
        self.cancel_requested.set()
        if self.proc is not None and self.proc.poll() is None:
            try:
                subprocess.run(["taskkill","/PID",str(self.proc.pid),"/T","/F"],capture_output=True,timeout=10,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)) if os.name=="nt" else self.proc.terminate()
            except Exception: pass
    def run(self):
        try:
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            if self.install:
                raise RuntimeError('AI-окружение не готово. В «AI-движки» запустите «Установить CUDA-сборку PyTorch».')
            # Validate CUDA before any model-weight download. The default PyPI torch wheel may be CPU-only.
            check=subprocess.run([ai_python(),'-c',
                'import torch; print("torch="+torch.__version__); print("cuda_build="+str(torch.version.cuda)); print("cuda_available="+str(torch.cuda.is_available()))'],
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45,creationflags=flags)
            if check.returncode:
                raise RuntimeError('Ошибка проверки PyTorch: '+(check.stderr or check.stdout)[-1000:])
            self.progress.emit('Диагностика GPU: '+check.stdout.strip().replace('\n','; '))
            if 'cuda_available=True' not in check.stdout:
                raise RuntimeError('PyTorch не видит CUDA. Загрузка весов Qwen остановлена. '
                    'Проверьте совместимость CUDA-сборки PyTorch с Python и драйвером NVIDIA. '
                    'В Stage 28 доступна отдельная диагностика GPU; переустановка без подтверждения не выполняется.')
            env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'}
            def execute(cmd):
                proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                    encoding='utf-8',errors='replace',creationflags=flags,env=env)
                self.proc=proc
                lines=[];model_path=None
                for line in proc.stdout:
                    line=line.strip()
                    if self.cancel_requested.is_set():
                        self.cancel();break
                    if not line:continue
                    lines.append(line);lines=lines[-30:]
                    try:
                        payload=json.loads(line)
                        if 'stage' in payload:self.progress.emit(payload['stage'])
                        if 'error' in payload:self.progress.emit('Ошибка: '+payload['error'])
                        if 'model_path' in payload:model_path=payload['model_path']
                    except json.JSONDecodeError:
                        if any(x in line.lower() for x in ('download','loading','fetch','safetensors','model-','%|','% ')):
                            self.progress.emit(line[-180:])
                code=proc.wait()
                if self.cancel_requested.is_set():return None
                if code:
                    diagnostic=f'Код завершения процесса: {code} (Windows NTSTATUS: 0x{code & 0xffffffff:08X}). '
                    if (code & 0xffffffff) in (0xC0000017,0xC000012D):
                        diagnostic+='Windows сообщает о нехватке памяти/лимита выделения. Увеличьте файл подкачки или используйте облегчённую модель. '
                    elif (code & 0xffffffff)==0xC0000005:
                        diagnostic+='Сбой доступа к памяти в нативной библиотеке; возможны RAM/VRAM или несовместимость. '
                    if any('out of memory' in x.lower() or 'cudaerrormemoryallocation' in x.lower() for x in lines):
                        raise RuntimeError('CUDA out of memory (подробности в журнале процесса).')
                    raise RuntimeError(diagnostic+'Последние сообщения: '+' '.join(lines)[-1400:])
                return model_path
            model_path=execute([ai_python(),'-u',str(Path(__file__).with_name('model_download.py'))])
            if self.cancel_requested.is_set():
                self.failed.emit('Загрузка отменена. Скачанные части сохранены для продолжения.')
                return
            if not model_path:raise RuntimeError('Загрузчик не вернул путь к проверенной модели.')
            self.progress.emit('Модель скачана. Запускаю редактирование фотографии…')
            cmd=[ai_python(),'-u',str(Path(__file__).with_name('modern_local_editor.py')),
                 '--model-path',model_path,'--offload',self.offload,'--steps',str(self.steps),'--max-side',str(self.max_side),'--source',self.source,'--output',str(self.output),'--prompt',self.prompt]
            if self.reference:cmd+=['--reference',self.reference]
            try:
                execute(cmd)
            except RuntimeError as exc:
                detail=str(exc)
                if ('out of memory' in detail.lower() or 'cudaerrormemoryallocation' in detail.lower()) and self.offload in ('model', 'group') and not self.cancel_requested.is_set():
                    self.progress.emit('ВНИМАНИЕ: быстрый режим не поместился в VRAM. Автоматический переход на МЕДЛЕННЫЙ sequential offload; оценка времени предыдущего режима больше не действует.')
                    fallback=[ai_python(),'-u',str(Path(__file__).with_name('modern_local_editor.py')),
                        '--model-path',model_path,'--offload','sequential','--steps',str(min(self.steps,8)),
                        '--max-side',str(min(self.max_side,512)),'--source',self.source,'--output',str(self.output),'--prompt',self.prompt]
                    if self.reference:fallback+=['--reference',self.reference]
                    try:
                        execute(fallback)
                    except RuntimeError as fallback_exc:
                        if 'out of memory' in str(fallback_exc).lower() or 'cudaerrormemoryallocation' in str(fallback_exc).lower():
                            raise RuntimeError('Недостаточно памяти даже в совместимом режиме. Закройте другие GPU-программы, освободите RAM/VRAM или используйте облачную обработку. Исходник не изменён.') from fallback_exc
                        raise
                elif 'out of memory' in detail.lower() or 'cudaerrormemoryallocation' in detail.lower():
                    raise RuntimeError('Недостаточно памяти для Qwen Image Edit. Освободите RAM/VRAM, закройте другие GPU-программы или используйте облачную обработку. Исходник не изменён.') from exc
                elif self.offload == 'group' and not self.cancel_requested.is_set():
                    self.progress.emit('ВНИМАНИЕ: group offload недоступен. Переход на МЕДЛЕННЫЙ sequential offload.')
                    fallback=[ai_python(),'-u',str(Path(__file__).with_name('modern_local_editor.py')),
                        '--model-path',model_path,'--offload','sequential','--steps',str(min(self.steps,8)),
                        '--max-side',str(min(self.max_side,512)),'--source',self.source,'--output',str(self.output),'--prompt',self.prompt]
                    if self.reference:fallback+=['--reference',self.reference]
                    execute(fallback)
                else:
                    raise
            if self.cancel_requested.is_set():
                self.failed.emit("Обработка отменена пользователем.")
                return
            if not Path(self.output).is_file():
                raise RuntimeError('Редактор не создал выходной файл.')
            self.done.emit(str(self.output))
        except Exception as exc:self.failed.emit(str(exc))

class CloudEditWorker(QThread):
    done=Signal(str)
    failed=Signal(str)
    def __init__(self, source, reference, output, instruction, api_key, *, use_reference=None):
        super().__init__()
        self.source,self.reference,self.output,self.instruction,self.api_key=source,(reference if (mentions_reference(instruction) if use_reference is None else use_reference) else None),output,instruction,api_key
    def run(self):
        try:
            # The worker itself enforces the paid-call gate, even if invoked
            # without the GUI or from a stale queued batch.
            from paid_provider_boundary import require_priced_paid_call
            require_priced_paid_call('openai', 'image_edit')
            boundary='AISMM'+uuid4().hex
            chunks=[]
            def field(name, value):
                chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode())
            def image_field(path):
                with Image.open(path) as im:
                    im=ImageOps.exif_transpose(im)
                    if max(im.size)>2048: im.thumbnail((2048,2048))
                    buf=io.BytesIO()
                    if 'A' in im.getbands() or 'transparency' in im.info:
                        im.convert('RGBA').save(buf,'PNG',optimize=True)
                        ext,mime='png','image/png'
                    else:
                        im.convert('RGB').save(buf,'JPEG',quality=92,optimize=True,subsampling=0)
                        ext,mime='jpg','image/jpeg'
                chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="source.{ext}"\r\nContent-Type: {mime}\r\n\r\n').encode()+buf.getvalue()+b'\r\n')
            field('model','gpt-image-1')
            field('prompt',self.instruction)
            field('size','auto')
            image_field(self.source)
            if self.reference: image_field(self.reference)
            chunks.append(f'--{boundary}--\r\n'.encode())
            req=urllib.request.Request('https://api.openai.com/v1/images/edits',data=b''.join(chunks),
                headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
            try:
                with urllib.request.urlopen(req,timeout=300) as response: result=json.load(response)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f'OpenAI API HTTP {exc.code}; содержимое ответа сервера скрыто') from None
            images=result.get('data',[])
            if not images: raise RuntimeError('API не вернул изображение.')
            if 'b64_json' not in images[0]: raise RuntimeError('API вернул неожиданный формат ответа.')
            raw=base64.b64decode(images[0]['b64_json'],validate=True)
            self.output.parent.mkdir(parents=True,exist_ok=True)
            staging=self.output.with_name('.'+self.output.stem+'_'+uuid4().hex+'.png')
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    im.load()
                    im.save(staging,'PNG')
                with Image.open(staging) as check:
                    check.verify()
                os.replace(staging,self.output)
            finally:
                staging.unlink(missing_ok=True)
            self.done.emit(str(self.output))
        except Exception as exc:self.failed.emit(str(exc))

class HuggingFaceSpaceWorker(QThread):
    """Call a user-selected public Gradio Space; no automatic paid fallback."""
    done=Signal(str)
    failed=Signal(str)
    def __init__(self, source, output, instruction, space_id, api_name):
        super().__init__()
        self.source,self.output,self.instruction=source,output,instruction
        self.space_id,self.api_name=space_id,api_name
    def run(self):
        try:
            from gradio_client import Client, handle_file
            client=Client(self.space_id)
            # Spaces do not share a universal API. This integration supports
            # endpoints whose first two parameters are image and prompt.
            result=client.predict(handle_file(str(self.source)), self.instruction,
                                  api_name=self.api_name)
            def candidate(obj):
                if isinstance(obj, str): return obj
                if isinstance(obj, dict):
                    for key in ('path','url','image'):
                        if key in obj:
                            found=candidate(obj[key])
                            if found:return found
                if isinstance(obj,(tuple,list)):
                    for item in obj:
                        found=candidate(item)
                        if found:return found
                return None
            location=candidate(result)
            if not location: raise RuntimeError('Space не вернул изображение в поддерживаемом формате.')
            if location.startswith(('http://','https://')):
                from urllib.parse import urlparse
                parsed=urlparse(location)
                if parsed.scheme!='https' or not (parsed.hostname or '').endswith(('.hf.space','huggingface.co')):
                    raise RuntimeError('Space вернул URL вне Hugging Face; загрузка отклонена.')
                with urllib.request.urlopen(location,timeout=120) as response:
                    data=response.read(30*1024*1024+1)
                if len(data)>30*1024*1024: raise RuntimeError('Ответ слишком большой.')
                picture=Image.open(io.BytesIO(data))
            else:
                picture=Image.open(location)
            self.output.parent.mkdir(parents=True,exist_ok=True)
            with picture as im: im.convert('RGB').save(self.output,'PNG')
            self.done.emit(str(self.output))
        except ImportError:
            self.failed.emit('Не установлен gradio_client. Выполните в окружении приложения: python -m pip install gradio_client')
        except Exception as exc:self.failed.emit(str(exc))

class DownloadModelWorker(QThread):
    progress = Signal(str, int)
    done = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    def __init__(self, model):
        super().__init__()
        self.model = model
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            payload=json.dumps({'name':self.model,'stream':True}).encode('utf-8')
            request=urllib.request.Request('http://127.0.0.1:11434/api/pull',
                data=payload, headers={'Content-Type':'application/json'},method='POST')
            self.progress.emit('Подключение к Ollama…',-1)
            with urllib.request.urlopen(request,timeout=90) as response:
                for line in response:
                    if self._cancel:
                        self.cancelled.emit()
                        return
                    if not line.strip():continue
                    info=json.loads(line)
                    if info.get('error'):
                        raise RuntimeError(info['error'])
                    status=info.get('status','Загрузка…')
                    completed=info.get('completed')
                    total=info.get('total')
                    percent=int(completed*100/total) if isinstance(completed,(int,float)) and isinstance(total,(int,float)) and total>0 else -1
                    detail=f'{completed/1048576:.0f} / {total/1048576:.0f} МБ' if total and completed is not None else ''
                    self.progress.emit(f'{status}  {detail}'.strip(),percent)
            if self._cancel:self.cancelled.emit()
            else:self.done.emit(self.model)
        except urllib.error.URLError as exc:
            self.failed.emit('Не удалось подключиться к Ollama на 127.0.0.1:11434. Убедитесь, что Ollama запущена. '+str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))

class InstallSegmentationWorker(QThread):
    progress=Signal(str)
    done=Signal()
    failed=Signal(str)
    def run(self):
        try:
            self.progress.emit('Скачиваю rembg и ONNX Runtime. Подождите…')
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            proc=subprocess.run([sys.executable,'-m','pip','install','rembg[cpu]>=2.0.60,<3'],
                capture_output=True,text=True,timeout=900,creationflags=flags, encoding='utf-8', errors='replace')
            if proc.returncode:
                raise RuntimeError((proc.stderr or proc.stdout)[-1800:])
            self.progress.emit('Библиотеки установлены. Готовлю модель…')
            self.done.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

class InstallOpenCVWorker(QThread):
    """Install optional OpenCV outside the GUI thread; never accept implicit installs."""
    done = Signal()
    failed = Signal(str)
    def run(self):
        try:
            proc = subprocess.run(
                [sys.executable, '-m', 'pip', 'install',
                 'opencv-python-headless>=4.8,<5', 'numpy>=1.26,<3'],
                capture_output=True, text=True, timeout=240,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), encoding='utf-8', errors='replace')
            if proc.returncode:
                raise RuntimeError((proc.stderr or proc.stdout)[-1800:])
            self.done.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

class NeuralMaskWorker(QThread):
    """Neural foreground matting with rembg/u2net; no image uploads."""
    done = Signal(bytes)
    failed = Signal(str)
    progress = Signal(str)
    def __init__(self, image_path):
        super().__init__()
        self.image_path=image_path
    def run(self):
        try:
            from rembg import remove, new_session
            self.progress.emit('Загружаю модель сегментации (первый запуск может скачивать её)…')
            session=new_session('u2net')
            self.progress.emit('Выделяю человека и уточняю контуры…')
            with Image.open(self.image_path) as im:
                source=ImageOps.exif_transpose(im).convert('RGB')
            source.thumbnail((1600,1600))
            rgba=remove(source,session=session).convert('RGBA')
            alpha=rgba.getchannel('A')
            # Slightly extend protected subject so inpainting cannot modify its edges.
            from PIL import ImageFilter
            alpha=alpha.filter(ImageFilter.MaxFilter(5))
            editable=ImageOps.invert(alpha)
            if not (0.03 < sum(editable.resize((32,32)).getdata())/(32*32*255) < 0.97):
                raise ValueError('Нейросеть не смогла уверенно выделить объект. Генерация не запущена.')
            out=io.BytesIO()
            editable.save(out,'PNG')
            self.done.emit(out.getvalue())
        except Exception as exc:
            self.failed.emit(str(exc))

class SmartMaskWorker(QThread):
    """GrabCut foreground refinement from a loose user stroke or a centered box."""
    done = Signal(bytes)
    failed = Signal(str)
    def __init__(self, image_path, strokes_png, mode):
        super().__init__()
        self.image_path, self.strokes_png, self.mode = image_path, strokes_png, mode
    def run(self):
        try:
            import cv2
            import numpy as np
            with Image.open(self.image_path) as im:
                photo=ImageOps.exif_transpose(im).convert('RGB')
            photo.thumbnail((768,768))
            arr=np.asarray(photo)
            h,w=arr.shape[:2]
            if w<16 or h<16: raise ValueError('Изображение слишком маленькое.')
            mask=np.full((h,w),cv2.GC_PR_BGD,dtype=np.uint8)
            if self.mode=='stroke':
                raw=Image.open(io.BytesIO(self.strokes_png)).convert('L').resize((w,h),Image.Resampling.NEAREST)
                strokes=np.asarray(raw)>80
                ys,xs=np.where(strokes)
                if len(xs)<12: raise ValueError('Грубо закрасьте объект кистью, затем повторите.')
                x1,x2=max(0,int(xs.min())-20),min(w,int(xs.max())+21)
                y1,y2=max(0,int(ys.min())-20),min(h,int(ys.max())+21)
                mask[y1:y2,x1:x2]=cv2.GC_PR_FGD
                # Scribbles are foreground hints, not editable background.
                mask[strokes]=cv2.GC_FGD
            else:
                # An automatic center guess, explicitly not guaranteed.
                x1,y1,x2,y2=int(w*.16),int(h*.06),int(w*.84),int(h*.96)
                mask[y1:y2,x1:x2]=cv2.GC_PR_FGD
                mask[int(h*.25):int(h*.75),int(w*.35):int(w*.65)]=cv2.GC_FGD
            mask[:2,:]=cv2.GC_BGD;mask[-2:,:]=cv2.GC_BGD
            mask[:,:2]=cv2.GC_BGD;mask[:,-2:]=cv2.GC_BGD
            bg=np.zeros((1,65),np.float64); fg=np.zeros((1,65),np.float64)
            cv2.grabCut(cv2.cvtColor(arr,cv2.COLOR_RGB2BGR),mask,None,bg,fg,6,cv2.GC_INIT_WITH_MASK)
            foreground=np.where((mask==cv2.GC_FGD)|(mask==cv2.GC_PR_FGD),255,0).astype('uint8')
            if foreground.mean()<2 or foreground.mean()>248: raise ValueError('Не удалось уверенно отделить объект. Сделайте грубую пометку по человеку.')
            # Protect the foreground with a small margin.
            foreground=cv2.dilate(foreground,np.ones((5,5),np.uint8),iterations=1)
            background=255-foreground
            out=io.BytesIO(); Image.fromarray(background).save(out,'PNG')
            self.done.emit(out.getvalue())
        except Exception as exc:
            self.failed.emit(str(exc))

class MaskCanvas(QWidget):
    """Paint white editable regions on a transparent mask, in the main workspace."""
    def __init__(self):
        super().__init__()
        self.image = None
        self.mask = None
        self.brush = 36
        self.erase = False
        self.last = None
        self.setMinimumSize(300, 250)

    def set_image(self, path):
        self.image = None
        self.mask = None
        if path and Path(path).suffix.lower() in IMAGE_EXTENSIONS:
            with Image.open(path) as src:
                src = ImageOps.exif_transpose(src).convert('RGB')
                src.thumbnail((768, 768))
                b = io.BytesIO(); src.save(b, 'PNG')
            self.image = QImage.fromData(b.getvalue(), 'PNG').convertToFormat(QImage.Format_RGB32)
            self.mask = QImage(self.image.size(), QImage.Format_ARGB32)
            self.mask.fill(Qt.transparent)
        self.update()

    def set_mask_from_pil(self, mask):
        """Apply a white editable-region mask at canvas resolution."""
        if self.image is None:
            return
        from PySide6.QtCore import QBuffer, QIODevice
        mask=mask.convert('L').resize((self.image.width(),self.image.height()),Image.Resampling.LANCZOS)
        rgba=Image.new('RGBA',mask.size,(255,255,255,0))
        rgba.putalpha(mask)
        buf=io.BytesIO()
        rgba.save(buf,'PNG')
        self.mask=QImage.fromData(buf.getvalue(),'PNG').convertToFormat(QImage.Format_ARGB32)
        self.update()

    def clear_mask(self):
        if self.mask: self.mask.fill(Qt.transparent); self.update()

    def has_mask(self):
        if self.mask is None: return False
        for y in range(self.mask.height()):
            for x in range(self.mask.width()):
                if self.mask.pixelColor(x,y).alpha() > 10: return True
        return False

    def mask_png(self):
        from PySide6.QtCore import QBuffer, QIODevice
        if self.mask is None: return None
        output = QImage(self.mask.size(), QImage.Format_RGB32)
        output.fill(Qt.black)
        painter = QPainter(output)
        painter.drawImage(0,0,self.mask)
        painter.end()
        # Use alpha channel from drawing, not RGB, for robust white-on-black masks.
        from PIL import Image as PILImage
        b=QBuffer(); b.open(QIODevice.WriteOnly); self.mask.save(b,'PNG')
        rgba=PILImage.open(io.BytesIO(bytes(b.data()))).convert('RGBA')
        result=PILImage.new('L',rgba.size,0); result.putdata([a for *_,a in rgba.getdata()])
        out=io.BytesIO(); result.save(out,'PNG'); return out.getvalue()

    def rect_image(self):
        if self.image is None: return None
        scale=min(self.width()/self.image.width(),self.height()/self.image.height())
        w=int(self.image.width()*scale); h=int(self.image.height()*scale)
        return ((self.width()-w)//2,(self.height()-h)//2,w,h)

    def paintEvent(self,event):
        painter=QPainter(self); painter.fillRect(self.rect(),QColor('#222831'))
        if self.image is None:
            painter.setPen(Qt.white); painter.drawText(self.rect(),Qt.AlignCenter,'Выберите исходную фотографию'); return
        from PySide6.QtCore import QRect
        x,y,w,h=self.rect_image(); rect=QRect(x,y,w,h)
        painter.drawImage(rect,self.image)
        if self.mask is not None:
            overlay=QImage(self.mask.size(),QImage.Format_ARGB32); overlay.fill(Qt.transparent)
            op=QPainter(overlay); op.setCompositionMode(QPainter.CompositionMode_Source)
            op.drawImage(0,0,self.mask); op.setCompositionMode(QPainter.CompositionMode_SourceIn)
            op.fillRect(overlay.rect(),QColor(255,65,65,115)); op.end()
            painter.drawImage(rect,overlay)

    def point(self,event):
        rect=self.rect_image()
        if rect is None: return None
        x,y,w,h=rect; pos=event.position()
        if not (x<=pos.x()<x+w and y<=pos.y()<y+h): return None
        return (round((pos.x()-x)*self.image.width()/w),round((pos.y()-y)*self.image.height()/h))

    def stroke(self,pt):
        if pt is None or self.mask is None:return
        painter=QPainter(self.mask)
        painter.setCompositionMode(QPainter.CompositionMode_Clear if self.erase else QPainter.CompositionMode_Source)
        pen=QPen(QColor(255,255,255,255),self.brush,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin)
        painter.setPen(pen)
        prev=self.last or pt; painter.drawLine(prev[0],prev[1],pt[0],pt[1]); painter.end()
        self.last=pt; self.update()

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton: self.last=None; self.stroke(self.point(event))
    def mouseMoveEvent(self,event):
        if event.buttons() & Qt.LeftButton: self.stroke(self.point(event))
    def mouseReleaseEvent(self,event): self.last=None

class ProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Новый проект')
        self.resize(460, 330)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText('Название клиента или проекта')
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        self.description = QTextEdit()
        self.description.setPlaceholderText('Что делает компания, какой стиль и задачи важны? Необязательно.')
        form.addRow('Название:', self.name)
        form.addRow('Направление:', self.category)
        form.addRow('Описание:', self.description)
        lay.addLayout(form)
        btn = QPushButton('Создать проект')
        btn.clicked.connect(self.accept_valid)
        lay.addWidget(btn)
    def accept_valid(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, 'Название', 'Введите название проекта.')
        else: self.accept()

class DropButton(QPushButton):
    file_dropped = Signal(list)
    def __init__(self, title):
        super().__init__(title)
        self.setAcceptDrops(True)
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.file_dropped.emit(paths)
            event.acceptProposedAction()

class DropList(QListWidget):
    files_dropped = Signal(list)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setToolTip('Перетащите сюда файлы для импорта в проект')
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
        else: super().dragEnterEvent(event)
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: super().dragMoveEvent(event)
    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else: super().dropEvent(event)

class DropWorkspace(QWidget):
    files_dropped = Signal(list)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setToolTip('Перетащите файл на свободное место для импорта и открытия')
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

class Preview(QWidget):
    file_dropped = Signal(list)
    def __init__(self, title, allow_drop=False):
        super().__init__()
        self.setAcceptDrops(allow_drop)
        self.allow_drop = allow_drop
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(title))
        self.image = QLabel('Выберите изображение')
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(180, 170)
        self.image.setStyleSheet('background:#252b34; border:1px solid #404852; border-radius:6px; color:#cbd5e1;')
        lay.addWidget(self.image, 1)
        self.filename = QLabel('—')
        self.filename.setWordWrap(True)
        lay.addWidget(self.filename)
        self.path = None
        if allow_drop:
            self.image.setText('Перетащите файл сюда\nили выберите из проекта')
    def dragEnterEvent(self, event):
        if self.allow_drop and event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
    def dragMoveEvent(self, event):
        if self.allow_drop and event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths and self.allow_drop:
            self.file_dropped.emit(paths)
            event.acceptProposedAction()
    def show_file(self, path):
        self.path = path
        self.filename.setText(Path(path).name if path else '—')
        self.refresh()
    def refresh(self):
        if not self.path:
            self.image.setPixmap(QPixmap())
            self.image.setText('Нет изображения')
            return
        pix = QPixmap(str(self.path))
        if pix.isNull():
            self.image.setPixmap(QPixmap())
            self.image.setText('Предпросмотр недоступен\n(видео/аудио или неподдерживаемый формат)')
        else:
            self.image.setPixmap(pix.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

class DocumentDragList(QListWidget):
    """Drag a document id (not its title) to a destination section."""
    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item is None or not item.data(Qt.UserRole):
            return
        mime = QMimeData()
        mime.setData('application/x-ai-smm-document', str(item.data(Qt.UserRole)).encode('ascii'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

class SectionDropButton(QPushButton):
    def __init__(self, title, kind, owner):
        super().__init__(title)
        self.kind = kind
        self.owner = owner
        self.setAcceptDrops(kind in ('strategy','plans','calendar','tasks','analytics'))
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/x-ai-smm-document'):
            event.acceptProposedAction()
        else:
            event.ignore()
    def dropEvent(self, event):
        try:
            doc_id = bytes(event.mimeData().data('application/x-ai-smm-document')).decode('ascii')
        except (ValueError, UnicodeDecodeError):
            event.ignore()
            return
        if self.owner.relocate_document(doc_id, self.kind):
            event.acceptProposedAction()
        else:
            event.ignore()

class TaskComposer(QTextEdit):
    """Multiline composer: Enter sends, Shift+Enter inserts a newline."""
    submitted = Signal()
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier)):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)

class Studio(QMainWindow):
    def _export_document_files(self, project_id, doc_id, kind, title, content,
                               scene=None, diagram_png=None):
        # Export and SQLite registry must resolve the same immutable location.
        return export_document_files(project_id, doc_id, kind, title, content,
            scene=scene, diagram_png=diagram_png,
            workspace_root=self.core.workspace(project_id))

    def __setattr__(self, name, value):
        if name in ('task_worker', 'dialogue_worker', 'report_worker', 'video_worker',
                    'cloud_worker', 'modern_worker', 'edit_worker', 'smart_mask_worker',
                    'neural_worker', 'generator', 'worker', 'cuda_install_worker',
                    'download_worker', 'smart_worker', 'vision_worker', 'neural_install', 'opencv_install'):
            from qt_worker_lifecycle import retain_replaced_worker
            retain_replaced_worker(self, name, value)
        super().__setattr__(name, value)

    def __init__(self):
        super().__init__()
        self.db = connect()
        from application_core import ApplicationRuntime
        from project_workspace_runtime import ProjectWorkspaceResolver
        self.core = ApplicationRuntime(self.db, ProjectWorkspaceResolver(self.db))
        self.core.configure_project_control(ROOT / 'backups')
        # One atomic startup fence for text/model and batch worker attempts.
        # No Qt-local interpretation of which incomplete projects to discard.
        self.core.recover_inflight()
        self.task_attempt = None
        self.current_project = None
        self.project_ids = []
        self.project_section_state = {}
        self._loading_document = False
        self._switching_project = False
        self.asset_paths = []
        self.worker = None
        self.task_worker = None
        self.dialogue_worker = None
        self._background_workers=[]
        self._mask_owner=None  # (launch project id, launch source path)
        self._mask_install_owner=None
        self._resolved_dialogue=None
        self._dialogue_error=None
        self.task_semantic_kind=None
        self.task_execution_claim=None
        self.task_dialogue_turn_id=None
        self._semantic_media_project=None
        self.report_worker = None
        self.video_worker = None
        self.edit_worker = None
        self.generator = None
        self._generator_owner = None
        self._edit_owner = None
        self.cloud_key = None  # Legacy compatibility; credential source is cloud_credentials (RAM/env)
        self.cloud_worker = None
        self.modern_worker = None
        self.batch_queue = []
        self.batch_total = 0
        self.batch_prompt = None
        self.batch_active = False
        self.batch_run_id = None
        self.batch_inflight = None
        self.batch_claim = None
        self.batch_owner_project = None
        self.modern_started_at = None
        self.modern_last_event = None
        self.modern_timer = QTimer(self)
        self.modern_timer.setInterval(5000)
        self.modern_timer.timeout.connect(self.modern_heartbeat)
        self.download_worker = None
        self.pending_background_prompt = None
        self.setWindowTitle(f'AI SMM Manager — {BUILD_LABEL}')
        self.setWindowTitle(self.windowTitle() + ' · ТЕСТОВЫЙ КАНДИДАТ — НЕ РЕЛИЗ')
        self.resize(1540, 920)
        self.setMinimumSize(1050, 680)
        self.apply_workspace_theme()
        main = QWidget()
        self.setCentralWidget(main)
        outer = QHBoxLayout(main)
        horizontal = QSplitter(Qt.Horizontal)
        self.main_splitter = horizontal
        horizontal.setChildrenCollapsible(False)
        outer.addWidget(horizontal)
        sidebar = QWidget()
        sl = QVBoxLayout(sidebar)
        sl.setSpacing(5)
        sl.setContentsMargins(8, 12, 8, 8)
        sl.setAlignment(Qt.AlignTop)
        sl.addWidget(QLabel('AI SMM MANAGER'))
        sl.addWidget(QLabel(f'Версия {APP_VERSION} · Stage {STAGE_NUMBER}'))
        sl.addWidget(QLabel('ПРОЕКТ'))
        self.projects = QComboBox()
        self.projects.currentIndexChanged.connect(self.select_project)
        self.projects.setToolTip('Переключить активный проект')
        self.projects.setFixedHeight(29)
        sl.addWidget(self.projects)
        create = QPushButton('+ Новый проект')
        create.clicked.connect(self.add_project)
        sl.addWidget(create)
        storage_btn = QPushButton('⚙ Папки проектов')
        storage_btn.clicked.connect(self.configure_storage)
        sl.addWidget(storage_btn)
        project_folder_btn = QPushButton('📂 Открыть папку проекта')
        project_folder_btn.clicked.connect(self.open_project_folder)
        sl.addWidget(project_folder_btn)
        sl.addWidget(QLabel('РАБОЧЕЕ ПРОСТРАНСТВО'))
        self.nav_buttons = {}
        for key, label in [('overview','⌂  Главная'), ('strategy','◎  Стратегии'),
                           ('plans','▦  Планы и схемы'), ('calendar','▤  Контент-планы'),
                           ('tasks','☑  Задачи'), ('analytics','▥  Аналитика'),
                           ('content','▣  Обработка контента'), ('publishing','◷  Публикации')]:
            btn = SectionDropButton(label, key, self)
            btn.clicked.connect(lambda checked=False, k=key: self.show_section(k))
            btn.setFixedHeight(30)
            sl.addWidget(btn)
            self.nav_buttons[key] = btn
        self.materials_header = QLabel('МАТЕРИАЛЫ')
        sl.addWidget(self.materials_header)
        self.assets = DropList()
        self.assets.files_dropped.connect(self.drop_library)
        self.assets.currentRowChanged.connect(self.select_asset)
        self.assets.setDragEnabled(True)
        self.assets.startDrag = self.drag_project_asset
        self.assets.setMinimumHeight(100)
        self.assets.setMaximumHeight(260)
        sl.addWidget(self.assets)
        imp = QPushButton('Импортировать исходники')
        imp.clicked.connect(self.import_assets)
        self.import_button = imp
        sl.addWidget(imp)
        self.materials_note = QLabel('Исходники не изменяются.')
        sl.addWidget(self.materials_note)
        horizontal.addWidget(sidebar)
        workspace = DropWorkspace()
        workspace.files_dropped.connect(self.drop_source)
        wl = QVBoxLayout(workspace)
        self.heading = QLabel('Выберите проект')
        self.heading.setStyleSheet('font-size:20px; font-weight:600;')
        topbar = QHBoxLayout()
        topbar.addWidget(self.heading)
        topbar.addStretch()
        self.ai_toggle = QPushButton('✦  AI Manager')
        self.ai_toggle.setObjectName('accentButton')
        self.ai_toggle.clicked.connect(self.toggle_ai_panel)
        topbar.addWidget(self.ai_toggle)
        wl.addLayout(topbar)
        vertical = QSplitter(Qt.Vertical)
        wl.addWidget(vertical, 1)
        # Workspace-first UI: reference | source(s) | result. No task-specific toolbar.
        self.comparison = QSplitter(Qt.Horizontal)
        self.comparison.setChildrenCollapsible(False)
        self.reference_panel = QWidget()
        ref_layout = QVBoxLayout(self.reference_panel)
        ref_header = QHBoxLayout()
        ref_header.addWidget(QLabel('ЭТАЛОН'))
        ref_header.addStretch()
        clear_ref = QPushButton('Скрыть')
        clear_ref.clicked.connect(self.hide_reference)
        ref_header.addWidget(clear_ref)
        ref_layout.addLayout(ref_header)
        self.reference = Preview('Перетащите эталон сюда', True)
        self.reference.file_dropped.connect(self.drop_reference)
        ref_layout.addWidget(self.reference, 1)
        self.comparison.addWidget(self.reference_panel)

        source_panel = QWidget()
        source_layout = QVBoxLayout(source_panel)
        source_header = QHBoxLayout()
        source_header.addWidget(QLabel('ИСХОДНИКИ'))
        source_header.addStretch()
        self.source_count = QLabel('0 файлов')
        source_header.addWidget(self.source_count)
        source_layout.addLayout(source_header)
        self.source = Preview('Перетащите исходники сюда', True)
        self.source.file_dropped.connect(self.drop_source)
        source_layout.addWidget(self.source, 1)
        self.source_list = DropList()
        self.source_list.files_dropped.connect(self.drop_source)
        self.source_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.source_list.setAcceptDrops(True)
        self.source_list.setDragEnabled(True)
        self.source_list.setToolTip('Ctrl/Shift для множественного выбора; импорт нескольких файлов из Проводника — в область исходников')
        self.source_list.setMinimumHeight(155)
        self.source_list.setViewMode(QListWidget.IconMode)
        self.source_list.setIconSize(QSize(84, 70))
        self.source_list.setResizeMode(QListWidget.Adjust)
        self.source_list.setMovement(QListWidget.Static)
        self.source_list.itemDoubleClicked.connect(lambda item: self.open_workspace_file('source'))
        self.source_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.source_list.customContextMenuRequested.connect(lambda point: self.workspace_file_menu('source', point))
        self.source_list.currentRowChanged.connect(self.select_working_source)
        self.source_list.setToolTip("Ctrl/Shift — выделить несколько файлов для пакетной обработки")
        source_layout.addWidget(self.source_list)
        source_actions = QHBoxLayout()
        remove_source_btn = QPushButton('Убрать выбранные')
        remove_source_btn.clicked.connect(self.remove_workspace_sources)
        source_actions.addWidget(remove_source_btn)
        source_layout.addLayout(source_actions)
        self.comparison.addWidget(source_panel)

        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.addWidget(QLabel('РЕЗУЛЬТАТ'))
        self.result = Preview('Результаты и версии')
        result_layout.addWidget(self.result, 1)
        self.result_list = QListWidget()
        self.result_list.setMinimumHeight(155)
        self.result_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.result_list.setViewMode(QListWidget.IconMode)
        self.result_list.setIconSize(QSize(84, 70))
        self.result_list.setResizeMode(QListWidget.Adjust)
        self.result_list.setMovement(QListWidget.Static)
        self.result_list.itemDoubleClicked.connect(lambda item: self.open_workspace_file('result'))
        self.result_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_list.customContextMenuRequested.connect(lambda point: self.workspace_file_menu('result', point))
        self.result_list.currentRowChanged.connect(self.select_result_version)
        open_result = QPushButton("Открыть папку результатов")
        open_result.clicked.connect(self.open_results_folder)
        result_layout.addWidget(open_result)
        result_layout.addWidget(self.result_list)
        remove_result_btn = QPushButton('Удалить выбранные результаты…')
        remove_result_btn.clicked.connect(self.remove_workspace_results)
        result_layout.addWidget(remove_result_btn)
        self.comparison.addWidget(result_panel)
        self.comparison.setSizes([340, 430, 430])
        # Photo and video workspaces share the same project library.
        self.media_workspace = QWidget()
        media_layout = QVBoxLayout(self.media_workspace)
        media_layout.setContentsMargins(0, 0, 0, 0)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('РАБОЧИЙ РЕЖИМ'))
        self.media_mode = QComboBox()
        self.media_mode.addItems(['Фото', 'Видео / Монтаж'])
        mode_row.addWidget(self.media_mode)
        mode_row.addStretch()
        media_layout.addLayout(mode_row)
        self.media_mode_pages = QStackedWidget()
        self.media_mode_pages.addWidget(self.comparison)
        self.video_workspace = QWidget()
        video_layout = QVBoxLayout(self.video_workspace)
        video_split = QSplitter(Qt.Horizontal)
        video_library_panel = QWidget()
        video_library_layout = QVBoxLayout(video_library_panel)
        video_library_layout.addWidget(QLabel('МАТЕРИАЛЫ ПРОЕКТА · фото, видео, аудио'))
        self.video_media_list = QListWidget()
        self.video_media_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.video_media_list.itemDoubleClicked.connect(self.preview_video_media)
        video_library_layout.addWidget(self.video_media_list)
        video_add = QPushButton('Добавить выбранное на монтажную шкалу')
        video_add.clicked.connect(self.add_video_timeline_items)
        video_library_layout.addWidget(video_add)
        video_split.addWidget(video_library_panel)
        video_edit_panel = QWidget()
        video_edit_layout = QVBoxLayout(video_edit_panel)
        video_edit_layout.addWidget(QLabel('ПРЕДПРОСМОТР'))
        self.video_preview = Preview('Выберите файл в библиотеке')
        video_edit_layout.addWidget(self.video_preview, 3)
        video_edit_layout.addWidget(QLabel('МОНТАЖНАЯ ШКАЛА · черновик'))
        self.video_timeline = QListWidget()
        self.video_timeline.setSelectionMode(QListWidget.ExtendedSelection)
        video_edit_layout.addWidget(self.video_timeline, 2)
        video_controls = QHBoxLayout()
        for title, handler in [('↑', self.video_timeline_up), ('↓', self.video_timeline_down), ('Убрать', self.video_timeline_remove)]:
            button = QPushButton(title)
            button.clicked.connect(handler)
            video_controls.addWidget(button)
        video_edit_layout.addLayout(video_controls)
        video_edit_layout.addWidget(QLabel('Черновик монтажа: экспорт и воспроизведение видео пока не подключены.'))
        video_split.addWidget(video_edit_panel)
        video_split.setSizes([330, 700])
        video_layout.addWidget(video_split)
        self.media_mode_pages.addWidget(self.video_workspace)
        media_layout.addWidget(self.media_mode_pages, 1)
        self.media_mode.currentIndexChanged.connect(self.switch_media_mode)
        self.section_stack = QStackedWidget()
        self.section_stack.addWidget(self.media_workspace)
        self.manager_page = QWidget()
        mp = QVBoxLayout(self.manager_page)
        self.section_title = QLabel('Обзор проекта')
        self.section_title.setStyleSheet('font-size:22px; font-weight:700;')
        mp.addWidget(self.section_title)
        self.section_hint = QLabel('Стратегии, планы, контент-планы и задачи сохраняются внутри проекта.')
        self.section_hint.setWordWrap(True)
        mp.addWidget(self.section_hint)
        self.document_list = DocumentDragList()
        self.document_list.setDragEnabled(True)
        self.document_list.setDragDropMode(QAbstractItemView.DragOnly)
        self.document_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.document_list.customContextMenuRequested.connect(self.document_context_menu)
        self.document_list.setToolTip('Правый клик: переименовать, переместить, удалить.\nПеретащите документ на нужный раздел слева.')
        self.document_list.currentRowChanged.connect(self.open_document)
        self.document_split = QSplitter(Qt.Horizontal)
        self.document_split.setChildrenCollapsible(False)
        self.document_list.setMinimumWidth(180)
        self.document_split.addWidget(self.document_list)
        doc_buttons = QHBoxLayout()
        new_doc = QPushButton('+ Создать документ')
        new_doc.clicked.connect(self.create_manager_document)
        doc_buttons.addWidget(new_doc)
        save_doc = QPushButton('Сохранить изменения')
        save_doc.clicked.connect(self.save_manager_document)
        doc_buttons.addWidget(save_doc)
        export_btn = QPushButton('Экспорт PDF')
        export_btn.clicked.connect(self.export_active_document)
        doc_buttons.addWidget(export_btn)
        self.revise_ai_button=QPushButton('Доработать комплект')
        self.revise_ai_button.setToolTip('Проверить и доработать комплект по сохранённому исходному заданию. Создаётся новая редакция.')
        self.revise_ai_button.clicked.connect(self.revise_saved_ai_task)
        doc_buttons.addWidget(self.revise_ai_button)
        move_doc = QPushButton('Переместить в раздел…')
        move_doc.clicked.connect(self.move_manager_document)
        doc_buttons.addWidget(move_doc)
        delete_doc = QPushButton('Удалить документ')
        delete_doc.setToolTip('Удаляет только выбранный документ текущего проекта')
        delete_doc.clicked.connect(self.delete_manager_document)
        doc_buttons.addWidget(delete_doc)
        self.delete_doc_button = delete_doc
        rename_doc = QPushButton('Переименовать')
        rename_doc.clicked.connect(self.rename_manager_document)
        doc_buttons.addWidget(rename_doc)
        self.document_detail = QWidget()
        self.document_detail_layout = QVBoxLayout(self.document_detail)
        self.document_detail_layout.addLayout(doc_buttons)
        self.document_title = QLineEdit()
        self.document_title.setPlaceholderText('Название документа')
        self.document_detail_layout.addWidget(self.document_title)
        self.document_editor = QTextEdit()
        self.document_editor.setPlaceholderText('План, стратегия, этапы, KPI, схема Mermaid или другой рабочий материал…')
        self.document_views = QTabWidget()
        self.document_views.addTab(self.document_editor, 'Редактировать Markdown')
        self.document_preview = QTextEdit()
        self.document_preview.setReadOnly(True)
        self.document_preview.setStyleSheet('QTextEdit { background:#ffffff; color:#17222d; padding:12px; }')
        self.document_preview.setPlaceholderText('Предпросмотр документа появится здесь.')
        self.document_views.addTab(self.document_preview, 'Документ · просмотр')
        self.document_editor.textChanged.connect(self.refresh_document_preview)
        self.diagram_view = QGraphicsView()
        self.diagram_view.setRenderHint(QPainter.Antialiasing)
        self.diagram_view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.document_views.addTab(self.diagram_view, 'Блок-схема')
        self.document_table = QTableWidget()
        self.document_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.document_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.document_views.addTab(self.document_table, 'Таблица данных')
        self.document_views.currentChanged.connect(self.refresh_document_diagram)
        self.document_views.currentChanged.connect(self.refresh_document_preview)
        self.document_detail_layout.addWidget(self.document_views, 1)
        self.document_split.addWidget(self.document_detail)
        self.document_split.setSizes([230, 850])
        mp.addWidget(self.document_split, 1)
        self.section_stack.addWidget(self.manager_page)
        self.dashboard = QWidget()
        dash_layout = QVBoxLayout(self.dashboard)
        dash_layout.setSpacing(18)
        dash_title = QLabel('Рабочее пространство')
        dash_title.setObjectName('pageTitle')
        dash_layout.addWidget(dash_title)
        self.dashboard_subtitle = QLabel('Проекты, документы и задачи в одном месте')
        self.dashboard_subtitle.setObjectName('muted')
        dash_layout.addWidget(self.dashboard_subtitle)
        self.dashboard_metrics = QHBoxLayout()
        dash_layout.addLayout(self.dashboard_metrics)
        dash_layout.addWidget(QLabel('РАЗДЕЛЫ ПРОЕКТА'))
        dash_grid = QVBoxLayout()
        for row in [
            [('strategy','Стратегии','Цели, аудитория, KPI'),('plans','Планы и схемы','Дорожные карты и связи')],
            [('calendar','Контент-планы','Публикации и сценарии'),('content','Обработка контента','Фото, видео, Reels')],
            [('tasks','Задачи','Исполнение и сроки'),('analytics','Аналитика','Результаты и отчёты')]
        ]:
            h = QHBoxLayout()
            for key,title,description in row:
                b = QPushButton(f'{title}\n{description}')
                b.setObjectName('dashboardCard')
                b.setMinimumHeight(98)
                b.clicked.connect(lambda checked=False,k=key:self.show_section(k))
                h.addWidget(b)
            dash_grid.addLayout(h)
        dash_layout.addLayout(dash_grid)
        dash_layout.addStretch()
        self.section_stack.addWidget(self.dashboard)
        self.build_publishing_page()
        vertical.addWidget(self.section_stack)
        self.active_section = 'overview'
        self.document_ids = []
        self.active_document_id = None

        # Legacy processing primitives are retained internally for later agent tools,
        # but are not exposed as buttons or scenario-specific tabs.
        self.mask_canvas = MaskCanvas()
        self.mask_instruction = QLineEdit()
        self.generate_button = QPushButton()
        self.process_button = QPushButton()
        self.vision_button = QPushButton()
        self.refine_button = QPushButton()
        self.tabs = QTabWidget()
        self.mask_tab_index = 0
        self.text_editor = QTextEdit()

        # Full AI workspace: adjustable width, multiline task composer, separate technical log.
        chat_area = QWidget()
        chat_l = QVBoxLayout(chat_area)
        chat_l.setContentsMargins(8, 8, 8, 8)
        header = QHBoxLayout()
        header.addWidget(QLabel('AI-МЕНЕДЖЕР · задания и диалог'))
        self.ai_status = QLabel('Готов')
        header.addStretch()
        header.addWidget(self.ai_status)
        self.report_button=QPushButton('Собрать отчёт')
        self.report_button.setToolTip('Один ZIP: задания, контент, проверки, ошибки и логи выбранного проекта')
        self.report_button.clicked.connect(self.export_support_report)
        header.addWidget(self.report_button)
        chat_l.addLayout(header)
        self.chat_area = chat_area
        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setStyleSheet('QTextEdit { font-size: 14px; }')
        self.chat.setPlaceholderText('Здесь отображается диалог с AI-менеджером.')
        chat_l.addWidget(self.chat, 1)
        self.technical_log = QTextEdit()
        self.technical_log.setReadOnly(True)
        self.technical_log.setPlaceholderText('Загрузка моделей, GPU и технические события…')
        self.technical_log.setMinimumHeight(100)
        self.technical_log.setVisible(False)
        self.log_toggle = QPushButton('▸ Технический журнал')
        self.log_toggle.clicked.connect(self.toggle_technical_log)
        chat_l.addWidget(self.log_toggle)
        chat_l.addWidget(self.technical_log, 1)
        chat_l.addWidget(QLabel('ЗАДАНИЕ ДЛЯ AI'))
        self.message = TaskComposer()
        self.message.setPlaceholderText('Опишите задачу подробно…\nShift+Enter — новая строка, Enter — отправить')
        self.message.setMinimumHeight(110)
        self.message.setMaximumHeight(230)
        self.message.submitted.connect(self.send_message)
        chat_l.addWidget(self.message)
        command = QHBoxLayout()
        send = QPushButton('➤ Отправить задание')
        send.setObjectName('accentButton')
        send.clicked.connect(self.send_message)
        self.send_button = send
        command.addWidget(send, 1)
        batch = QPushButton('Обработать выбранные')
        batch.setToolTip('Выделите несколько исходников через Ctrl/Shift и введите команду')
        batch.clicked.connect(self.start_batch)
        command.addWidget(batch)
        mic = QPushButton('🎤')
        mic.setToolTip('Надиктовать текст в поле сообщения через голосовой ввод Windows')
        mic.clicked.connect(self.start_dictation)
        command.addWidget(mic)
        chat_l.addLayout(command)
        horizontal.addWidget(workspace)
        horizontal.addWidget(chat_area)
        sidebar.setMinimumWidth(170)
        sidebar.setMaximumWidth(300)
        workspace.setMinimumWidth(360)
        chat_area.setMinimumWidth(350)
        # User can drag the separator to expand the AI panel as needed.
        horizontal.setSizes([215, 760, 565])
        self.chat_area.setVisible(True)
        self.ai_toggle.setText('✦  Скрыть AI')
        self.reference_pick = QPushButton('Добавить эталон')
        self.reference_pick.clicked.connect(self.pick_reference)
        sl.addWidget(self.reference_pick)
        self.components_button = QPushButton('Проверить AI-компоненты')
        self.components_button.clicked.connect(self.check_components)
        sl.addWidget(self.components_button)
        self.engines_button=QPushButton('AI-движки · локально / облако')
        self.engines_button.clicked.connect(self.show_engines)
        sl.addWidget(self.engines_button)
        self.cancel_ai_button = QPushButton('Остановить AI-обработку')
        self.cancel_ai_button.setEnabled(False)
        self.cancel_ai_button.clicked.connect(self.cancel_modern)
        sl.addWidget(self.cancel_ai_button)
        self.gpu_button = QPushButton('Проверить GPU')
        self.gpu_button.clicked.connect(self.check_gpu)
        sl.addWidget(self.gpu_button)
        # Keep the project switcher and navigation at the top on every page.
        # Spare height is consumed only by this spacer, not by the project area.
        sl.addStretch(1)
        self.statusBar().showMessage(f'Версия {APP_VERSION} · Данные: {ROOT}')
        self.refresh_projects()
        self.show_section('overview')

    def show_section(self, key):
        if getattr(self, 'active_section', None) != key:
            if self.save_document_if_changed() is False:
                return
        self.active_section = key
        if hasattr(self,'revise_ai_button'):self.revise_ai_button.setVisible(key in ('strategy','calendar'))
        for name in ('reference_pick','components_button','engines_button','cancel_ai_button','gpu_button','materials_header','assets','import_button','materials_note'):
            if hasattr(self, name):
                getattr(self, name).setVisible(key == 'content')
        if self.current_project:
            self.project_section_state[self.current_project] = key
        if key in ('content', 'overview', 'publishing'):
            self.active_document_id = None
            self.document_list.blockSignals(True)
            self.document_list.clear()
            self.document_list.blockSignals(False)
            self.document_ids = []
            self._loading_document = True
            self.document_title.clear()
            self.document_editor.clear()
            self._loading_document = False
        if key == 'content':
            self.section_stack.setCurrentIndex(0)
            self.heading.setText('Обработка контента · фото, видео, Reels, ролики')
            self.highlight_navigation(key)
            return
        if key == 'publishing':
            self.section_stack.setCurrentWidget(self.publishing_page)
            self.heading.setText('Публикации · готовый контент и расписание')
            self.refresh_publications()
            self.highlight_navigation(key)
            return
        if key == 'overview':
            self.section_stack.setCurrentIndex(2)
            self.heading.setText('Главная · AI SMM Manager')
            self.update_dashboard()
            self.highlight_navigation(key)
            return
        labels = {'overview':'Обзор проекта', 'strategy':'Стратегии',
                  'plans':'Планы и схемы', 'calendar':'Контент-планы',
                  'tasks':'Задачи', 'analytics':'Аналитика'}
        self.section_stack.setCurrentIndex(1)
        self.section_title.setText(labels.get(key, 'Проект'))
        self.section_hint.setText('Документы сохраняются в проекте. AI-чат открывается кнопкой справа. '
                                  'Схемы Mermaid пока хранятся как редактируемый текст.')
        self.load_manager_documents()
        self.heading.setText(labels.get(key, 'Рабочее пространство'))
        self.highlight_navigation(key)

    def preview_publication_media(self, item=None):
        item = item or self.ready_media.currentItem()
        if item is None:return
        from project_file_boundary import require_managed_file
        try:
            path=require_managed_file(self.core.projects.activate(self.current_project),
                                      item.data(Qt.UserRole), ('ready','processed','video'))
            if not path.is_file():
                return
        except (ValueError, OSError):
            QMessageBox.warning(self, 'Просмотр', 'Файл не принадлежит текущему проекту.')
            return
        dialog=QDialog(self)
        dialog.setWindowTitle('Просмотр готового материала — '+path.name)
        dialog.resize(850,740)
        layout=QVBoxLayout(dialog)
        label=QLabel();label.setAlignment(Qt.AlignCenter)
        label.setMinimumSize(400,400)
        if path.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
            pix=QPixmap(str(path))
            if not pix.isNull():label.setPixmap(pix.scaled(QSize(800,600),Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else:
            label.setText('Видео: '+path.name+'\nДля просмотра нажмите «Открыть файл».')
        layout.addWidget(label,1)
        layout.addWidget(QLabel(path.name+'  ·  '+self.media_review_status(str(path))))
        controls=QHBoxLayout()
        def approve():
            if self.db.in_transaction:
                QMessageBox.warning(self, 'Проверка материала', 'Незавершённая транзакция SQLite.')
                return
            try:
                self.core.publication_queue.review_media(self.current_project,[str(path)],'Одобрен',timestamp=now())
            except (sqlite3.Error,OSError,ValueError,RuntimeError) as exc:
                QMessageBox.warning(self,'Проверка материала',str(exc))
                return
            dialog.accept();self.refresh_publications()
        for title,fn in [('Одобрить',approve),('Открыть файл',lambda:os.startfile(str(path)) if os.name=='nt' else subprocess.Popen(['xdg-open',str(path)])),('Закрыть',dialog.reject)]:
            b=QPushButton(title);b.clicked.connect(fn);controls.addWidget(b)
        layout.addLayout(controls)
        dialog.exec()

    def add_media_to_channel(self, channel):
        self.add_media_paths_to_channel([x.data(Qt.UserRole) for x in self.ready_media.selectedItems()],channel)

    def add_media_paths_to_channel(self,paths,channel):
        if not self.current_project:return False
        if not paths:
            QMessageBox.information(self,'Публикации','Выберите готовые материалы.')
            return False
        try:
            self.core.publication_queue.enqueue(self.current_project,
                tuple(dict.fromkeys(map(str,paths))),channel,timestamp=now(),
                dedupe=True,video_only=(channel=='Reels'))
        except (sqlite3.Error,OSError,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,'Публикации','Очередь не изменена: '+str(exc))
            return False
        self.refresh_publications()
        return True

    def publication_paths_for_job_ids(self,ids):
        if not self.current_project or not isinstance(ids,list):return []
        return list(self.core.publication_queue.job_paths(self.current_project,ids))

    def import_ready_publication_files(self,paths):
        """Qt collects source paths; the application core owns verified import."""
        if not self.current_project:return False
        try:
            result=self.core.publication_queue.import_ready_sources(self.current_project,paths,
                timestamp=now())
        except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
            QMessageBox.warning(self,'Импорт готовых файлов',str(exc))
            return False
        if result['done']:
            self.refresh_publications()
            self.statusBar().showMessage(
                f'Добавлено в готовые материалы: {len(result["done"])}. Перед публикацией проверьте и одобрите.',9000)
        if result['failed']:
            QMessageBox.warning(self,'Импорт частично завершён',str(result['failed'][:3]))
        return bool(result['done'])

    def show_publication_selection(self, item, previous=None):
        if not hasattr(self,'pub_preview'):return
        if not item:
            self.pub_preview.clear();self.pub_preview_info.clear();return
        from project_file_boundary import require_managed_file
        try:
            path=require_managed_file(self.core.projects.activate(self.current_project),
                                      item.data(Qt.UserRole), ('ready','processed','video'))
            if not path.is_file():
                raise ValueError('Файл отсутствует')
        except (ValueError, OSError):
            self.pub_preview.clear()
            self.pub_preview_info.setText('Файл вне проекта или недоступен')
            return
        self.pub_preview_info.setText(path.name+'\n'+self.media_review_status(str(path)))
        if path.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
            pix=QPixmap(str(path))
            if not pix.isNull():
                self.pub_preview.setPixmap(pix.scaled(QSize(420,340),Qt.KeepAspectRatio,Qt.SmoothTransformation));return
        self.pub_preview.setText('Видео — двойной щелчок для просмотра')

    def publication_media_menu(self, point):
        item=self.ready_media.itemAt(point)
        if item and not item.isSelected():
            self.ready_media.clearSelection();item.setSelected(True);self.ready_media.setCurrentItem(item)
        if not self.ready_media.selectedItems():return
        menu=QMenu(self)
        menu.addAction('Увеличить / просмотреть',self.preview_publication_media)
        menu.addSeparator()
        menu.addAction('✓ Одобрить',self.approve_ready_media)
        menu.addAction('На проверку',self.unapprove_ready_media)
        menu.addSeparator()
        for channel in ('Истории','Reels','Лента'):
            menu.addAction('Добавить в '+channel,lambda checked=False,c=channel:self.add_media_to_channel(c))
        menu.addSeparator()
        menu.addAction('Открыть расположение',lambda:self.open_media_location())
        menu.addAction('Отклонить (переместить в rejected)',self.remove_ready_media)
        menu.exec(self.ready_media.mapToGlobal(point))

    def open_media_location(self):
        item=self.ready_media.currentItem()
        if not item:return
        from project_file_boundary import require_managed_file
        try:
            path=require_managed_file(self.core.projects.activate(self.current_project),
                                      item.data(Qt.UserRole), ('ready','processed','video'))
            if not path.is_file():
                return
        except (ValueError, OSError):
            QMessageBox.warning(self,'Файл','Файл не принадлежит текущему проекту.')
            return
        if os.name=='nt':subprocess.Popen(['explorer','/select,',str(path)])
        else:subprocess.Popen(['xdg-open',str(path.parent)])

    def publication_gallery_filter(self):
        wanted=self.pub_filter.currentText()
        for i in range(self.ready_media.count()):
            item=self.ready_media.item(i)
            status=self.media_review_status(item.data(Qt.UserRole))
            item.setHidden(wanted!='Все' and status!=wanted)

    def publication_channel_rule(self, channel):
        self.pub_block.setCurrentText(channel)
        self.pub_tabs.setCurrentIndex(1)

    def build_publishing_page(self):
        self.publishing_page=QWidget()
        outer=QVBoxLayout(self.publishing_page)
        heading=QHBoxLayout()
        title=QLabel('Публикации · медиатека и расписание')
        title.setStyleSheet('font-size:21px;font-weight:700')
        heading.addWidget(title,1)
        refresh=QPushButton('↻ Обновить')
        refresh.clicked.connect(self.refresh_publications)
        heading.addWidget(refresh)
        open_folder=QPushButton('Открыть папку готовых')
        open_folder.clicked.connect(self.open_ready_folder)
        heading.addWidget(open_folder)
        outer.addLayout(heading)
        outer.addWidget(QLabel('Локальный планировщик: отправка в Instagram ещё не подключена. Файлы не публикуются автоматически.'))
        self.pub_tabs=QTabWidget()
        outer.addWidget(self.pub_tabs,1)
        gallery_page=QWidget();gallery_lay=QVBoxLayout(gallery_page)
        filterrow=QHBoxLayout()
        filterrow.addWidget(QLabel('Готовые материалы:'))
        self.pub_filter=QComboBox();self.pub_filter.addItems(['Все','Ожидает проверки','Одобрен','Отклонён'])
        self.pub_filter.currentTextChanged.connect(self.publication_gallery_filter)
        filterrow.addWidget(self.pub_filter)
        filterrow.addStretch(1)
        gallery_lay.addLayout(filterrow)
        self.ready_media=PublicationGallery(self)
        self.ready_media.setViewMode(QListWidget.IconMode)
        self.ready_media.setIconSize(QSize(175,175))
        self.ready_media.setGridSize(QSize(205,230))
        self.ready_media.setResizeMode(QListWidget.Adjust)
        self.ready_media.setMovement(QListWidget.Static)
        self.ready_media.setWordWrap(True)
        self.ready_media.setSpacing(9)
        self.ready_media.setDragEnabled(True)
        self.ready_media.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ready_media.customContextMenuRequested.connect(self.publication_media_menu)
        self.ready_media.currentItemChanged.connect(self.show_publication_selection)
        self.ready_media.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.ready_media.itemDoubleClicked.connect(self.preview_publication_media)
        top=QSplitter(Qt.Horizontal)
        top.addWidget(self.ready_media)
        preview=QWidget();preview_lay=QVBoxLayout(preview)
        preview_lay.addWidget(QLabel('ПРЕДПРОСМОТР'))
        self.pub_preview=QLabel('Выберите фотографию для просмотра')
        self.pub_preview.setAlignment(Qt.AlignCenter)
        self.pub_preview.setMinimumSize(190,190)
        preview_lay.addWidget(self.pub_preview,1)
        self.pub_preview_info=QLabel('')
        self.pub_preview_info.setWordWrap(True)
        preview_lay.addWidget(self.pub_preview_info)
        preview_button=QPushButton('Увеличить / открыть')
        preview_button.clicked.connect(self.preview_publication_media)
        preview_lay.addWidget(preview_button)
        top.addWidget(preview);top.setStretchFactor(0,3);top.setStretchFactor(1,2)
        gallery_lay.addWidget(top,4)
        reviewrow=QHBoxLayout()
        for name,callback in [('🔍 Просмотр',self.preview_publication_media),('✓ Одобрить',self.approve_ready_media),('На проверку',self.unapprove_ready_media),('Отклонить',self.remove_ready_media)]:
            b=QPushButton(name);b.clicked.connect(callback);reviewrow.addWidget(b)
        gallery_lay.addLayout(reviewrow)
        gallery_lay.addWidget(QLabel('Перетащи файлы из Проводника в галерею; одобренные — в Истории, Reels или Ленту. Из одного блока в другой — копирование задания.'))
        blocks=QHBoxLayout()
        self.pub_channel_lists={}
        for channel in ('Истории','Reels','Лента'):
            pane=QFrame();pane.setFrameShape(QFrame.StyledPanel)
            pane.setMinimumWidth(175)
            vl=QVBoxLayout(pane)
            vl.addWidget(QLabel('<b>'+channel+'</b>'))
            lw=PublicationChannelList(channel,self);lw.setMinimumHeight(150)
            lw.itemDoubleClicked.connect(lambda item:self.publication_job_from_item(item))
            self.pub_channel_lists[channel]=lw
            vl.addWidget(lw)
            add=QPushButton('+ Добавить выделенное')
            add.clicked.connect(lambda checked=False,c=channel:self.add_media_to_channel(c))
            vl.addWidget(add)
            schedule=QPushButton('⚙ Расписание блока')
            schedule.clicked.connect(lambda checked=False,c=channel:self.publication_channel_rule(c))
            vl.addWidget(schedule)
            blocks.addWidget(pane,1)
        gallery_lay.addLayout(blocks)
        self.pub_tabs.addTab(gallery_page,'Медиатека и блоки')
        schedule_page=QWidget();schedule_lay=QVBoxLayout(schedule_page)
        schedule_lay.addWidget(QLabel('ОБЩЕЕ РАСПИСАНИЕ БЛОКА'))
        rules=QHBoxLayout()
        self.pub_block=QComboBox();self.pub_block.addItems(['Истории','Reels','Лента'])
        self.pub_block.currentTextChanged.connect(self.load_publication_rule)
        self.pub_frequency=QComboBox();self.pub_frequency.addItems(['Вручную','Ежедневно','Пн–Пт','Еженедельно'])
        self.pub_time=QLineEdit('09:00');self.pub_time.setMaximumWidth(80)
        save_rule=QPushButton('Сохранить график');save_rule.clicked.connect(self.save_publication_rule)
        for widget in (self.pub_block,self.pub_frequency,self.pub_time,save_rule):rules.addWidget(widget)
        schedule_lay.addLayout(rules)
        schedule_lay.addWidget(QLabel('ОЧЕРЕДЬ · индивидуальная дата и время имеют приоритет'))
        self.pub_table=QTableWidget(0,5)
        self.pub_table.setHorizontalHeaderLabels(['Файл','Канал','Режим','Дата/время','Статус'])
        self.pub_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.pub_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pub_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        schedule_lay.addWidget(self.pub_table,1)
        actions=QHBoxLayout()
        for name,callback in [('Назначить точное время',self.set_individual_publication),('Использовать график блока',self.set_block_publication),('Убрать из очереди',self.remove_publication_job)]:
            b=QPushButton(name);b.clicked.connect(callback);actions.addWidget(b)
        schedule_lay.addLayout(actions)
        self.pub_tabs.addTab(schedule_page,'Расписание и очередь')
        self.section_stack.addWidget(self.publishing_page)

    def publication_job_from_item(self,item):
        jid=item.data(Qt.UserRole)
        for row in range(self.pub_table.rowCount()):
            cell=self.pub_table.item(row,0)
            if cell and cell.data(Qt.UserRole)==jid:
                self.pub_tabs.setCurrentIndex(1)
                self.pub_table.selectRow(row)
                return

    def refresh_publications(self):
        if not hasattr(self,'ready_media'):return
        self.ready_media.clear();self.pub_table.setRowCount(0)
        for lst in self.pub_channel_lists.values():lst.clear()
        if not self.current_project:return
        base=self.core.projects.activate(self.current_project)
        allowed={'.png','.jpg','.jpeg','.webp','.mp4','.mov'}
        seen=set()
        from project_file_boundary import require_managed_folder, require_managed_file
        for folder in ('ready','processed','video'):
            try:
                directory=require_managed_folder(base, folder)
            except ValueError:
                continue  # Never preview files through a linked media folder.
            for file in sorted(directory.glob('*')):
                try:
                    require_managed_file(base, file, (folder,))
                except (ValueError, OSError):
                    continue
                if not file.is_file() or file.suffix.lower() not in allowed or str(file) in seen:continue
                seen.add(str(file))
                review=self.media_review_status(str(file))
                item=QListWidgetItem(file.name+'\n'+review)
                item.setData(Qt.UserRole,str(file))
                if file.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
                    pix=QPixmap(str(file))
                    if not pix.isNull():item.setIcon(QIcon(pix.scaled(QSize(132,132),Qt.KeepAspectRatio,Qt.SmoothTransformation)))
                else:item.setIcon(self.style().standardIcon(self.style().SP_MediaPlay))
                self.ready_media.addItem(item)
        rows=self.core.publication_queue.jobs(self.current_project)
        for jid,path,channel,mode,at,status in rows:
            r=self.pub_table.rowCount();self.pub_table.insertRow(r)
            for c,v in enumerate((Path(path).name,channel,mode,at,status)):
                cell=QTableWidgetItem(str(v or ''));cell.setData(Qt.UserRole,jid);self.pub_table.setItem(r,c,cell)
            if channel in self.pub_channel_lists:
                entry=QListWidgetItem(Path(path).name+(' · '+at if at else ''))
                entry.setData(Qt.UserRole,jid)
                self.pub_channel_lists[channel].addItem(entry)
        self.publication_gallery_filter()
        self.load_publication_rule()

    def media_review_status(self,path):
        return self.core.publication_queue.media_status(self.current_project,path)

    def set_media_review(self,status):
        if not self.current_project:return False
        try:
            paths=[item.data(Qt.UserRole) for item in self.ready_media.selectedItems()]
            self.core.publication_queue.review_media(self.current_project,paths,status,timestamp=now())
        except (sqlite3.Error,OSError,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,'Проверка материала',str(exc))
            return False
        self.refresh_publications()
        return True

    def approve_ready_media(self):self.set_media_review('Одобрен')
    def unapprove_ready_media(self):self.set_media_review('Ожидает проверки')

    def remove_ready_media(self):
        if not self.current_project:return False
        selected=self.ready_media.selectedItems()
        if not selected:return False
        answer=QMessageBox.question(self,'Убрать из медиатеки',
            f'Убрать {len(selected)} файл(ов) из готовых? Файлы будут перемещены в папку rejected, '
            'а исходники останутся без изменений. Связанные задания публикаций будут удалены.',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return False
        try:
            result=self.core.publication_queue.reject_media(self.current_project,
                [item.data(Qt.UserRole) for item in selected],timestamp=now())
        except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
            QMessageBox.warning(self,'Удаление не выполнено',str(exc))
            return False
        self.refresh_publications()
        if result['failed']:
            QMessageBox.warning(self,'Восстановление медиатеки',
                'Часть операций остаётся в очереди для восстановления: '+str(result['failed'][:3]))
            return False
        return True

    def open_ready_folder(self):
        if not self.current_project:return
        folder=self.core.projects.activate(self.current_project)/'ready'
        if os.name=='nt':os.startfile(str(folder))
        else:subprocess.Popen(['xdg-open',str(folder)])

    def register_ready_result(self,path,*,project_id=None):
        """A generated artifact is staged and verified in its origin project."""
        pid=project_id or self.current_project
        if not pid:return False
        result=self.core.publication_queue.import_ready_sources(pid,[path],timestamp=now())
        if result['failed']:
            raise RuntimeError('Publication media is awaiting verified recovery: '+str(result['failed'][:3]))
        if pid==self.current_project and hasattr(self,'ready_media') and self.active_section=='publishing':
            self.refresh_publications()
        return bool(result['done'])

    def checkpoint_batch(self,status='running'):
        if not self.batch_run_id or not self.current_project:return
        if status=='completed':
            self.core.batches.finish(self.batch_owner_project or self.current_project,self.batch_run_id,timestamp=now())
        elif status=='cancelled':
            self.core.batches.cancel(self.batch_owner_project or self.current_project,self.batch_run_id,timestamp=now())
        elif status!='running':
            raise ValueError('Unsupported batch status')

    def offer_resume_batch(self):
        if not self.current_project or self.batch_active or self.modern_worker is not None or self.cloud_worker is not None:return
        try:
            pending_records=self.core.batches.pending_all(self.current_project)
        except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
            QMessageBox.warning(self,'Незавершённая очередь','Не удалось проверить состояние очереди: '+str(exc))
            return
        if not pending_records:return
        if len(pending_records)>1:
            labels=[f'{r.batch_id[:8]} · {len(r.paths)}/{r.total} осталось · {r.prompt[:55]}'
                    for r in pending_records]
            selected,ok=QInputDialog.getItem(self,'Незавершённые задания',
                'Выберите сохранённую очередь:',labels,0,False)
            if not ok:return
            record=pending_records[labels.index(selected)]
        else:
            record=pending_records[0]
        if record.blocked:
            QMessageBox.warning(self,'Незавершённая очередь',record.blocked)
            return
        if not record.paths:
            try:self.core.batches.finish(self.current_project,record.batch_id,timestamp=now())
            except (ValueError,sqlite3.Error,RuntimeError) as exc:
                QMessageBox.warning(self,'Незавершённая очередь','Завершение требует проверки: '+str(exc))
            return
        if record.missing:
            QMessageBox.warning(self,'Незавершённая очередь',
                f'Для продолжения отсутствуют {len(record.missing)} из {len(record.paths)} входных файлов. '
                'Оригинальная очередь сохранена без изменений; восстановите исходники.')
            return
        if QMessageBox.question(self,'Незавершённая очередь',
            f'Найдена незавершённая пакетная обработка ({len(record.paths)} файлов осталось). Продолжить?',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.core.batches.recover(self.current_project,record.batch_id)
        self.batch_owner_project=self.current_project
        self.batch_run_id=record.batch_id
        self.batch_claim=None
        self.batch_inflight=None
        self.batch_prompt=record.prompt
        self.batch_queue=list(record.paths)
        self.batch_total=record.total
        self.batch_active=True
        self.engine_mode=record.engine_mode
        QTimer.singleShot(0,self.batch_next)

    def add_publication_jobs(self):
        if not self.current_project:return
        paths=[item.data(Qt.UserRole) for item in self.ready_media.selectedItems()]
        if not paths:
            QMessageBox.information(self,'Публикации','Выберите один или несколько готовых файлов.')
            return
        try:
            self.core.publication_queue.enqueue(self.current_project,paths,
                self.pub_channel.currentText(),timestamp=now())
        except (sqlite3.Error,OSError,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,'Публикации','Очередь не изменена: '+str(exc))
            return
        self.refresh_publications()

    def selected_publication_id(self):
        r=self.pub_table.currentRow()
        return self.pub_table.item(r,0).data(Qt.UserRole) if r>=0 and self.pub_table.item(r,0) else None

    def set_individual_publication(self):
        jid=self.selected_publication_id()
        if not jid or not self.current_project:return
        value,ok=QInputDialog.getText(self,'Индивидуальная публикация',
            'Дата и время (ГГГГ-ММ-ДД ЧЧ:ММ):',text=datetime.now().strftime('%Y-%m-%d %H:%M'))
        if not ok:return
        try:
            if not self.core.publication_queue.schedule(self.current_project,jid,'Индивидуально',value.strip()):
                raise ValueError('Задание публикации не найдено')
        except (sqlite3.Error,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,'Публикации','Расписание не изменено: '+str(exc))
            return
        self.refresh_publications()

    def set_block_publication(self):
        jid=self.selected_publication_id()
        if not jid or not self.current_project:return
        try:
            if not self.core.publication_queue.schedule(self.current_project,jid,'По графику блока'):
                raise ValueError('Задание публикации не найдено')
        except (sqlite3.Error,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,'Публикации','Расписание не изменено: '+str(exc))
            return
        self.refresh_publications()

    def remove_publication_job(self):
        jid=self.selected_publication_id()
        if not jid or not self.current_project:return
        if QMessageBox.question(self,'Удалить задание',
            'Убрать публикацию из очереди? Медиафайл останется на диске.',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        try:
            self.core.publication_queue.delete(self.current_project,jid)
        except (sqlite3.Error,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,'Публикации','Очередь не изменена: '+str(exc))
            return
        self.refresh_publications()

    def load_publication_rule(self,*_):
        if not self.current_project:return
        row=self.core.publication_queue.block_rule(self.current_project,self.pub_block.currentText())
        self.pub_frequency.setCurrentText(row[0] if row else 'Вручную')
        self.pub_time.setText(row[1] if row else '09:00')

    def save_publication_rule(self):
        if not self.current_project:return
        try:
            self.core.publication_queue.save_block_rule(self.current_project,
                self.pub_block.currentText(),self.pub_frequency.currentText(),self.pub_time.text().strip())
        except (sqlite3.Error,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,'Публикации','Правило не сохранено: '+str(exc))
            return
        self.statusBar().showMessage('График блока сохранён. Публикация в Instagram пока не подключена.',6000)

    def toggle_technical_log(self):
        visible = not self.technical_log.isVisible()
        self.technical_log.setVisible(visible)
        self.log_toggle.setText('▾ Скрыть технический журнал' if visible else '▸ Технический журнал')

    def toggle_ai_panel(self):
        self.chat_area.setVisible(not self.chat_area.isVisible())
        self.ai_toggle.setText('✦  AI Manager' if not self.chat_area.isVisible() else '✦  Скрыть AI')
        if self.chat_area.isVisible():
            self.main_splitter.setSizes([215, 760, 565])
            self.message.setFocus()

    def highlight_navigation(self, key):
        for k, button in self.nav_buttons.items():
            button.setProperty('active', k == key)
            button.style().unpolish(button)
            button.style().polish(button)

    def update_dashboard(self):
        if not hasattr(self, 'dashboard_metrics'):
            return
        while self.dashboard_metrics.count():
            item = self.dashboard_metrics.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        project_count,current_docs,current_name = self.core.data.counts(self.current_project)
        self.dashboard_subtitle.setText('Активный проект: ' + current_name if current_name else 'Выбери проект слева.')
        for label,value in [('Всего проектов',project_count),('Документы проекта',current_docs)]:
            card = QLabel(f'{label}\n\n{value}')
            card.setObjectName('metricCard')
            card.setMinimumHeight(94)
            self.dashboard_metrics.addWidget(card)

    def apply_workspace_theme(self):
        self.setStyleSheet('''
        QMainWindow, QWidget { background: #0c0d0f; color: #e8eaed;
            font-family: "Segoe UI"; font-size: 12px; }
        QLabel#pageTitle { font-size: 25px; font-weight: 700; }
        QLabel#muted { color: #969ba4; }
        QLabel#metricCard { background: #1a1c20; border: 1px solid #26292d;
            border-radius: 12px; padding: 14px; font-size: 15px; }
        QPushButton { background: #1c1e22; border: 1px solid #2b2e33;
            border-radius: 9px; padding: 9px 12px; text-align: left; }
        QPushButton:hover { background: #292d31; border-color: #337c7b; }
        QPushButton[active="true"] { background: #213b3a; border-color: #327b79; }
        QPushButton#accentButton { background: #176b69; color: white;
            font-weight: 600; text-align: center; }
        QPushButton#dashboardCard { background: #1a1c20; border-radius: 13px;
            padding: 17px; font-size: 15px; font-weight: 600; }
        QPushButton#dashboardCard:hover { background: #203432; }
        QLineEdit, QTextEdit, QPlainTextEdit, QListWidget, QComboBox {
            background: #17191c; color: #e8eaed; border: 1px solid #303339;
            border-radius: 8px; padding: 7px; selection-background-color: #176b69; }
        QListWidget::item { padding: 7px; border-radius: 6px; }
        QListWidget::item:selected { background: #24514f; }
        QSplitter::handle { background: #24272b; }
        QScrollBar { background: #151719; }
        QStatusBar { color: #969ba4; }
        ''')

    def load_manager_documents(self):
        self.document_list.blockSignals(True)
        self.document_list.clear()
        self.document_ids = []
        if self.current_project:
            kind = None if self.active_section == 'overview' else self.active_section
            for doc_id, title, doc_kind, _ in self.core.data.documents(self.current_project,kind=kind):
                self.document_ids.append(doc_id)
                item = QListWidgetItem(title)
                item.setData(Qt.UserRole, doc_id)
                self.document_list.addItem(item)
        self.document_list.blockSignals(False)
        self.active_document_id = None
        self._loading_document = True
        try:
            self.document_title.clear()
            self.document_editor.clear()
            self.refresh_document_diagram()
        finally:
            self._loading_document = False
        if self.document_ids:
            self.document_list.setCurrentRow(0)
        self.document_title.setEnabled(bool(self.document_ids))
        self.document_views.setEnabled(bool(self.document_ids))

    def open_document(self, row):
        if not 0 <= row < len(self.document_ids): return
        if not self._loading_document:
            if self.save_document_if_changed() is False:
                return
        self.active_document_id = self.document_ids[row]
        record = self.core.data.document(self.current_project,self.active_document_id)
        doc = (record[1],record[2]) if record else None
        if doc:
            self._loading_document = True
            try:
                self.document_title.setText(doc[0])
                self.document_editor.setPlainText(doc[1])
                self.refresh_document_diagram()
            finally:
                self._loading_document = False

    def create_manager_document(self):
        if not self.current_project:
            QMessageBox.information(self, 'Проект', 'Сначала создайте или выберите проект.')
            return
        kinds = ['strategy', 'plans', 'calendar', 'tasks', 'analytics']
        kind = self.active_section if self.active_section in kinds else 'strategy'
        title, ok = QInputDialog.getText(self, 'Новый документ', 'Название:')
        if not ok or not title.strip(): return
        if self.db.in_transaction:
            QMessageBox.warning(self, 'Новый документ',
                                'Создание заблокировано: незавершённая транзакция SQLite.')
            return
        doc_id = uuid4().hex
        try:
            saved = self.core.create_document(self._export_document_files,
                project_id=self.current_project,document_id=doc_id,kind=kind,
                title=title.strip(),content='',created_at=now())
        except Exception as exc:
            QMessageBox.warning(self, 'Новый документ', 'Не удалось зарегистрировать документ: ' + str(exc))
            return
        if not saved.exported:
            self.statusBar().showMessage('Документ сохранён; экспорт ожидает восстановления',9000)
        self.load_manager_documents()
        if doc_id in self.document_ids:
            self.document_list.setCurrentRow(self.document_ids.index(doc_id))

    def relocate_document(self, doc_id, destination):
        if not self.current_project or destination not in ('strategy','plans','calendar','tasks','analytics'):
            return False
        exists = self.core.data.document(self.current_project, doc_id)
        if not exists:
            return False
        if self.save_document_if_changed() is False:
            return False
        # Another save may have updated the source text; never relocate stale content.
        exists = self.core.data.document(self.current_project, doc_id)
        if not exists or not self.save_manager_document(doc_id_override=doc_id,
                kind_override=destination, title_override=exists[1], content_override=exists[2]):
            return False
        self.show_section(destination)
        if doc_id in self.document_ids:
            self.document_list.setCurrentRow(self.document_ids.index(doc_id))
        self.statusBar().showMessage('Документ перемещён в выбранный раздел', 7000)
        return True

    def move_manager_document(self):
        if not self.current_project or not self.active_document_id:
            QMessageBox.information(self, 'Перемещение', 'Выберите документ.')
            return
        sections = [('strategy','Стратегии'), ('plans','Планы и схемы'),
                    ('calendar','Контент-планы'), ('tasks','Задачи'), ('analytics','Аналитика')]
        labels = [label for _, label in sections]
        choice, ok = QInputDialog.getItem(self, 'Переместить документ',
                                         'Выберите раздел:', labels, 0, False)
        if ok:
            self.relocate_document(self.active_document_id, sections[labels.index(choice)][0])

    def rename_manager_document(self):
        if not self.current_project or not self.active_document_id:
            QMessageBox.information(self, 'Переименование', 'Выберите документ.')
            return
        title, ok = QInputDialog.getText(self, 'Переименовать документ', 'Название:',
                                         text=self.document_title.text())
        if not ok or not title.strip():
            return
        self.document_title.setText(title.strip())
        if self.save_manager_document() is False:
            return
        doc_id = self.active_document_id
        self.load_manager_documents()
        if doc_id in self.document_ids:
            self.document_list.setCurrentRow(self.document_ids.index(doc_id))

    def document_context_menu(self, point):
        item = self.document_list.itemAt(point)
        if item is None:
            return
        self.document_list.setCurrentItem(item)
        menu = QMenu(self)
        rename = menu.addAction('Переименовать')
        move = menu.addAction('Переместить в раздел…')
        menu.addSeparator()
        delete = menu.addAction('Удалить документ…')
        selected = menu.exec(self.document_list.mapToGlobal(point))
        if selected == rename:
            self.rename_manager_document()
        elif selected == move:
            self.move_manager_document()
        elif selected == delete:
            self.delete_manager_document()

    def delete_manager_document(self):
        if not self.current_project or not self.active_document_id:
            QMessageBox.information(self, 'Удаление', 'Сначала выберите документ.')
            return
        title = self.document_title.text().strip()
        answer = QMessageBox.question(
            self, 'Удалить документ',
            f'Удалить документ «{title}» из текущего проекта?\nЭто действие нельзя отменить.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        # All GUI and AI deletion entrypoints require the same verified backup.
        # A backup failure or rejected project scope must not clear the editor.
        project_id = self.current_project
        document_id = self.active_document_id
        try:
            receipt = self.core.controls.backup_and_delete(
                project_id, 'documents', selected_ids=(document_id,))
        except Exception as exc:
            QMessageBox.warning(self, 'Документ не удалён', str(exc))
            return
        self.active_document_id = None
        self.load_manager_documents()
        self.statusBar().showMessage(
            'Документ удалён; резервная копия: ' + str(receipt.backup))

    def refresh_document_preview(self, *_):
        """Render Markdown safely without altering the source stored in the project DB."""
        if not hasattr(self, 'document_preview'):
            return
        source = self.document_editor.toPlainText()
        if self.document_preview.toMarkdown() == source:
            return
        self.document_preview.setHtml(document_export_html(self.document_title.text(),source))

    def refresh_document_diagram(self, *_):
        """Build views from document facts only; no invented metrics or generic nodes."""
        if not hasattr(self, 'diagram_view'):
            return
        source = self.document_editor.toPlainText().strip()
        scene = QGraphicsScene(self.diagram_view)
        if not source:
            scene.addText('Документ пуст. Визуализация появится после заполнения.')
            self.diagram_view.setScene(scene)
            self.document_table.setRowCount(0)
            self.document_table.setColumnCount(0)
            return
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        sections = []
        current_title = None
        current_body = []
        heading_re = re.compile(r'^\s*(?:#{1,5}\s+|(?:\d{1,2}\.)+\s+|\d{1,2}[)]\s+|\*\*[^*]{3,}\*\*\s*$)')
        for line in lines:
            if heading_re.match(line):
                if current_title is not None:
                    sections.append((current_title, ' '.join(current_body)))
                current_title = re.sub(r'^\s*(?:#{1,5}\s+|(?:\d{1,2}\.)+\s+|\d{1,2}[)]\s+)', '', line).strip(' *')
                current_body = []
            elif current_title is not None:
                current_body.append(line)
        if current_title is not None:
            sections.append((current_title, ' '.join(current_body)))
        if not sections:
            # Without identified sections, show actual paragraphs, not fictitious stages.
            sections = [(line[:100], '') for line in lines[:12]]
        sections = sections[:24]
        width = 610
        y = 0
        for idx, (heading, body) in enumerate(sections):
            body = body[:260]
            content = heading + ('\n' + body if body else '')
            height = 78 + min(85, (len(body) // 80) * 23)
            scene.addRect(0, y, width, height, QPen(QColor('#327b77'), 1.6), QBrush(QColor('#202c30')))
            item = scene.addText(content, QFont('Segoe UI', 10))
            item.setDefaultTextColor(QColor('#e8eaed'))
            item.setTextWidth(width - 32)
            item.setPos(14, y + 8)
            if idx:
                # A vertical reading order, not an asserted causal dependency.
                scene.addLine(width / 2, y - 22, width / 2, y, QPen(QColor('#66a6a1'), 2))
            y += height + 22
        scene.setSceneRect(-15, -15, width + 30, y + 20)
        self.diagram_view.setScene(scene)
        self._refresh_document_table(lines)

    def _refresh_document_table(self, lines):
        """Display only rows explicitly present in a markdown/pipe table."""
        parsed = []
        for line in lines:
            if '|' not in line:
                continue
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if len(cells) < 2 or all(re.fullmatch(r'[-: ]+', c or '-') for c in cells):
                continue
            parsed.append(cells)
        table = self.document_table
        table.clear()
        if len(parsed) < 2:
            table.setRowCount(1)
            table.setColumnCount(1)
            table.setHorizontalHeaderLabels(['Данные документа'])
            table.setItem(0, 0, QTableWidgetItem('В документе нет структурированной таблицы. Данные не выдумываются.'))
            return
        headers = parsed[0]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(parsed) - 1)
        for r, row in enumerate(parsed[1:]):
            for c in range(len(headers)):
                table.setItem(r, c, QTableWidgetItem(row[c] if c < len(row) else ''))

    def save_ai_document(self, instruction, content, *, kind=None):
        # This GUI connection may carry an unfinished write from another task.
        # Neither export nor rollback may cross that transaction boundary.
        if self.db.in_transaction and not getattr(self.db, '_result_depth', 0):
            raise RuntimeError('Сохранение AI-документа заблокировано: чужая транзакция SQLite')
        if kind is not None:
            if kind not in ('strategy','calendar','plans','tasks','analytics'):
                raise ValueError('Неизвестный раздел документа')
            project=(self.core.data.project_name(self.current_project),)
            if not project:raise ValueError('Проект не найден')
            prefix={'strategy':'Стратегия','calendar':'Контент-план','plans':'План и схема',
                    'tasks':'Задачи','analytics':'Аналитика'}[kind]
            title=(prefix+' — '+project[0])[:100]
        else:
            # Route by the requested artifact, regardless of the currently open section.
            lower = instruction.casefold()
            body_start = content[:1800].casefold()
            if re.search(r'контент[ -]?план|план\s+публикац|календар|постинг|план\s+постов|публикаци[йия]', lower) or (not re.search(r'стратег|маркетингов', lower) and re.search(r'контент[ -]?план|календарь публикаций', body_start)):
                kind, prefix = 'calendar', 'Контент-план'
            elif re.search(r'стратег|продвижени|маркетинговый план', lower):
                kind, prefix = 'strategy', 'Маркетинговая стратегия'
            elif re.search(r'блок[ -]?схем|схем|дорожн|план работ', lower):
                kind, prefix = 'plans', 'План и схема'
            elif re.search(r'задач|чек[ -]?лист', lower):
                kind, prefix = 'tasks', 'План задач'
            elif re.search(r'аналит|отч[её]т|метрик', lower):
                kind, prefix = 'analytics', 'Аналитический отчёт'
            else:
                kind = self.active_section if self.active_section in ('strategy','plans','calendar','tasks','analytics') else 'plans'
                prefix = {'strategy':'Стратегия','plans':'Рабочий план','calendar':'Контент-план',
                          'tasks':'Задачи','analytics':'Аналитика'}[kind]
            # Use the active project's name when the user did not specify a subject.
            project_row = (self.core.data.project_name(self.current_project),)
            project_name = project_row[0].strip() if project_row else 'проекта'
            subject = None
            match = re.search(r'(?:для|по|о)\s+([\wа-яё][\wа-яё\s-]{1,48}?)(?:[.,!?]|\s+(?:на\s+\d|с\s+уч[её]том|и\s+добав)|$)', lower, re.I)
            if match:
                candidate = match.group(1).strip(' -')
                if candidate and candidate not in ('этого', 'него', 'этой стратегии'):
                    subject = candidate
            if not subject:
                subject = project_name if project_name.casefold() not in ('тест', 'test') else 'проекта'
            title = f'{prefix} — {subject}'[:100]
        doc_id = uuid4().hex
        if getattr(self.db, '_result_depth', 0):
            # The completion coordinator owns the enclosing transaction.
            # Both generated and manually authored documents use the same
            # application-level export reservations and project identity.
            self.core.stage_generated_document(project_id=self.current_project,
                document_id=doc_id,kind=kind,title=title,content=content,created_at=now())
            self._pending_result_export = (self.current_project, doc_id)
            return
        # SQLite and the export intention are now committed together. Failed
        # rendering leaves a recoverable pending revision, never a phantom
        # successful file or an unregistered provisional document.
        exporter=getattr(self, '_export_document_files', None)
        if exporter is None:
            exporter=globals().get('export_document_files')
        if not callable(exporter):
            raise RuntimeError('Document exporter is unavailable')
        accepted=self.core.create_document(exporter,
            project_id=self.current_project,document_id=doc_id,kind=kind,
            title=title,content=content,created_at=now())
        if not accepted.exported:
            self.statusBar().showMessage('Документ принят SQLite; PDF/TXT ожидают восстановления',9000)
            raise RuntimeError('Экспорт документа не подтверждён; данные сохранены для восстановления')
        self.show_section(kind)
        if doc_id in self.document_ids:
            self.document_list.setCurrentRow(self.document_ids.index(doc_id))
            self.document_views.setCurrentWidget(self.document_preview)

    def _finalize_ai_document_effects(self, project_id, artifact_id, request, kind, title, task_id):
        completed = self.core.complete_document(
            self._replay_ai_document_export, self.saved_document_message,
            project_id=project_id, artifact_id=artifact_id, request=request,
            kind=kind, title=title, task_id=task_id)
        if not completed:
            self.record_project_diagnostic('document_export_pending',
                {'artifact_id': artifact_id}, project_id)
            if self.current_project == project_id:
                self.ai_status.setText('Документ в SQLite; экспорт ожидает восстановления')
            return
        if self.current_project == project_id:
            if getattr(self, 'task_instruction', None) == request:
                self.record_task_lifecycle('completed', completion_verified=True,
                                           verified_documents=[artifact_id])
            self.append_chat('AI-менеджер', self.saved_document_message(kind,title))
            self.ai_status.setText('Документ сохранён и проверен')
            self.show_section(kind)

    def _recover_committed_document_exports(self):
        """Delegate durable recovery to the application service, never GUI state."""
        try:
            report = self.core.restore(self._replay_ai_document_export,
                             self.saved_document_message)
            for event in report.completed:
                if event.project_id == self.current_project:
                    self.ai_status.setText('Документ сохранён и проверен')
                    self.show_section(event.kind)
            if report.rejected:
                self.record_project_diagnostic('document_export_recovery_rejected',
                    {'rejected': list(report.rejected)[:25]}, self.current_project)
            edited=self.core.restore_document_edits(self._export_document_files)
            if edited['failed']:
                self.record_project_diagnostic('editor_export_recovery_pending',edited,self.current_project)
            media=self.core.restore_media()
            if media['failed']:
                self.record_project_diagnostic('media_ingest_recovery_pending',media,self.current_project)
            rejected=self.core.publication_queue.recover_rejections(limit=1000)
            if rejected['failed']:
                self.record_project_diagnostic('publication_rejections_pending',
                    {'failed':rejected['failed'][:25]},self.current_project)
            ready=self.core.publication_queue.recover_ready_ingest(limit=1000)
            if ready['failed']:
                self.record_project_diagnostic('publication_ready_ingest_pending',
                    {'failed':ready['failed'][:25]},self.current_project)
            if self.current_project:
                from media_ingest_core import prune_unreferenced_spool
                prune_unreferenced_spool(self.db,self.core.workspace,project_id=self.current_project)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            self.record_project_diagnostic('document_export_recovery_failed',
                                           {'error': str(exc)[:500]}, self.current_project)

    def _replay_ai_document_export(self, project_id, artifact_id, content):
        row = self.core.data.document(project_id, artifact_id)
        if not row or row[2] != content:
            raise RuntimeError('Экспортируемый документ изменён')
        self._export_document_files(project_id, artifact_id, row[0], row[1], content)

    def save_document_if_changed(self):
        if self._loading_document or not self.current_project or not self.active_document_id:
            return True
        previous = self.core.data.document_title_content(self.current_project, self.active_document_id)
        if previous and previous != (self.document_title.text().strip(), self.document_editor.toPlainText()):
            return self.save_manager_document()
        return True

    def save_manager_document(self, *, doc_id_override=None, title_override=None, content_override=None, kind_override=None):
        """Qt collects a snapshot; application core owns the edit and export intent."""
        project_id = self.current_project
        doc_id = doc_id_override or self.active_document_id
        if not project_id or not doc_id:
            QMessageBox.information(self, 'Документ', 'Сначала выберите или создайте документ.')
            return False
        if self.db.in_transaction:
            self.statusBar().showMessage('Редактирование заблокировано чужой транзакцией',10000)
            return False
        previous=self.core.data.document(project_id, doc_id)
        if previous is None:
            self.statusBar().showMessage('Документ не найден',10000)
            return False
        kind=kind_override if kind_override is not None else previous[0]
        title=title_override if title_override is not None else self.document_title.text().strip()
        content=content_override if content_override is not None else self.document_editor.toPlainText()
        if kind not in ('strategy','plans','calendar','tasks','analytics') or not title:
            self.statusBar().showMessage('Проверьте тип и название документа',10000)
            return False
        try:
            png=None
            if (doc_id==self.active_document_id and
                    self.document_views.currentIndex()==1):
                scene=self.diagram_view.scene()
                if scene is not None and scene.items():
                    png=render_diagram_png_bytes(scene)
            receipt=self.core.save_document(self._export_document_files,
                project_id=project_id,document_id=doc_id,kind=kind,title=title,
                content=content,updated_at=now(),diagram_png=png)
        except Exception as exc:
            self.statusBar().showMessage('Не удалось принять редакцию: '+str(exc),15000)
            return False
        if not receipt.exported:
            self.statusBar().showMessage('Редакция сохранена в SQLite; экспорт ожидает восстановления',15000)
            return False
        self.statusBar().showMessage('Редакция и проверенные PDF/TXT сохранены',7000)
        return True

    def export_active_document(self, silent=False):
        if not self.current_project or not self.active_document_id:
            if not silent: QMessageBox.information(self,'PDF','Выберите документ.')
            return False
        # Always use the same guarded PDF/TXT + SQLite transaction as Save.
        # A direct export from unsaved GUI text used to overwrite the last good
        # deliverables without updating the document database.
        if self.save_manager_document() is False:
            if not silent:
                QMessageBox.warning(self, 'Экспорт не выполнен',
                                    'Не удалось сохранить документ и подтвердить PDF/TXT.')
            return False
        doc = self.core.data.document_kind_title(self.current_project,self.active_document_id)
        if not doc:
            return False
        kind,title=doc
        stem=safe_file_label(title)+'_'+self.active_document_id[:8]
        base=self.core.workspace(self.current_project)
        txt=base/document_folder(kind)/(stem+'.txt')
        pdf=base/'pdf'/(stem+'.pdf')
        diagram=base/'diagrams'/(stem+'.png')
        if not silent:
            QMessageBox.information(self, 'Экспорт завершён',
                f'PDF: {pdf}\nТекст: {txt}'+(f'\nСхема: {diagram}' if diagram.is_file() else ''))
        return True

    def open_project_folder(self):
        if not self.current_project:
            QMessageBox.information(self,'Проект','Выберите проект.')
            return
        folder=self.core.projects.activate(self.current_project)
        try:
            if sys.platform=='win32': os.startfile(str(folder))
            else: subprocess.Popen(['xdg-open',str(folder)])
        except Exception as exc: QMessageBox.warning(self,'Папка проекта',str(exc))

    def configure_storage(self):
        current=str(storage_base())
        folder=QFileDialog.getExistingDirectory(self,'Основная папка для новых проектов',current)
        if not folder:return
        # This does not move existing files; it only changes where NEW projects live.
        try:
            Path(folder).mkdir(parents=True,exist_ok=True)
            STORAGE_SETTINGS.parent.mkdir(parents=True,exist_ok=True)
            from durable_json import atomic_write_json
            atomic_write_json(STORAGE_SETTINGS,{'projects_root':folder})
            QMessageBox.information(self,'Папка сохранена',
                'Новые проекты будут создаваться в:\n'+folder+'\n\nСуществующие проекты остаются на прежних местах.')
        except Exception as exc: QMessageBox.warning(self,'Ошибка настроек',str(exc))

    def refresh_projects(self, select_id=None):
        summaries = self.core.projects.list_projects()
        rows = [(item.id, item.name) for item in summaries]
        self.project_ids = [r[0] for r in rows]
        self.projects.blockSignals(True)
        self.projects.clear()
        self.projects.addItems([r[1] for r in rows])
        self.projects.blockSignals(False)
        if rows:
            index = self.project_ids.index(select_id) if select_id in self.project_ids else 0
            self.projects.setCurrentIndex(index)
            # Signals were blocked while rebuilding the combo box. Explicitly
            # activate the visible project so chat, documents and media agree.
            if self.current_project != self.project_ids[index]:
                self.select_project(index)
        else:
            # Clear the entire view; a deleted last project must not leave
            # its chat, media, or document state visible in the widgets.
            self.current_project = None
            self.select_project(-1)

    def add_project(self):
        from project_operation_guard import project_operation_busy
        if project_operation_busy(self):
            self.statusBar().showMessage('Дождитесь завершения активной операции перед созданием проекта.',6000)
            return
        dialog = ProjectDialog(self)
        if dialog.exec() != QDialog.Accepted: return
        if project_operation_busy(self):
            self.statusBar().showMessage('Создание проекта отменено: началась другая операция.',6000)
            return
        if self.db.in_transaction:
            QMessageBox.warning(self, 'Создание проекта',
                                'Другая операция SQLite не завершена; новый проект не создаётся.')
            return
        try:
            new_id = uuid4().hex
            pid = self.core.projects.create(storage_base(), dialog.name.text(),
                    dialog.category.currentText(), dialog.description.toPlainText(), now(),
                    project_id=new_id)
        except Exception as exc:
            # After registration the project remains durable and recoverable.
            QMessageBox.warning(self, 'Ошибка создания проекта', str(exc))
            return
        self.refresh_projects(pid)

    def select_project(self, row):
        if self._switching_project:
            return
        from project_operation_guard import project_operation_busy
        if project_operation_busy(self) or getattr(self,'_semantic_media_project',None):
            # The combobox already changed its visible index before invoking
            # this handler. Restore it without triggering a recursive switch.
            current = self.project_ids.index(self.current_project) if self.current_project in self.project_ids else -1
            if self.projects.currentIndex() != current:
                previous = self.projects.blockSignals(True)
                try:
                    self.projects.setCurrentIndex(current)
                finally:
                    self.projects.blockSignals(previous)
            self.statusBar().showMessage('Дождитесь завершения операции перед переключением проекта.',6000)
            return
        if self.save_document_if_changed() is False:
            # Leave the active project unchanged and restore the combobox.
            selected = self.project_ids.index(self.current_project) if self.current_project in self.project_ids else -1
            old_block = self.projects.blockSignals(True)
            try:
                self.projects.setCurrentIndex(selected)
            finally:
                self.projects.blockSignals(old_block)
            return
        # Read and verify the destination workspace *before* mutating widgets
        # or abandoning an unfinished selection in the current project.
        destination_view = None
        if 0 <= row < len(self.project_ids):
            try:
                target_project = self.project_ids[row]
                destination_view = self.core.prepare_project_switch(target_project)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                QMessageBox.warning(self, 'Восстановление проекта', str(exc))
                previous = self.projects.blockSignals(True)
                try:
                    self.projects.setCurrentIndex(self.project_ids.index(self.current_project)
                        if self.current_project in self.project_ids else -1)
                finally:
                    self.projects.blockSignals(previous)
                return
        self._switching_project = True
        # Any pending destructive choice is scoped to the old project, never
        # let its follow-up confirmation act on the newly selected project.
        self._pending_project_delete = None
        self.current_project = self.project_ids[row] if 0 <= row < len(self.project_ids) else None
        self.assets.clear()
        self.asset_paths = []
        self.source.show_file(None)
        self.mask_canvas.set_image(None)
        self.result.show_file(None)
        self.reference.show_file(None)
        self.reference_panel.hide()
        self.working_sources = []
        self.source_list.clear()
        self.source_count.setText('0 файлов')
        self.result_list.clear()
        self.result_versions = []
        self.text_editor.clear()
        self.chat.clear()
        self.load_manager_documents()
        if not self.current_project:
            self.heading.setText('Выберите проект')
            self._switching_project = False
            self.show_section('overview')
            return
        view = destination_view
        self.heading.setText(f'{view.name}  ·  {view.category or "Без категории"}')
        for name, path in view.assets:
            self.assets.addItem(name)
            self.asset_paths.append(path)
        if view.reference_path and Path(view.reference_path).exists():
            self.reference.show_file(view.reference_path)
            self.reference_panel.hide()
        for path in view.result_paths:
            if Path(path).exists():
                self.result_versions.append(path)
                self.result_list.addItem(self.workspace_item(path))
        if self.result_versions: self.result_list.setCurrentRow(0)
        for role, content, created_at in view.messages:
            self.append_chat('Вы' if role == 'user' else 'AI', content, created_at)
        # Restore the selected project's own workspace, never another project's content.
        self._switching_project = False
        self.show_section(self.project_section_state.get(self.current_project, 'overview'))
        self.update_dashboard()
        QTimer.singleShot(0,self.offer_resume_batch)
        # Opening a project does not implicitly place all its files in the working area.

    def drag_project_asset(self, supported_actions):
        row = self.assets.currentRow()
        if not (0 <= row < len(self.asset_paths)): return
        drag = QDrag(self.assets)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.asset_paths[row])])
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def select_asset(self, row):
        if 0 <= row < len(self.asset_paths):
            self.add_working_sources([self.asset_paths[row]])

    def drop_source(self, paths):
        self.import_asset_paths(paths, select_new=True, into_workspace=True)

    def add_working_sources(self, paths):
        for path in paths:
            if path not in self.working_sources:
                self.working_sources.append(path)
                self.source_list.addItem(self.workspace_item(path))
        self.source_count.setText(f'{len(self.working_sources)} файлов')
        if hasattr(self, 'video_media_list'):
            self.refresh_video_library()
        if self.working_sources:
            self.source_list.setCurrentRow(len(self.working_sources)-1)

    def switch_media_mode(self, index):
        self.media_mode_pages.setCurrentIndex(index)
        if index == 1:
            self.refresh_video_library()

    def refresh_video_library(self):
        self.video_media_list.clear()
        paths = list(dict.fromkeys(self.working_sources + self.result_versions))
        for path in paths:
            item = self.workspace_item(path)
            item.setData(Qt.UserRole, path)
            self.video_media_list.addItem(item)

    def preview_video_media(self, item):
        path = item.data(Qt.UserRole)
        if Path(path).suffix.lower() in IMAGE_EXTENSIONS:
            self.video_preview.show_file(path)
        else:
            self.video_preview.show_file(None)
            QMessageBox.information(self, 'Просмотр файла',
                'Встроенный видеоплеер и аудиоплеер пока не подключены. Файл можно добавить на монтажную шкалу.')

    def add_video_timeline_items(self):
        for item in self.video_media_list.selectedItems():
            path = item.data(Qt.UserRole)
            entry = QListWidgetItem(Path(path).name)
            entry.setData(Qt.UserRole, path)
            self.video_timeline.addItem(entry)

    def video_timeline_remove(self):
        for item in self.video_timeline.selectedItems():
            self.video_timeline.takeItem(self.video_timeline.row(item))

    def video_timeline_up(self):
        row = self.video_timeline.currentRow()
        if row > 0:
            item = self.video_timeline.takeItem(row)
            self.video_timeline.insertItem(row - 1, item)
            self.video_timeline.setCurrentRow(row - 1)

    def video_timeline_down(self):
        row = self.video_timeline.currentRow()
        if 0 <= row < self.video_timeline.count() - 1:
            item = self.video_timeline.takeItem(row)
            self.video_timeline.insertItem(row + 1, item)
            self.video_timeline.setCurrentRow(row + 1)

    def workspace_item(self, path):
        item = QListWidgetItem(Path(path).name)
        if Path(path).suffix.lower() in IMAGE_EXTENSIONS:
            pix = QPixmap(str(path))
            if not pix.isNull():
                item.setIcon(QIcon(pix.scaled(84, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        item.setToolTip(str(path) + "\nДвойной щелчок — большой просмотр")
        return item

    def workspace_file_menu(self, kind, point):
        menu = QMenu(self)
        menu.addAction('Открыть крупно', lambda: self.open_workspace_file(kind))
        if kind == 'source':
            menu.addAction('Убрать из рабочей области', self.remove_workspace_sources)
        else:
            menu.addAction('Удалить выбранные результаты…', self.remove_workspace_results)
        menu.exec((self.source_list if kind == 'source' else self.result_list).mapToGlobal(point))

    def open_workspace_file(self, kind):
        widget = self.source_list if kind == 'source' else self.result_list
        paths = self.working_sources if kind == 'source' else self.result_versions
        row = widget.currentRow()
        if row < 0 or row >= len(paths):
            return
        path = paths[row]
        dialog = QDialog(self)
        dialog.setWindowTitle(Path(path).name)
        dialog.resize(1100, 750)
        layout = QVBoxLayout(dialog)
        if Path(path).suffix.lower() in IMAGE_EXTENSIONS:
            viewer = Preview('Основной объект' if kind == 'source' else 'Результат')
            viewer.show_file(path)
            layout.addWidget(viewer)
        else:
            layout.addWidget(QLabel('Для этого формата встроенный просмотр пока не реализован.'))
            open_btn = QPushButton('Открыть во внешнем приложении')
            open_btn.clicked.connect(lambda: QUrl.fromLocalFile(path) and __import__('PySide6.QtGui', fromlist=['QDesktopServices']).QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
            layout.addWidget(open_btn)
        dialog.exec()

    def remove_workspace_sources(self):
        rows = sorted((self.source_list.row(i) for i in self.source_list.selectedItems()), reverse=True)
        for row in rows:
            if 0 <= row < len(self.working_sources):
                self.working_sources.pop(row)
                self.source_list.takeItem(row)
        self.source_count.setText(f'{len(self.working_sources)} файлов')
        if not self.working_sources:
            self.source.show_file(None)
            self.mask_canvas.set_image(None)

    def remove_workspace_results(self):
        if not self.current_project:
            return False
        if self.db.in_transaction:
            QMessageBox.warning(self, 'Удаление результатов', 'Незавершённая транзакция SQLite.')
            return False
        rows=sorted({self.result_list.row(i) for i in self.result_list.selectedItems()})
        if not rows:
            return False
        choice=QMessageBox.question(self,'Удаление результатов',
            f'Удалить {len(rows)} результат(ов) из проекта?\nИсходные материалы останутся нетронутыми.\n'
            'Удалённые файлы будут перемещены в корзину проекта.',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if choice!=QMessageBox.Yes:
            return False
        try:
            chosen = [self.result_versions[row] for row in rows]
            result = self.core.result_retirements.request(self.current_project,chosen)
            if result.failed or len(result.committed)!=len(chosen):
                raise RuntimeError('Удаление принято SQLite, но ожидает восстановления: '+str(result.failed))
        except (ValueError,OSError,RuntimeError,sqlite3.Error) as exc:
            QMessageBox.warning(self,'Удаление результатов не завершено',str(exc))
            return False
        for row in reversed(rows):
            self.result_versions.pop(row)
            self.result_list.takeItem(row)
        self.result.show_file(self.result_versions[0] if self.result_versions else None)
        return True

    def select_working_source(self, row):
        path = self.working_sources[row] if 0 <= row < len(self.working_sources) else None
        self.source.show_file(path)
        self.mask_canvas.set_image(path)

    def select_result_version(self, row):
        if 0 <= row < len(self.result_versions):
            self.result.show_file(self.result_versions[row])

    def hide_reference(self):
        self.reference_panel.hide()

    def show_reference(self):
        self.reference_panel.show()
        self.comparison.setSizes([350, 420, 420])


    def drop_library(self, paths):
        self.import_asset_paths(paths, select_new=False)

    def drop_reference(self, paths):
        if not paths: return
        self.import_reference_path(paths[0])

    def import_asset_paths(self, paths, select_new=False, into_workspace=False):
        """UI selects sources; durable media core owns their project publication."""
        if not self.current_project:
            self.append_chat('Система','Сначала выберите проект.')
            return
        if self.db.in_transaction:
            QMessageBox.warning(self,'Импорт заблокирован','Другая операция SQLite ещё не завершена.')
            return
        imported=[];errors=[];successes=0
        project_id=self.current_project
        allowed=IMAGE_EXTENSIONS | {'.mp4','.mov','.avi','.mkv','.webm','.mp3','.wav','.m4a',
            '.aac','.flac','.ogg','.wma','.txt','.md','.pdf','.docx','.rtf','.csv','.xlsx',
            '.xls','.json','.pptx','.srt','.vtt','.svg','.gif','.heic'}
        batch=self.core.import_media(paths,project_id=project_id,
                                   kind='asset',allowed_extensions=allowed)
        errors.extend(f'{Path(source).name}: {error}' for source,error in batch.failures)
        for receipt in batch.receipts:
            if not receipt.published:
                errors.append(f'{receipt.final_path.name}: данные приняты, файл ожидает восстановления')
            else:
                imported.append(str(receipt.final_path))
                if not receipt.duplicate:successes+=1
        if not imported:
            if errors:
                QMessageBox.warning(self,'Импорт','\n'.join(errors[:5]))
            return
        retained=list(getattr(self,'working_sources',[]))
        self.select_project(self.projects.currentIndex())
        if retained:self.add_working_sources(retained)
        if into_workspace and imported:self.add_working_sources(imported)
        elif select_new and imported and imported[-1] in self.asset_paths:
            self.add_working_sources([imported[-1]])
        if errors:
            QMessageBox.warning(self,'Импорт',f'Принято файлов: {successes}\nОшибки:\n'+'\n'.join(errors[:5]))
        else:
            self.statusBar().showMessage(f'Импортировано файлов: {successes}',7000)

    def import_reference_path(self, filename):
        if not self.current_project:
            self.append_chat('Система','Сначала выберите проект.')
            return
        src=Path(filename)
        if not src.is_file() or src.suffix.lower() not in IMAGE_EXTENSIONS:
            QMessageBox.warning(self,'Эталон','Перетащите файл изображения (JPG, PNG, WEBP и др.).')
            return
        if self.db.in_transaction:
            QMessageBox.warning(self,'Эталон','Импорт заблокирован: незавершённая транзакция SQLite.')
            return
        try:
            batch=self.core.import_media([src],project_id=self.current_project,
                                       kind='reference',allowed_extensions=IMAGE_EXTENSIONS)
            if batch.failures:raise ValueError(batch.failures[0][1])
            receipt=batch.receipts[0]
            if not receipt.published:
                self.statusBar().showMessage('Эталон принят в SQLite; публикация ожидает восстановления',10000)
                return
            self.show_reference()
            self.reference.show_file(str(receipt.final_path))
            self.comparison.setSizes([330,330,330])
        except Exception as exc:
            QMessageBox.warning(self,'Эталон',str(exc))

    def check_components(self):
        self.ai_status.setText('Диагностика компонентов…')
        try:
            info = inspect_components()
            self.append_chat('Диагностика', '\n'.join(f'{key}: {value}' for key, value in info.items()) +
                '\nИнструменты агента: '+str(available_tools())+'\nМодели устанавливаются только с согласия пользователя.')
        except Exception as exc:
            self.append_chat('Диагностика', 'Не удалось выполнить проверку: ' + str(exc))
        finally:
            self.ai_status.setText('Готов')

    def prepare_comfy(self):
        try:
            return get_comfy_checkpoints()
        except Exception:
            candidate = comfy_path()
            if not candidate:
                self.append_chat('Система', 'ComfyUI не запущен и установленная portable-версия не обнаружена. Установите ComfyUI или укажите путь к нему в будущих настройках.')
                return None
            self.append_chat('Система', f'Найден ComfyUI: {candidate}. Пробую запустить локальный движок без открытия консоли.')
            self.ai_status.setText('Запуск ComfyUI…')
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            try:
                ensure_comfyui_running(candidate)
                self.append_chat('Система', 'ComfyUI запущен и отвечает.')
                return get_comfy_checkpoints()
            except Exception as exc:
                self.append_chat('Система', 'Не удалось запустить ComfyUI: ' + str(exc))
                return None
            finally:
                self.ai_status.setText('Готов')

    def check_gpu(self):
        """Show a current snapshot, including free VRAM, not just nominal GPU size."""
        profile = detect_hardware()
        cards = profile.get('gpus') or []
        lines = ['Обнаруженное оборудование (данные на момент проверки):']
        if cards:
            for card in cards:
                lines.append(f"• {card['name']}: всего {card['vram_mb']} МБ, "
                             f"свободно {card['vram_free_mb']} МБ, драйвер {card['driver']}")
        else:
            lines.append('• NVIDIA через nvidia-smi не обнаружена.')
        lines.append(f"• RAM: всего {profile['ram_mb'] or 'неизвестно'} МБ, "
                     f"свободно {profile['ram_free_mb'] or 'неизвестно'} МБ")
        lines.append(f"• Логических CPU: {profile['cpu_cores'] or 'неизвестно'}")
        lines.extend('• ' + x for x in profile['guidance'])
        self.append_chat('Проверка оборудования', '\n'.join(lines))

    def _mask_identity(self):
        from project_operation_guard import mask_owner_matches
        if not self.current_project or not self.source.path:
            return None
        return (self.current_project, str(Path(self.source.path).resolve(strict=False)))

    def _mask_execution_busy(self):
        for field in ('neural_worker','smart_worker','neural_install','opencv_install'):
            worker=getattr(self,field,None)
            if worker is not None:
                try:
                    if worker.isRunning():return True
                except (AttributeError,RuntimeError):return True
        return False

    def neural_mask(self):
        if self._mask_execution_busy():
            self.append_chat('Система','Обработка маски уже выполняется.')
            return
        if not self.source.path:
            self.append_chat('AI-менеджер','Выберите исходное изображение.')
            return
        try:
            import rembg
            import onnxruntime
        except ImportError:
            reply=QMessageBox.question(self,'Нейросетевая сегментация',
                'Для точного выделения нужна бесплатная локальная модель U2-Net (около 176 МБ) '
                'и библиотеки rembg/ONNX Runtime. Они будут скачаны из интернета после подтверждения. '
                'Фотография никуда не отправляется. Установить?',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if reply!=QMessageBox.Yes:
                self.pending_background_prompt=None
                return
            self.append_chat('Система','Устанавливаю библиотеки сегментации. Это может занять несколько минут…')
            # Install in a worker so the app does not freeze.
            self._mask_install_owner=self._mask_identity()
            self._mask_install_succeeded=False
            self.neural_install=InstallSegmentationWorker()
            self.neural_install.progress.connect(lambda msg:self.append_chat('Установка',msg))
            self.neural_install.done.connect(self._mask_install_done)
            self.neural_install.failed.connect(lambda msg:(setattr(self,'pending_background_prompt',None),self.append_chat('Ошибка установки',msg)))
            # Do not restart mask processing on done: the installer thread is still running.
            self.neural_install.finished.connect(self._mask_install_finished)
            self.neural_install.start()
            return
        if getattr(self,'neural_worker',None) is not None and self.neural_worker.isRunning():
            return
        self.tabs.setCurrentIndex(self.mask_tab_index)
        self.ai_status.setText('Нейросеть выделяет объект…')
        self.append_chat('AI-менеджер','Начато локальное нейросетевое выделение. Первый запуск может потребовать загрузки модели U2-Net.')
        self._mask_owner=self._mask_identity()
        self.neural_worker=NeuralMaskWorker(self.source.path)
        self.neural_worker.progress.connect(lambda msg:self.ai_status.setText(msg))
        self.neural_worker.done.connect(self.smart_mask_done)
        self.neural_worker.failed.connect(self.smart_mask_failed)
        self.neural_worker.start()

    def _mask_install_done(self):
        # The done signal fires from run() before QThread.finished.
        self._mask_install_succeeded=True

    def _mask_install_finished(self):
        from project_operation_guard import mask_owner_matches
        owner=getattr(self,'_mask_install_owner',None)
        self._mask_install_owner=None
        succeeded=getattr(self,'_mask_install_succeeded',False)
        self._mask_install_succeeded=False
        if not succeeded:
            return
        if not mask_owner_matches(owner,self.current_project,self.source.path):
            self.pending_background_prompt=None
            self.append_chat('Система','После установки исходник или проект изменился; маска не запускается.')
            return
        # A successful installer exit does not ensure imports work in this interpreter.
        # Fail closed rather than entering another installation prompt loop.
        try:
            import importlib
            importlib.invalidate_caches()
            import rembg
            import onnxruntime
        except (ImportError, OSError) as exc:
            self.pending_background_prompt=None
            self.append_chat('Ошибка установки сегментации',
                             'Библиотеки установлены, но не загружаются: '+str(exc))
            return
        self.neural_mask()

    def smart_mask(self, mode='stroke'):
        if self._mask_execution_busy():
            self.append_chat('Система','Обработка маски уже выполняется.')
            return
        if not self.source.path:
            self.append_chat('AI-менеджер','Сначала выберите фотографию.')
            return
        try:
            import cv2
            import numpy
        except ImportError:
            reply=QMessageBox.question(self,'Компонент выделения',
                'Для уточнения границ нужна бесплатная библиотека OpenCV (~десятки МБ). '
                'Установить её в окружение AI SMM Studio? Никакие фотографии не отправляются в интернет.',
                QMessageBox.Yes|QMessageBox.No)
            if reply!=QMessageBox.Yes:
                self.pending_background_prompt=None
                return
            self._opencv_install_owner=self._mask_identity()
            self._opencv_install_mode=mode
            self._opencv_install_succeeded=False
            self.opencv_install=InstallOpenCVWorker()
            self.opencv_install.done.connect(self._opencv_install_done)
            self.opencv_install.failed.connect(self._opencv_install_failed)
            self.opencv_install.finished.connect(self._opencv_install_finished)
            self.opencv_install.start()
            return
        if mode=='stroke' and not self.mask_canvas.has_mask():
            self.append_chat('AI-менеджер','Проведите несколько грубых штрихов по человеку (не по фону), затем нажмите «Уточнить».')
            return
        self.refine_button.setEnabled(False)
        self.ai_status.setText('Уточняю границы объекта…')
        self._mask_owner=self._mask_identity()
        self.smart_worker=SmartMaskWorker(self.source.path,self.mask_canvas.mask_png(),mode)
        self.smart_worker.done.connect(self.smart_mask_done)
        self.smart_worker.failed.connect(self.smart_mask_failed)
        self.smart_worker.finished.connect(lambda:self.refine_button.setEnabled(True))
        self.smart_worker.start()

    def _opencv_install_done(self):
        self._opencv_install_succeeded=True

    def _opencv_install_failed(self, message):
        self.pending_background_prompt=None
        self.append_chat('Ошибка установки OpenCV',message)

    def _opencv_install_finished(self):
        from project_operation_guard import mask_owner_matches
        owner=getattr(self,'_opencv_install_owner',None)
        mode=getattr(self,'_opencv_install_mode','stroke')
        succeeded=getattr(self,'_opencv_install_succeeded',False)
        self._opencv_install_owner=None
        self._opencv_install_mode=None
        self._opencv_install_succeeded=False
        if not succeeded:
            return
        if not mask_owner_matches(owner,self.current_project,self.source.path):
            self.pending_background_prompt=None
            self.append_chat('Система','Исходник или проект изменился; выделение не запускается.')
            return
        # A successful pip exit is not proof that this running interpreter can
        # actually import the installed libraries. Avoid an installation loop.
        try:
            import importlib
            importlib.invalidate_caches()
            import cv2
            import numpy
        except ImportError as exc:
            self.pending_background_prompt=None
            self.append_chat('Ошибка установки OpenCV','Пакеты установлены, но не загружаются: '+str(exc))
            return
        self.smart_mask(mode)

    def smart_mask_done(self, data):
        from project_operation_guard import mask_owner_matches
        owner=self._mask_owner
        self._mask_owner=None
        if not mask_owner_matches(owner,self.current_project,self.source.path):
            self.pending_background_prompt=None
            self.ai_status.setText('Маска отклонена: исходник изменился')
            self.append_chat('Система','Запоздавшая маска относится к другому проекту или фото и не применена.')
            return
        self.mask_canvas.set_mask_from_pil(Image.open(io.BytesIO(data)))
        self.ai_status.setText('Маска готова — проверьте границы')
        self.append_chat('AI-менеджер',
            'Подготовлена предварительная маска фона. Красное будет изменено, человек должен остаться без выделения. '
            'Проверьте все объекты, которые должны сохраниться; при ошибках поправьте маску. '
            'Используется предварительная маска; точность не гарантирована.')
        if self.pending_background_prompt:
            prompt=self.pending_background_prompt
            self.pending_background_prompt=None
            preview=QDialog(self)
            preview.setWindowTitle('Проверка нейросетевой маски — красный фон будет изменён')
            layout=QVBoxLayout(preview)
            layout.addWidget(QLabel('Красный = область изменения. Сохраняемые объекты должны оставаться без красного.'))
            with Image.open(self.source.path) as im:
                src=ImageOps.exif_transpose(im).convert('RGBA')
            src.thumbnail((800,650))
            editable=Image.open(io.BytesIO(data)).convert('L').resize(src.size,Image.Resampling.BILINEAR)
            overlay=Image.new('RGBA',src.size,(240,30,30,0))
            overlay.putalpha(editable.point(lambda a:int(a*.45)))
            visual=Image.alpha_composite(src,overlay)
            buf=io.BytesIO();visual.save(buf,'PNG')
            label=QLabel()
            pix=QPixmap();pix.loadFromData(buf.getvalue(),'PNG')
            label.setPixmap(pix)
            layout.addWidget(label)
            layout.addWidget(QLabel('Если защищаемые объекты выделены красным, отмените генерацию.'))
            buttons=QHBoxLayout()
            yes=QPushButton('Маска верна — генерировать')
            no=QPushButton('Отмена — маска неверна')
            buttons.addWidget(yes);buttons.addWidget(no)
            layout.addLayout(buttons)
            yes.clicked.connect(preview.accept)
            no.clicked.connect(preview.reject)
            if preview.exec()==QDialog.Accepted:
                self.mask_instruction.setText(prompt)
                self.generate_image()
            else:
                self.append_chat('AI-менеджер','Генерация отменена: маска не подтверждена. Нейросеть могла выделить неверные объекты.')

    def smart_mask_failed(self, error):
        self._mask_owner=None
        self.pending_background_prompt=None
        self.ai_status.setText('Ошибка выделения')
        self.append_chat('Ошибка выделения',error)

    def show_engines(self):
        dialog=QDialog(self); dialog.setWindowTitle('AI-движки · Stage 28'); dialog.resize(620,700)
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('AI-агент использует доступные инструменты. Облачные операции — только с разрешения.'))
        layout.addWidget(QLabel('Состояние: '+', '.join(k+': '+('доступно' if v else 'не подключено') for k,v in available_tools().items())))
        from durable_cost_gate import DurableCostGate
        try:
            cost_gate = DurableCostGate(DB)
            usage = cost_gate.usage()
            budget_text = QLabel(
                f"Месячный лимит: ${usage['cap_usd']:.2f}; "
                f"учтено и зарезервировано: ${usage['committed_usd']:.2f}; "
                f"доступно: ${usage['available_usd']:.2f}; "
                f"платные вызовы: {'ЗАБЛОКИРОВАНЫ — проверка списания' if usage['paid_calls_frozen'] else 'только с разрешением и проверенной ценой'}")
            layout.addWidget(budget_text)
            budget_button = QPushButton('Изменить месячный лимит расходов (USD)')
            def update_budget():
                current = float(cost_gate.usage()['cap_usd'])
                amount, accepted = QInputDialog.getDouble(
                    dialog, 'Месячный лимит AI', 'Новый лимит, USD:',
                    current, 0.0, 1000000.0, 2)
                if not accepted:
                    return
                try:
                    cost_gate.set_monthly_cap(f'{amount:.2f}')
                    fresh = cost_gate.usage()
                    budget_text.setText(
                        f"Месячный лимит: ${fresh['cap_usd']:.2f}; "
                        f"учтено и зарезервировано: ${fresh['committed_usd']:.2f}; "
                        f"доступно: ${fresh['available_usd']:.2f}; "
                        f"платные вызовы: {'ЗАБЛОКИРОВАНЫ — проверка списания' if fresh['paid_calls_frozen'] else 'только с разрешением и проверенной ценой'}")
                except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
                    QMessageBox.warning(dialog, 'Бюджет AI', str(exc))
            budget_button.clicked.connect(update_budget)
            layout.addWidget(budget_button)
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            layout.addWidget(QLabel('Бюджет недоступен: '+str(exc)))
        layout.addWidget(QLabel('Режим генерации:'))
        selector=QComboBox();selector.addItems(['Локально: Qwen Image Edit 2509 (тяжёлая модель)','OpenAI API (заблокирован до проверки цены и бюджета)','Hugging Face Space (публичный Gradio API)'])
        selector.setCurrentIndex(getattr(self,'engine_mode',0));layout.addWidget(selector)
        layout.addWidget(QLabel('OpenAI API-ключ (только в памяти до закрытия программы):'))
        key=QLineEdit();key.setEchoMode(QLineEdit.Password)
        key.setPlaceholderText('sk-…');layout.addWidget(key)
        layout.addWidget(QLabel('Hugging Face Space ID (например owner/space):'))
        space=QLineEdit(getattr(self,'hf_space_id',''));space.setPlaceholderText('owner/space');layout.addWidget(space)
        layout.addWidget(QLabel('Gradio API endpoint (например /edit):'))
        endpoint=QLineEdit(getattr(self,'hf_api_name',''));endpoint.setPlaceholderText('/edit');layout.addWidget(endpoint)
        layout.addWidget(QLabel('HF Space: только API с аргументами (изображение, инструкция).\nБесплатность и доступность зависят от владельца Space.'))
        layout.addWidget(QLabel('Локальный редактор: Qwen Image Edit 2509 (веса — десятки ГБ, CUDA и много RAM).'))
        layout.addWidget(QLabel('Модель загружается после согласия; на целевых 8 ГБ VRAM возможен OOM.'))
        cuda_button=QPushButton('Проверить CUDA / PyTorch')
        cuda_button.clicked.connect(self.check_cuda)
        layout.addWidget(cuda_button)
        repair_button=QPushButton('Установить CUDA-сборку PyTorch (с подтверждением)')
        repair_button.clicked.connect(self.repair_cuda)
        layout.addWidget(repair_button)
        layout.addWidget(QLabel('Бесплатная локальная модель для понимания изображений: gemma3:4b'))
        pull=QPushButton('Установить gemma3:4b через Ollama (после согласия)')
        pull.clicked.connect(lambda:self.install_vision_model())
        layout.addWidget(pull)
        layout.addWidget(QLabel('Облачная генерация тарифицируется отдельно от ChatGPT Plus.\n'
            'Стоимость зависит от модели, размера и текущих тарифов провайдера.\n'
            'Перед отправкой потребуется отдельное подтверждение.'))
        save=QPushButton('Сохранить настройки на текущий сеанс')
        def apply():
            self.engine_mode=selector.currentIndex()
            if key.text().strip():set_openai_key(key.text().strip())
            self.hf_space_id=space.text().strip()
            self.hf_api_name=endpoint.text().strip()
            dialog.accept()
            self.append_chat('Система','Режим обработки: '+selector.currentText())
        save.clicked.connect(apply);layout.addWidget(save)
        clear_key=QPushButton('Очистить сеансовый ключ OpenAI сейчас')
        clear_key.clicked.connect(lambda:(clear_openai_key(),key.clear()))
        layout.addWidget(clear_key)
        dialog.exec()

    def check_cuda(self):
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
        py=ai_python()
        if not Path(py).is_file():
            result='Отдельное AI-окружение ещё не создано. Нажмите «Установить CUDA-сборку PyTorch». Python 3.14 основного приложения не затрагивается.'
        else:
            cmd=[py,'-c','import sys;print("Python:",sys.version);import torch;print("Torch:",torch.__version__);print("CUDA build:",torch.version.cuda);print("CUDA available:",torch.cuda.is_available());print("GPU:",torch.cuda.get_device_name(0) if torch.cuda.is_available() else "недоступна")']
            try:
                proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60,creationflags=flags)
                result=('AI Python: '+py+'\n'+proc.stdout+'\n'+proc.stderr).strip()
            except Exception as exc:result=str(exc)
        try:
            nv=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,memory.total','--format=csv,noheader'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15,creationflags=flags)
            result+='\nNVIDIA driver: '+(nv.stdout.strip() or nv.stderr.strip())
        except Exception as exc:result+='\nnvidia-smi: '+str(exc)
        self.append_chat('Диагностика CUDA',result)
        QMessageBox.information(self,'Диагностика CUDA',result[:3000])

    def repair_cuda(self):
        if getattr(self,'cuda_install_worker',None) is not None and self.cuda_install_worker.isRunning():
            if getattr(self,'cuda_progress_dialog',None):
                self.cuda_progress_dialog.show();self.cuda_progress_dialog.raise_()
            return
        reply=QMessageBox.question(self,'Проверка компонентов AI',
            'Проверить существующее AI-окружение и установить только отсутствующие компоненты? '
            'Если CUDA уже работает, повторная установка PyTorch не выполняется. Продолжить?',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if reply!=QMessageBox.Yes:return
        dialog=QDialog(self)
        dialog.setWindowTitle('AI SMM Studio — установка и проверка компонентов')
        dialog.resize(680,430)
        layout=QVBoxLayout(dialog)
        phase=QLabel('Подготовка проверки…')
        layout.addWidget(phase)
        bar=QProgressBar();bar.setRange(0,0)
        layout.addWidget(bar)
        elapsed=QLabel('Прошло: 0 с. Индикатор показывает активность; точный процент установки недоступен.')
        layout.addWidget(elapsed)
        log=QPlainTextEdit();log.setReadOnly(True)
        layout.addWidget(log)
        close_button=QPushButton('Скрыть окно (установка продолжится)')
        close_button.clicked.connect(dialog.hide)
        layout.addWidget(close_button)
        self.cuda_progress_dialog=dialog
        self.cuda_install_worker=CudaInstallWorker()
        started=time.monotonic()
        timer=QTimer(dialog)
        timer.timeout.connect(lambda:elapsed.setText(
            f'Прошло: {int(time.monotonic()-started)} с. Индикатор активности — не процент скачивания.'))
        timer.start(1000)
        def report(msg):
            phase.setText(msg[:170]);log.appendPlainText(msg)
            self.append_chat('CUDA PyTorch',msg)
        def complete():
            timer.stop();bar.setRange(0,100);bar.setValue(100)
            phase.setText('Проверка завершена: CUDA и библиотеки доступны')
            close_button.setText('Закрыть')
            self.append_chat('CUDA PyTorch','Компоненты готовы. Повторная установка не нужна.')
        def failure(err):
            timer.stop();bar.setRange(0,100);bar.setValue(0)
            phase.setText('Ошибка установки — подробности в журнале')
            log.appendPlainText(err);close_button.setText('Закрыть')
            self.append_chat('Ошибка CUDA PyTorch',err)
        self.cuda_install_worker.progress.connect(report)
        self.cuda_install_worker.done.connect(complete)
        self.cuda_install_worker.failed.connect(failure)
        self.cuda_install_worker.start()
        dialog.show()

    def install_vision_model(self):
        if self.download_worker is not None:
            if getattr(self,'model_progress_dialog',None):
                self.model_progress_dialog.show()
                self.model_progress_dialog.raise_()
            self.append_chat('Система','Загрузка модели уже выполняется.')
            return
        reply=QMessageBox.question(self,'Загрузка модели',
            'Скачать gemma3:4b через локальный Ollama? Загрузка нескольких ГБ; '
            'потребуется интернет и свободное место. Проверьте условия лицензии. Продолжить?',
            QMessageBox.Yes|QMessageBox.No)
        if reply!=QMessageBox.Yes:return
        self.download_worker=DownloadModelWorker('gemma3:4b')
        dialog=QDialog(self)
        dialog.setWindowTitle('Загрузка gemma3:4b — Stage 15')
        dialog.resize(480,170)
        dialog.setModal(False)
        layout=QVBoxLayout(dialog)
        heading=QLabel('Подготовка загрузки модели через Ollama…')
        heading.setWordWrap(True)
        layout.addWidget(heading)
        bar=QProgressBar()
        bar.setRange(0,0)
        layout.addWidget(bar)
        hint=QLabel('Окно можно оставить открытым. Модель загружается в Ollama.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        cancel=QPushButton('Отменить загрузку')
        layout.addWidget(cancel)
        self.model_progress_dialog=dialog
        def on_progress(message,percent):
            heading.setText(message)
            if percent<0:
                bar.setRange(0,0)
            else:
                bar.setRange(0,100)
                bar.setValue(percent)
        def on_done(name):
            bar.setRange(0,100);bar.setValue(100)
            heading.setText('Модель установлена: '+name)
            cancel.setText('Закрыть')
            self.append_chat('Система','Модель установлена: '+name)
        def on_failed(message):
            bar.setRange(0,100);bar.setValue(0)
            heading.setText('Ошибка: '+message)
            cancel.setText('Закрыть')
            self.append_chat('Ошибка загрузки',message)
        def on_cancelled():
            heading.setText('Загрузка отменена. Частично загруженные данные могут остаться в Ollama.')
            cancel.setText('Закрыть')
            self.append_chat('Система','Загрузка gemma3:4b отменена.')
        def on_cancel_click():
            if self.download_worker is not None:
                self.download_worker.cancel()
                cancel.setEnabled(False)
                heading.setText('Отменяю загрузку…')
            else:
                dialog.close()
        cancel.clicked.connect(on_cancel_click)
        def on_finish():
            self.download_worker=None
            cancel.setEnabled(True)
        self.download_worker.progress.connect(on_progress)
        self.download_worker.done.connect(on_done)
        self.download_worker.failed.connect(on_failed)
        self.download_worker.cancelled.connect(on_cancelled)
        self.download_worker.finished.connect(on_finish)
        self.download_worker.start()
        dialog.show()
        self.append_chat('Система','Загрузка gemma3:4b запущена. Открыто окно с прогрессом.')

    def task_reference(self, instruction, *, use_reference=None):
        """Never include the reference unless the user explicitly requests it."""
        if not (mentions_reference(instruction) if use_reference is None else use_reference):
            return None
        reference=getattr(self.reference, 'path', None)
        if not reference:
            self.append_chat('Система', 'В задании упомянут эталон, но эталон не выбран. Обработка без эталона.')
            return None
        return reference

    def checked_source(self):
        """Fail closed when the editable input points at the protected reference."""
        source=getattr(self.source, 'path', None)
        reference=getattr(self.reference, 'path', None)
        if source and reference and same_file(source, reference):
            self.append_chat('Ошибка выбора исходника',
                'Выбранный исходник совпадает с эталоном. Обработка запрещена. Выберите настоящий исходник.')
            return None
        if source and Path(source).suffix.lower() not in IMAGE_EXTENSIONS:
            return None
        return source

    def edit_with_selected_engine(self):
        if not self.current_project or not self.source.path:
            self.append_chat('Система','Сначала выберите проект и исходную фотографию.');return
        prompt,ok=QInputDialog.getMultiLineText(self,'Редактирование фото','Опишите желаемое изменение:')
        if not ok or not prompt.strip():return
        if getattr(self,'engine_mode',0)==1:self.cloud_generate(prompt.strip())
        elif getattr(self,'engine_mode',0)==2:self.hf_generate(prompt.strip())
        else:
            self.modern_local_generate(prompt.strip())

    def open_results_folder(self):
        if not self.current_project:
            self.append_chat('Система','Сначала выберите проект.');return
        folder=self.core.projects.activate(self.current_project)/'processed'
        folder.mkdir(parents=True,exist_ok=True)
        try:
            if sys.platform=='win32':os.startfile(str(folder))
            else:subprocess.Popen(['xdg-open',str(folder)])
        except Exception as exc:self.append_chat('Система',f'Не удалось открыть папку {folder}: {exc}')

    def start_batch(self):
        if self.batch_active or self.modern_worker is not None or self.cloud_worker is not None:
            self.append_chat('Система','Дождитесь завершения текущей обработки.');return
        if not self.current_project:
            self.append_chat('Система','Сначала выберите проект.');return
        if self.db.in_transaction:
            self.append_chat('Система','Пакетная обработка заблокирована: незавершённая транзакция SQLite.')
            return
        indices=sorted({self.source_list.row(item) for item in self.source_list.selectedItems()})
        paths=[self.working_sources[i] for i in indices if 0<=i<len(self.working_sources)
               and Path(self.working_sources[i]).suffix.lower() in IMAGE_EXTENSIONS]
        paths=[path for path in paths if not same_file(path, getattr(self.reference, 'path', None))]
        if not paths:
            self.append_chat('Система','Выделите исходные фотографии в списке через Ctrl/Shift.');return
        prompt=self.message.toPlainText().strip()
        if not prompt:
            self.append_chat('Система','Введите команду для всех выбранных фотографий.');return
        mode=getattr(self,'engine_mode',0)
        if mode==1:
            self.append_chat('Система',
                'Платная пакетная обработка отключена: нет проверенной верхней цены '
                'за каждый файл и отдельного разрешения на каждую операцию.')
            return
        if mode==2:
            self.append_chat('Система','Пакетная обработка через HF Space пока не поддерживается. Используйте одиночное редактирование.')
            return
        if mode==1:
            answer=QMessageBox.question(self,'Подтверждение пакетной облачной обработки',
                f'Отправить {len(paths)} изображений в OpenAI API? Каждая обработка оплачивается отдельно. '
                'Точная суммарная стоимость неизвестна. Продолжить?',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        else:
            answer=QMessageBox.question(self,'Пакетная локальная обработка',
                f'Последовательно обработать {len(paths)} фотографий через Qwen Image Edit? '
                'При первом запуске модель может скачиваться. Продолжить?',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        # The durable queue is committed before mutating in-memory worker state.
        # An aborted INSERT must not leave batch_active=True with no recovery row.
        try:
            accepted=self.core.batches.create(self.current_project,paths,prompt,mode,timestamp=now())
        except (sqlite3.Error,OSError,ValueError,RuntimeError) as exc:
            self.append_chat('Система','Пакетная обработка не запущена: '+str(exc))
            return
        self.batch_queue=list(accepted.paths)
        self.batch_total=accepted.total
        self.batch_prompt=accepted.prompt
        self.batch_run_id=accepted.batch_id
        self.batch_owner_project=accepted.project_id
        self.batch_claim=None
        self.batch_inflight=None
        self.batch_active=True
        self.message.clear()
        self.append_chat('Пакетная обработка',f'Задание: {prompt}. Файлов: {len(paths)}. Обрабатываю последовательно.')
        self.batch_next()

    def batch_next(self):
        if not self.batch_active:return
        # The durable ledger, not the Qt queue snapshot, chooses the next item.
        # A project switch must never dispatch a worker into another project.
        if self.current_project != self.batch_owner_project:
            self.batch_active=False
            self.append_chat('Пакетная обработка','Проект изменился; очередь сохранена для возобновления.')
            return
        try:
            claim=self.core.batches.advance(self.batch_owner_project,self.batch_run_id,timestamp=now())
        except (ValueError,OSError,sqlite3.Error,RuntimeError) as exc:
            self.batch_active=False
            self.append_chat('Пакетная обработка','Очередь остановлена без потери заданий: '+str(exc))
            return
        if claim is None:
            self.batch_active=False
            self.batch_inflight=None
            self.batch_claim=None
            self.batch_queue=[]
            self.append_chat('Пакетная обработка',f'Очередь завершена: {self.batch_total} файлов. Результаты в папке processed.')
            self.ai_status.setText('Готов')
            return
        self.batch_claim=claim
        self.batch_inflight=claim.source_path
        pending=self.core.batches.pending_all(self.batch_owner_project)
        record=next((r for r in pending if r.batch_id==self.batch_run_id),None)
        self.batch_queue=list(record.paths[1:]) if record else []
        number=self.batch_total-len(record.paths)+1 if record else self.batch_total
        self.append_chat('Пакетная обработка',f'{number}/{self.batch_total}: {Path(claim.source_path).name}')
        self.source.show_file(claim.source_path)
        try:
            if self.engine_mode==1:
                self.cloud_generate(self.batch_prompt,confirmed=True)
            else:
                self.modern_local_generate(self.batch_prompt,confirmed=True)
            if self.cloud_worker is None and self.modern_worker is None:
                raise RuntimeError('Batch worker did not start')
        except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
            self.core.batches.interrupt(claim,timestamp=now())
            self.batch_active=False
            self.batch_claim=None
            self.batch_inflight=None
            self.append_chat('Пакетная обработка','Исполнитель не запустился; элемент сохранён для повтора: '+str(exc))

    def batch_cancel(self):
        if self.batch_active:
            try:self.core.batches.cancel(self.batch_owner_project,self.batch_run_id,timestamp=now())
            except (sqlite3.Error,ValueError,RuntimeError) as exc:
                self.append_chat('Пакетная обработка','Отмена не подтверждена: '+str(exc))
                return
            self.batch_queue.clear()
            self.batch_active=False
            self.batch_claim=None
            self.batch_inflight=None
            self.append_chat('Пакетная обработка','Очередь отменена; уже сохранённые результаты остаются.')

    def _batch_worker_finished(self):
        claim=self.batch_claim
        if claim is not None:
            # A finished signal is not a success receipt. Failed/empty worker output
            # cannot remove an item or advance the next worker.
            try:self.core.batches.interrupt(claim,timestamp=now())
            except (sqlite3.Error,ValueError,RuntimeError) as exc:
                self.append_chat('Пакетная обработка','Сбой фиксации неудачной попытки: '+str(exc))
            self.batch_active=False
            self.append_chat('Пакетная обработка','Результат не подтверждён. Очередь сохранена для повторного запуска.')
        self.batch_claim=None
        self.batch_inflight=None
        if self.batch_active:QTimer.singleShot(0,self.batch_next)

    def modern_local_generate(self, prompt, confirmed=False, *, use_reference=None):
        if not self.checked_source():
            self.append_chat('Система', 'Выберите допустимый исходник, отличный от эталона.')
            return
        if self.modern_worker is not None:
            elapsed=int(time.monotonic()-self.modern_started_at) if self.modern_started_at else 0
            self.append_chat('Система',f'Локальная задача ещё активна ({elapsed//60} мин {elapsed%60} сек). Последний этап: {self.modern_last_event or "нет данных"}. Для остановки нажмите «Остановить AI-обработку».');return
        if not self.source.path or not self.current_project:
            self.append_chat('Система','Сначала выберите исходное изображение и проект.');return
        try:
            import importlib.util
            installed=Path(ai_python()).is_file()
        except Exception:installed=False
        warning=('Qwen Image Edit 2509 — универсальная модель редактирования по тексту. '
                 'Веса занимают десятки ГБ, нужны NVIDIA CUDA, много RAM и места на диске. '
                 'Можно выбрать быстрый, качественный или совместимый режим. Быстрый режим может потребовать больше VRAM. '
                 'Первый запуск скачает веса с Hugging Face; точный объём пока не рассчитывается. '
                 'Фото не отправляется в облачный API.\n\n'
                 + ('Также установятся библиотеки PyTorch/Diffusers.\n\n' if not installed else '')
                 +'Продолжить?')
        if not confirmed:
            reply=QMessageBox.question(self,'Локальная AI-обработка',warning,
                                       QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if reply!=QMessageBox.Yes:return
        choices = [
            'Быстрый · model offload · 8 шагов · 512px (может не хватить VRAM)',
            'Качественный · model offload · 16 шагов · 640px (риск нехватки VRAM/RAM)',
            'Совместимый · sequential offload · 12 шагов · 512px (медленно)',
            'Экономный по времени · sequential offload · 8 шагов · 512px (качество может снизиться)',
            'Экспериментальный · group offload · 12 шагов · 512px (может быть быстрее; зависит от diffusers)'
        ]
        choice, accepted = QInputDialog.getItem(self, 'Режим локального редактирования',
            '12 ГБ VRAM: совместимый режим надёжнее, но медленнее. Быстрый может вызвать OOM:', choices, 2, False)
        if not accepted:
            return
        mode_index = choices.index(choice)
        offload, steps, max_side = [('model',8,512), ('model',16,640), ('sequential',12,512), ('sequential',8,512), ('group',12,512)][mode_index]
        reference=self.task_reference(prompt,use_reference=use_reference)
        output=self.core.workspace(self.current_project)/'processed'/(uuid4().hex+'.png')
        self.append_chat('Система', f'Редактируемый исходник: {Path(self.source.path).name}. Эталон: '+('только справочно' if reference else 'не используется')+'.')
        self.modern_worker=ModernLocalWorker(self.source.path,reference,output,prompt,install=not installed,offload=offload,steps=steps,max_side=max_side,use_reference=use_reference)
        self.modern_worker.progress.connect(self.modern_progress)
        self.modern_worker.done.connect(lambda path, pid=self.current_project, claim=self.batch_claim:self.cloud_done(path,project_id=pid,batch_claim=claim))
        self.modern_worker.failed.connect(lambda err:self.append_chat('Ошибка Qwen Image Edit',err))
        self.modern_worker.finished.connect(self.modern_finished)
        self.modern_started_at=time.monotonic()
        self.modern_last_event='Запуск'
        self.cancel_ai_button.setEnabled(True)
        self.modern_timer.start()
        self.ai_status.setText('Подготовка Qwen Image Edit…')
        self.append_chat('AI-менеджер',f'Qwen Image Edit 2509: {max_side}px, {steps} шагов, offload={offload}. Исходник сохранится; ускорение не гарантировано.')
        self.modern_worker.start()

    def modern_progress(self, msg):
        self.modern_last_event=msg
        self.ai_status.setText(msg[:90])
        self.append_chat('Локальная обработка',msg)

    def modern_heartbeat(self):
        if self.modern_worker is None:
            self.modern_timer.stop()
            return
        elapsed=int(time.monotonic()-self.modern_started_at) if self.modern_started_at else 0
        self.ai_status.setText(f'AI-задача {elapsed//60:02d}:{elapsed%60:02d} · {str(self.modern_last_event)[:65]}')
        self.statusBar().showMessage(f'Обработка фото: прошло {elapsed//60} мин {elapsed%60:02d} сек · {str(self.modern_last_event)[:110]}')

    def cancel_modern(self):
        if self.modern_worker is None:
            self.batch_cancel();return
        if QMessageBox.question(self,'Остановить обработку',
            'Прервать локальную AI-задачу и её дочерний процесс? Незавершённая загрузка модели может остаться в кэше.',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.batch_cancel()
        self.append_chat('Система','Останавливаю локальную задачу…')
        self.cancel_ai_button.setEnabled(False)
        self.modern_worker.cancel()

    def modern_finished(self):
        self.modern_timer.stop()
        self.cancel_ai_button.setEnabled(False)
        self.modern_worker=None
        self.modern_started_at=None
        self.ai_status.setText('Готов')
        self._batch_worker_finished()

    def hf_generate(self, prompt, *, use_reference=None):
        if not self.checked_source():
            self.append_chat('Система', 'Выберите допустимый исходник, отличный от эталона.')
            return
        space=getattr(self,'hf_space_id','').strip()
        endpoint=getattr(self,'hf_api_name','').strip()
        if not space or not endpoint.startswith('/') or space.count('/')!=1:
            self.append_chat('Система','Откройте «AI-движки» и задайте HF Space ID (owner/space) и API endpoint (/edit).')
            return
        if self.cloud_worker is not None:return
        if not self.source.path or not self.current_project:return
        if mentions_reference(prompt) if use_reference is None else use_reference:
            self.append_chat('Система','Этот Hugging Face Space не поддерживает эталон. Выберите другой движок или уберите ссылку на эталон.')
            return
        reply=QMessageBox.question(self,'Отправка фотографии в Hugging Face',
            f'Отправить фотографию и текст стороннему публичному Space {space}?\n'
            'Владелец Space может видеть отправленные данные. Бесплатность и лимиты не гарантируются.\n'
            'Автоматической платной обработки нет. Продолжить?',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if reply!=QMessageBox.Yes:return
        output=self.core.workspace(self.current_project)/'processed'/(uuid4().hex+'.png')
        self.append_chat('Система', f'Облачный исходник: {Path(self.source.path).name}. Эталон не отправляется.')
        self.cloud_worker=HuggingFaceSpaceWorker(self.source.path,output,prompt,space,endpoint)
        self.cloud_worker.done.connect(lambda path, pid=self.current_project, claim=self.batch_claim:self.cloud_done(path,project_id=pid,batch_claim=claim))
        self.cloud_worker.failed.connect(lambda err:self.append_chat('Ошибка Hugging Face Space',err))
        self.cloud_worker.finished.connect(self.cloud_finished)
        self.ai_status.setText('Hugging Face Space: обработка…')
        self.append_chat('AI-менеджер',f'Отправляю задачу в {space} через {endpoint}.')
        self.cloud_worker.start()

    def cloud_generate(self, prompt, confirmed=False, *, use_reference=None):
        if not self.checked_source():
            self.append_chat('Система', 'Выберите допустимый исходник, отличный от эталона.')
            return
        # A Yes/No prompt with unknown price is not spending authorization.
        # Keep this fail-closed until a provider quote and per-call permit exist.
        from paid_provider_boundary import require_priced_paid_call
        from cost_authorization import PaymentDenied
        try:
            require_priced_paid_call('openai', 'image_edit')
        except PaymentDenied as exc:
            self.append_chat('Система', str(exc))
            return
        if not get_openai_key():
            self.append_chat('Система','Для облачной генерации откройте «AI-движки» и введите API-ключ OpenAI.')
            return
        if self.cloud_worker is not None:return
        reference=self.task_reference(prompt,use_reference=use_reference)
        reply=QMessageBox.Yes if confirmed else QMessageBox.question(self,'Подтверждение платной облачной обработки',
            'В OpenAI будут отправлены исходная фотография'+(' и эталон' if reference else '')+'.\n'
            'API может списать плату. Точную стоимость приложение пока не рассчитывает. '
            'Проверьте действующие тарифы OpenAI перед согласием.\n\nОтправить файлы и выполнить платную операцию?',
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if not confirmed and reply!=QMessageBox.Yes:return
        output=self.core.workspace(self.current_project)/'processed'/(uuid4().hex+'.png')
        self.append_chat('Система', f'Облачный исходник: {Path(self.source.path).name}. Эталон: '+('отправляется по запросу' if reference else 'не отправляется')+'.')
        self.cloud_worker=CloudEditWorker(self.source.path,reference,output,prompt,get_openai_key(),use_reference=use_reference)
        self.cloud_worker.done.connect(lambda path, pid=self.current_project, claim=self.batch_claim:self.cloud_done(path,project_id=pid,batch_claim=claim))
        self.cloud_worker.failed.connect(lambda err:self.append_chat('Ошибка облачного API',err))
        self.cloud_worker.finished.connect(self.cloud_finished)
        self.ai_status.setText('Обработка OpenAI API…')
        self.append_chat('AI-менеджер','Файлы отправляются в OpenAI API после вашего подтверждения. Ожидаем результат.')
        self.cloud_worker.start()

    def cloud_done(self,path,*,project_id=None,batch_claim=None):
        if self.db.in_transaction:
            raise RuntimeError('SQLite: media publication cannot adopt an unrelated transaction')
        # The application core verifies bytes, ownership and SQL publication.
        # Only then may the associated dialogue be marked resolved.
        pid=project_id
        if not isinstance(pid, str) or not pid:
            raise ValueError('Missing immutable image worker project identity')
        args=self._semantic_media_publication(pid,'image') if batch_claim is None else {}
        if batch_claim is not None and (batch_claim != self.batch_claim or pid != batch_claim.project_id):
            raise ValueError('The callback no longer owns this batch item')
        published=self.core.publish_result(pid,'image',path,active_project_id=pid,
                                           batch_claim=batch_claim,**args)
        if batch_claim is not None:
            self.batch_claim=None  # SQLite publication and receipt committed together.
        self._semantic_media_accepted(pid,'image')
        if self.current_project==pid:
            artifact=Path(path).resolve(strict=True)
            self.add_result_version(str(artifact))
            self.register_ready_result(str(artifact))
            self.append_chat('Система','Новая версия сохранена отдельно. Проверьте соответствие запросу и сохранность важных деталей.')
        return published

    def cloud_finished(self):
        self.cloud_worker=None;self.ai_status.setText('Готов')
        self._batch_worker_finished()

    def generate_image(self):
        if not self.current_project or not self.source.path or Path(self.source.path).suffix.lower() not in IMAGE_EXTENSIONS:
            self.append_chat('Система','Сначала выберите исходную фотографию.'); return
        from project_operation_guard import project_operation_busy
        if project_operation_busy(self):
            self.append_chat('Система','Завершите текущую операцию перед запуском новой обработки.')
            return
        if getattr(self,'engine_mode',0)==1:
            prompt=self.mask_instruction.text().strip() or self.message.toPlainText().strip()
            if not prompt:
                self.append_chat('Система','Введите инструкцию для обработки изображения.');return
            self.cloud_generate(prompt);return
        if not self.mask_canvas.has_mask():
            self.tabs.setCurrentIndex(self.mask_tab_index)
            self.append_chat('AI-менеджер','Закрасьте кистью область изменения на вкладке «Редактирование области». Оригинал не изменится.')
            return
        prompt=self.mask_instruction.text().strip() or self.message.toPlainText().strip()
        if not prompt:
            self.tabs.setCurrentIndex(self.mask_tab_index)
            self.append_chat('AI-менеджер','Напишите, что нужно сделать с выделенной областью.'); return
        choices=self.prepare_comfy()
        if not choices:return
        compatible=[name for name in choices if 'inpaint' in name.lower()]
        if not compatible:
            self.append_chat('AI-менеджер','Не найдена установленная inpainting-модель ComfyUI. Новые модели без согласия не скачиваются.'); return
        checkpoint=next((x for x in compatible if 'sd-v1-5-inpainting' in x.lower()),compatible[0])
        output=self.core.workspace(self.current_project)/'processed'/(uuid4().hex+'.png')
        self._generator_owner=(self.current_project,str(output))
        self.generator=GenerateWorker(self.source.path,output,prompt,checkpoint,1.0,self.mask_canvas.mask_png())
        self.generator.done.connect(self.generation_done)
        self.generator.failed.connect(self.generation_failed)
        self.generate_button.setEnabled(False); self.projects.setEnabled(False)
        self.ai_status.setText('AI редактирует область…')
        self.append_chat('AI-менеджер','Начал локальную обработку выделенной области через ComfyUI. Результат появится справа.')
        self.generator.start()

    def add_result_version(self, path):
        self.result_versions.insert(0, str(path))
        self.result_list.insertItem(0, Path(path).name)
        self.result_list.setCurrentRow(0)

    def generation_done(self,path):
        try:
            owner = self._generator_owner
            if owner is None:
                raise ValueError('У результата отсутствует зарегистрированный владелец')
            pid, expected = owner
            from media_result_ownership import commit_owned_image
            saved = commit_owned_image(self.db, pid, expected, path, self.core.workspace(pid))
            if self.current_project == pid:
                self.add_result_version(saved)
                self.register_ready_result(saved)
                self.append_chat('Система','Генеративная версия сохранена отдельно. Пиксели вне маски сохранены; внутри выделения возможны изменения деталей. Эталон пока не используется как визуальное условие.')
        except Exception as exc:
            self.append_chat('Ошибка генерации','Результат не принят: '+str(exc))
        finally:
            self.generation_finished()

    def generation_failed(self,error):
        self.append_chat('Ошибка генерации',error)
        self.generation_finished()

    def generation_finished(self):
        self._generator_owner = None
        self.generator = None
        self.generate_button.setEnabled(True)
        self.projects.setEnabled(True)
        self.ai_status.setText('Готов')

    def process_image(self):
        if not self.checked_source():
            self.append_chat('Система', 'Выберите допустимый исходник, отличный от эталона.')
            return
        if not self.current_project or not self.source.path:
            QMessageBox.information(self, 'Обработка', 'Выберите проект и исходное изображение.')
            return
        from project_operation_guard import project_operation_busy
        if project_operation_busy(self):
            self.append_chat('Система','Завершите текущую операцию перед запуском новой обработки.')
            return
        if Path(self.source.path).suffix.lower() not in IMAGE_EXTENSIONS:
            QMessageBox.information(self, 'Обработка', 'В этой версии обрабатываются фотографии, не видео.')
            return
        reference = self.task_reference(self.message.toPlainText().strip())
        output = self.core.workspace(self.current_project) / 'processed' / (uuid4().hex + '.jpg')
        self._edit_owner = (self.current_project, str(output))
        self.edit_worker = EditWorker(self.source.path, reference, output, self.message.toPlainText().strip())
        self.edit_worker.done.connect(self.image_done)
        self.edit_worker.failed.connect(self.image_failed)
        self.process_button.setEnabled(False)
        self.projects.setEnabled(False)
        self.ai_status.setText('Обработка фото…')
        self.edit_worker.start()

    def image_done(self, path):
        try:
            owner = self._edit_owner
            if owner is None:
                raise ValueError('У результата отсутствует зарегистрированный владелец')
            pid, expected = owner
            from media_result_ownership import commit_owned_image
            saved = commit_owned_image(self.db, pid, expected, path, self.core.workspace(pid))
            if self.current_project == pid:
                self.add_result_version(saved)
                self.register_ready_result(saved)
                self.append_chat('Система', 'Создана новая версия изображения: локальная цветовая/тоновая коррекция' +
                                 (' с ориентацией на эталон.' if self.edit_worker.reference else '.') +
                                 ' Оригинал сохранён. Это не генеративное изменение сцены или позы.')
        except Exception as exc:
            self.append_chat('Ошибка обработки','Результат не принят: '+str(exc))
        finally:
            self._edit_owner = None
            self.edit_worker = None
            self.projects.setEnabled(True)
            self.process_button.setEnabled(True)
            self.ai_status.setText('Готов')

    def image_failed(self, error):
        self._edit_owner = None
        self.edit_worker = None
        self.projects.setEnabled(True)
        self.process_button.setEnabled(True)
        self.ai_status.setText('Ошибка обработки')
        QMessageBox.warning(self, 'Обработка фото', error)

    def analyze_images(self):
        if not self.current_project or not self.source.path:
            QMessageBox.information(self, 'Анализ', 'Выберите проект и фотографию.')
            return
        if self.worker is not None: return
        try:
            with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=4) as response:
                models = [m.get('name', '') for m in json.load(response).get('models', [])]
        except Exception as exc:
            QMessageBox.warning(self, 'Ollama', 'Не удалось проверить Ollama: ' + str(exc))
            return
        if not any(m.split(':')[0] == 'gemma3' and m.split(':')[-1] == '4b' for m in models):
            QMessageBox.information(self, 'Нужна модель зрения',
                'Для анализа изображений установите локальную модель зрения gemma3:4b.\n'
                'Команда: ollama pull gemma3:4b\n\n'
                'Это дополнительная загрузка нескольких ГБ. Без неё локальная цветокоррекция уже работает.')
            return
        try:
            images = []
            for path in [self.source.path]:
                if not path: continue
                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im).convert('RGB')
                    im.thumbnail((1024, 1024))
                    buffer = io.BytesIO()
                    im.save(buffer, format='JPEG', quality=85)
                    images.append(base64.b64encode(buffer.getvalue()).decode('ascii'))
            prompt = ('Проанализируй фотографии на русском языке. Первая — исходник; '
                      + ('вторая — эталон стиля. ' if len(images) > 1 else '')
                      + 'Опиши реально видимые объекты и стиль, предложи корректировки цвета и света. '
                        'Не утверждай, что уже изменил изображения. Не придумывай детали.')
            messages = [{'role': 'user', 'content': prompt, 'images': images}]
            self.worker = AIWorker(messages, VISION_MODEL)
            # Result ownership is fixed at task submission, never at callback time.
            self.worker.done.connect(lambda response, pid=self.current_project:
                                     self.vision_done(response, project_id=pid))
            self.worker.failed.connect(self.ai_failed)
            self.vision_button.setEnabled(False)
            self.send_button.setEnabled(False)
            self.projects.setEnabled(False)
            self.ai_status.setText('AI анализирует фото…')
            self.worker.start()
        except Exception as exc:
            QMessageBox.warning(self, 'Анализ', str(exc))

    def vision_done(self, response, *, project_id=None):
        pid = project_id
        try:
            if not isinstance(pid, str) or not pid:
                raise ValueError('Missing immutable vision worker project identity')
            self.core.publish_result(pid,'message', '[Анализ изображений]\n' + response,
                                     active_project_id=pid)
            if self.current_project == pid:
                self.append_chat('AI · анализ изображений', response)
        except Exception as exc:
            self.statusBar().showMessage('Анализ не принят: ' + str(exc), 15000)
        finally:
            self.finish_worker()

    def import_assets(self):
        if not self.current_project:
            self.append_chat('Система', 'Сначала выберите проект.')
            return
        paths, _ = QFileDialog.getOpenFileNames(self, 'Выберите исходные файлы')
        if not paths: return
        self.import_asset_paths(paths, into_workspace=True)

    def add_project_file(self, kind, folder_name, label):
        if not self.current_project:
            self.append_chat('Система', 'Сначала выберите проект.')
            return None
        if self.db.in_transaction:
            QMessageBox.warning(self, 'Импорт заблокирован', 'Незавершённая транзакция SQLite')
            return None
        if (kind,folder_name)!=('result','processed'):
            QMessageBox.warning(self,'Импорт заблокирован','Неизвестный тип файла проекта')
            return None
        filename, _ = QFileDialog.getOpenFileName(self, label)
        if not filename:return None
        try:
            result=self.core.import_result_file(self.current_project,Path(filename),item_id=uuid4().hex)
            if not result.published:
                raise RuntimeError('Результат принят SQLite; файл ожидает восстановления')
            return str(result.final_path)
        except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
            QMessageBox.warning(self,'Импорт не завершён',str(exc))
            return None

    def pick_reference(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Выберите эталон стиля')
        if filename: self.import_reference_path(filename)

    def pick_result(self):
        path = self.add_project_file('result', 'processed', 'Выберите готовый результат')
        if path: self.result.show_file(path)

    def save_text(self):
        """Qt adapter: the application core owns persistence and recovery."""
        if not self.current_project:
            self.append_chat('Система', 'Сначала выберите проект.')
            return False
        content = self.text_editor.toPlainText()
        if not content.strip():
            return False
        try:
            result=self.core.save_text(self.current_project,content,now())
        except Exception as exc:
            QMessageBox.warning(self,'Сохранение не завершено',str(exc))
            return False
        if not result.exported:
            self.statusBar().showMessage('Текст принят SQLite; экспорт ожидает восстановления',9000)
            return False
        self.statusBar().showMessage(f'Текст сохранён: {result.path}',7000)
        return True

    def saved_document_message(self, kind, title):
        """A navigation receipt, never the full saved artifact in the dialogue."""
        section={'strategy':'Стратегии','calendar':'Контент-планы','plans':'Планы и схемы',
                 'tasks':'Задачи','analytics':'Аналитика'}.get(kind,'Документы проекта')
        return 'Документ «'+str(title)+'» сохранён в разделе «'+section+'». Откройте его в рабочей области.'

    def append_chat(self, speaker, content, created_at=None):
        """Render assistant Markdown after any result transaction commits."""
        deferred=getattr(self,'_result_effects',None)
        if deferred is not None:
            deferred.append(lambda:self.append_chat(speaker,content,created_at))
            return
        who = str(speaker)
        message = str(content)
        if created_at is None:
            self.record_project_diagnostic('interface_message',{'speaker':who,'content':message})
        user = who in ('Вы', 'user')
        assistant = who in ('AI', 'AI-менеджер', 'assistant')
        # Historical message bytes remain intact. If an assistant message is
        # an exact saved project artifact, display its location instead of
        # duplicating the document inside the dialogue. No fuzzy matching or
        # inferred saving/approval is permitted.
        if assistant and self.current_project:
            saved=self.core.data.document_for_content(self.current_project,message)
            if saved:message=self.saved_document_message(saved[0],saved[1])
        if not (user or assistant):
            self.technical_log.append(
                '<b>' + html.escape(who) + ':</b> ' + html.escape(message).replace('\n', '<br>'))
            return
        label = 'Вы' if user else 'AI-менеджер'
        try:
            stamp = datetime.fromisoformat(str(created_at).replace('Z','+00:00')).astimezone().strftime('%H:%M:%S') if created_at else datetime.now().strftime('%H:%M:%S')
        except (ValueError, TypeError):
            stamp = datetime.now().strftime('%H:%M:%S')
        background = '#204f52' if user else '#252d39'
        alignment = 'right' if user else 'left'
        if user:
            rendered = '<p style="margin:0;">' + html.escape(message).replace('\n', '<br>') + '</p>'
        else:
            # QTextDocument supports headings, lists, bold, links, and GFM tables.
            # Extract the rendered body; nesting full HTML documents is invalid.
            document = QTextDocument()
            document.setDefaultFont(QFont('Segoe UI', 11))
            document.setMarkdown(message)
            generated = document.toHtml()
            match = re.search(r'<body[^>]*>(.*?)</body>', generated, re.I | re.S)
            rendered = match.group(1) if match else html.escape(message).replace('\n', '<br>')
        block = (f'<table width="100%" cellspacing="0" cellpadding="4"><tr>'
                 f'<td align="{alignment}"><table width="94%" cellspacing="0" cellpadding="12" '
                 f'bgcolor="{background}"><tr><td>'
                 f'<p style="font-size:12px;color:#a8bdc8;margin:0 0 8px 0;"><b>{label}</b> · {stamp}</p>'
                 f'<div style="font-size:14px;color:#f2f5f7;">{rendered}</div>'
                 f'</td></tr></table></td></tr></table><p></p>')
        self.chat.moveCursor(self.chat.textCursor().MoveOperation.End)
        self.chat.insertHtml(block)
        self.chat.ensureCursorVisible()

    def is_direct_image_edit(self, msg):
        """Tool safety gate: a command about the selected image must not become chat.

        Uses generic action verbs, never object-specific words or fixed scenes.
        Text requests/questions remain eligible for Ollama.
        """
        import re
        source = getattr(self.source, 'path', None)
        if not source or Path(source).suffix.lower() not in IMAGE_EXTENSIONS:
            return False
        text = msg.strip().casefold()
        # Requests for advice, writing or explanation are not image-edit actions.
        if re.match(r'^(как\b|почему\b|зачем\b|что\b|можно ли\b|объясни\b|расскажи\b|напиши\b|составь\b|придумай\b|дай совет\b|подскажи\b|инструкция\b)', text):
            return False
        verbs = (r'замени(?:ть)?|поменяй|поменять|измени(?:ть)?|перенеси|перенести|'
                 r'убери|убрать|удали(?:ть)?|добавь|добавить|нарисуй|дорисуй|'
                 r'улучши(?:ть)?|обработай|обработать|отретушируй|ретушируй|'
                 r'поверни|повернуть|разверни|развернуть|сотри|сотрите|'
                 r'вырежи|вырезать|замаскируй|скрой|скрыть|осветли|затемни|'
                 r'перекрась|перекрасить|сделай|сделать|стилизуй|стилизовать|'
                 r'восстанови|восстановить|скорректируй|скорректировать|'
                 r'сгенерируй|сгенерировать|создай|создать|поставь|помести|'
                 r'перемести|переместить|убрать|приблизь|отдали|отрази')
        return bool(re.search(r'(?<![\w])(?:'+verbs+r')(?![\w])',text))

    def dispatch_image_edit(self, instruction):
        if not self.checked_source():
            self.append_chat('Система', 'Выберите допустимый исходник, отличный от эталона.')
            return
        if not self.source.path or Path(self.source.path).suffix.lower() not in IMAGE_EXTENSIONS:
            self.append_chat('AI-менеджер','Выберите исходную фотографию для редактирования.')
            return
        if getattr(self, 'engine_mode', 0) == 1:
            self.cloud_generate(instruction)
        elif getattr(self, 'engine_mode', 0) == 2:
            self.hf_generate(instruction)
        else:
            self.modern_local_generate(instruction)

    def route_image_command(self, msg):
        if not self.is_direct_image_edit(msg):
            return False
        self.dispatch_image_edit(msg)
        return True

    def handle_project_control(self, msg):
        """Local destructive commands: preview, backup, explicit confirmation, verify."""
        # A local chat command must not commit SQLite work owned by another
        # callback. Leave the composer and the pending deletion untouched.
        if self.db.in_transaction:
            self.append_chat('Система',
                'Команда не принята: незавершённая транзакция SQLite. Повторите после её завершения.')
            return True
        from project_control import parse_command, choose_documents
        command = parse_command(msg)
        pending = getattr(self, '_pending_project_delete', None)
        reply = msg.casefold().strip().strip('.! ')
        if pending and reply in ('отмена', 'отмени', 'не надо', 'нет, отмена'):
            self._pending_project_delete = None
            self.core.record_local_command(self.current_project,msg,timestamp=now())
            self.append_chat('Вы',msg)
            self.append_chat('AI-менеджер','Операция отменена. Ничего не удалено.')
            return True
        if command is None and pending:
            # Follow-up is resolved against the pending target, not parsed as an unrelated task.
            if reply in ('он один','она одна','один','одна','да, этот','этот','его','её','ее','да','последний','первый','второй'):
                command = dict(pending)
                if reply in ('последний','первый','второй'):
                    command['query'] = reply
            elif reply.startswith(('называется ','название ')):
                command = dict(pending)
                command['query'] = reply

        if command is None:
            return False
        from project_operation_guard import project_operation_busy
        if project_operation_busy(self):
            self.append_chat('Система',
                'Удаление заблокировано: сначала завершите или остановите активные операции.')
            return True
        # Log every user command, including intercepted local operations.
        self.core.record_local_command(self.current_project,msg,timestamp=now())
        self.append_chat('Вы',msg)

        pid = self.current_project
        scope = command['scope']
        if scope == 'documents':
            kind = command['kind']
            docs=self.core.data.documents(pid,kind=kind)
            rows=sorted(((ident,doc_kind,title,created) for ident,title,doc_kind,created in docs),key=lambda r:(r[3],r[0]))
            selected = choose_documents(rows,command['query'])
            # Resolve 'this document' against the currently opened editor, not another section.
            if not selected and any(x in command['query'] for x in ('этот','эту','открыт','выбран')):
                active = getattr(self,'active_document_id',None)
                selected = [r for r in rows if r[0] == active]

            if not selected:
                self._pending_project_delete = command
                self.append_chat('AI-менеджер', 'Не могу однозначно определить документы. Укажите точное название, номер в списке или «все стратегии / все контент-планы». Ничего не удалено.')
                return True
            self._pending_project_delete = None
            description = '\n'.join('• '+r[2] for r in selected)
        elif scope == 'project':
            counts = self.core.data.deletion_counts(pid,'project')
            description = '\n'.join(f'{name}: {value}' for name,value in counts) + '\nВсе файлы в папке проекта и настройки проекта'
        elif scope == 'assets':
            count = self.core.data.deletion_counts(pid,'assets')[0][1]
            description = f'Импортированные исходники: {count} (результаты и документы сохраняются)'
        else:
            count = self.core.data.deletion_counts(pid,'chat')[0][1]
            description = f'Сообщения чата: {count}'
        answer = QMessageBox.question(self, 'Подтверждение удаления',
            'Будут удалены данные ТОЛЬКО текущего проекта:\n\n'+description+
            '\n\nПеред удалением будет создана резервная копия. Продолжить?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            self._pending_project_delete = None
            self.append_chat('Система','Удаление отменено. Данные не изменены.')
            return True
        try:
            receipt=self.core.controls.backup_and_delete(
                pid, scope, selected_ids=tuple(r[0] for r in selected)
                    if scope=='documents' else ())
            if scope in ('project','documents'):
                self.active_document_id=None
            if scope=='project' and hasattr(self,'_pending_ai_tasks'):
                self._pending_ai_tasks.pop(pid,None)
            self.current_project=None
            self.active_document_id=None
            self.refresh_projects(None if scope=='project' else pid)
            if scope in ('project','chat'):
                self.chat.clear()
            if scope=='project':
                self._pending_project_delete=None
            self.append_chat('AI-менеджер',
                'Удаление выполнено и проверено. Резервная копия: '+str(receipt.backup))
        except Exception as exc:
            QMessageBox.critical(self,'Ошибка удаления',
                'Операция не завершена: '+str(exc)+'\nПроверьте данные и резервную копию.')
            self.append_chat('Система','Удаление не подтверждено: '+str(exc))
        return True

    def execute_project_control(self,scope,target_ids):
        """Qt confirmation only; the application core owns backup and deletion."""
        from project_operation_guard import project_operation_busy
        if project_operation_busy(self):
            return {'status':'blocked','completion_verified':False,
                'reply':'Сначала завершите или остановите активные операции проекта.'}
        pid=self.current_project
        try:
            preview=self.core.controls.preview(pid,scope,selected_ids=tuple(target_ids))
        except (ValueError,RuntimeError,sqlite3.Error) as exc:
            return {'status':'failed','completion_verified':False,
                    'error':str(exc),'reply':'Операция не принята: '+str(exc)}
        description='\n'.join('• '+str(row) for row in preview)
        answer=QMessageBox.question(self,'Подтверждение удаления',
            'Будут удалены данные ТОЛЬКО текущего проекта:\n\n'+description+
            '\n\nПеред удалением будет создана резервная копия. Продолжить?',
            QMessageBox.Yes | QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:
            self._pending_project_delete=None
            return {'status':'cancelled','completion_verified':False,
                    'reply':'Удаление отменено. Данные не изменены.'}
        try:
            receipt=self.core.controls.backup_and_delete(pid,scope,selected_ids=tuple(target_ids))
            if scope in ('project','documents'):self.active_document_id=None
            if scope=='project' and hasattr(self,'_pending_ai_tasks'):
                self._pending_ai_tasks.pop(pid,None)
            self.current_project=None
            self.active_document_id=None
            self.refresh_projects(None if scope=='project' else pid)
            if scope in ('project','chat'):self.chat.clear()
            self._pending_project_delete=None
            return {'status':'applied','completion_verified':True,'backup':str(receipt.backup),
                    'backup_sha256':receipt.backup_sha256,
                    'reply':'Удаление выполнено и проверено. Резервная копия: '+str(receipt.backup)}
        except Exception as exc:
            QMessageBox.critical(self,'Ошибка удаления','Операция не завершена: '+str(exc))
            return {'status':'failed','completion_verified':False,
                    'error':str(exc),'reply':'Операция не завершена: '+str(exc)}

    def revise_saved_ai_task(self):
        if not self.current_project or not self.active_document_id:return
        if self.task_worker is not None or self.video_worker is not None:return
        # A UI button supplies context, not an alternative lexical dispatcher.
        unsent=self.message.toPlainText()
        self.message.setPlainText('Хочу доработать выбранный документ и связанные с ним материалы по исходному заданию.')
        self.send_message()
        if not self.message.toPlainText():self.message.setPlainText(unsent)

    def start_dictation(self):
        self.message.setFocus()
        try:
            from voice_input import start_windows_dictation
            start_windows_dictation()
            self.record_project_diagnostic('dictation_requested',{'target':'dialogue_composer'})
        except Exception as exc:self.append_chat('Система',str(exc))

    def _track_background(self,worker):
        self._background_workers.append(worker)
        def finished():
            if worker in self._background_workers:self._background_workers.remove(worker)
            worker.deleteLater()
        worker.finished.connect(finished)

    def send_message(self):
        if not self.current_project:
            self.append_chat('Система','Сначала выберите проект.');return
        msg=self.message.toPlainText().strip()
        if not msg:return
        from project_operation_guard import project_operation_busy
        if project_operation_busy(self):
            # Do not clear the composer or insert a message that has not been
            # accepted for execution. User can submit it after the worker ends.
            self.ai_status.setText('Дождитесь завершения текущей операции')
            return
        if self.db.in_transaction:
            self.ai_status.setText('Новое задание заблокировано: незавершённая транзакция SQLite')
            return
        if self.save_document_if_changed() is False:
            self.ai_status.setText('Сохранение документа не подтверждено; новое задание не запущено')
            return
        # Qt is an adapter: the application core atomically owns both the
        # submitted message and the durable intake receipt.
        project_id=self.current_project
        turn_id=uuid4().hex
        try:
            accepted=self.core.conversations.accept(project_id,msg,turn_id=turn_id,timestamp=now())
            inserted_message_id=accepted.message_id
        except Exception as exc:
            self.ai_status.setText('Не удалось сохранить новое задание')
            self.append_chat('Система','Исходное поручение не принято: '+str(exc))
            return
        self.append_chat('Вы',msg);self.message.clear()
        self.task_project_id=self.current_project
        self.task_instruction=msg
        self.task_revision_id=None
        self.task_semantic_kind=None
        self.task_execution_claim=None
        self.task_dialogue_turn_id=None
        try:
            snapshot=self.core.conversations.snapshot(project_id,selected_id=self.active_document_id,
                current_message_id=inserted_message_id,
                ui_context={'section':self.active_section,'source_path':str(self.source.path) if self.source.path else None,
                    'reference_path':str(self.reference.path) if self.reference.path else None,'capabilities':available_tools()})
        except (ValueError,TypeError,KeyError,sqlite3.Error,RuntimeError) as exc:
            self.task_failed('Не удалось прочитать состояние проекта: '+str(exc));return
        self._resolved_dialogue=None;self._dialogue_error=None
        self.task_dialogue_turn_id=turn_id
        worker=DialogueWorker(msg,snapshot,str(self.core.workspace(self.current_project)),self.task_dialogue_turn_id)
        self.dialogue_worker=worker;self.task_worker=worker
        worker.done.connect(self._dialogue_resolved)
        worker.failed.connect(self._dialogue_failed)
        worker.progress.connect(self.task_progress)
        worker.finished.connect(self._dialogue_finished)
        self._track_background(worker)
        self.send_button.setEnabled(False);self.ai_status.setText('AI понимает сообщение…')
        self._task_started=time.monotonic();self._task_progress_text='AI понимает сообщение'
        if not hasattr(self,'_task_clock'):
            self._task_clock=QTimer(self);self._task_clock.timeout.connect(self.update_task_clock)
        self._task_clock.start(1000)
        worker.start()

    def _dialogue_resolved(self,plan):self._resolved_dialogue=plan

    def _dialogue_failed(self,error):self._dialogue_error=error

    def _dialogue_reply(self,text,*,complete_turn=False):
        project_id=getattr(self,'task_project_id',None)
        if not project_id or project_id != self.current_project:
            raise ValueError('Диалог не принадлежит выбранному проекту')
        if complete_turn:
            turn_id=getattr(self,'task_dialogue_turn_id',None)
            if not turn_id:
                raise ValueError('Dialogue completion requires original turn identity')
            self.core.conversations.publish_reply(project_id,turn_id,text,timestamp=now())
        else:
            self.core.conversations.reply(project_id,text,timestamp=now())
        self.append_chat('AI-менеджер',text)

    def _record_dialogue_projection(self,workspace,turn_id,**state):
        # A diagnostic JSON must not undo a confirmed SQLite user action.
        try:
            from dialogue_runtime import save_turn
            save_turn(workspace,turn_id,**state)
        except (OSError,ValueError,TypeError) as exc:
            self.record_project_diagnostic('dialogue_projection_deferred',{'error':str(exc)},
                getattr(self,'task_project_id',None))

    def _dialogue_finished(self):
        self._dialogue_applying=True
        try:
            return self._apply_dialogue_finished()
        finally:
            self._dialogue_applying=False

    def _apply_dialogue_finished(self):
        # Never let a dialogue callback commit or roll back another operation.
        # This guard precedes the state transition, widget updates and dispatch.
        if self.db.in_transaction:
            self.task_failed('SQLite: незавершённая транзакция другого действия')
            return
        plan=self._resolved_dialogue;error=self._dialogue_error
        from task_intake import update_intake
        intake_pid=getattr(self,'task_project_id',None)
        intake_turn=getattr(self,'task_dialogue_turn_id',None)
        if intake_pid and intake_pid != self.current_project:
            # Do not mutate intake or messages in the wrong project when a
            # completed dialogue arrives after the selection changed.
            self.dialogue_worker=None
            self.task_worker=None
            self.task_failed('Проект диалога изменился; результат не применён')
            return
        if intake_pid and intake_turn:
            try:
                # A model plan is not a completed user action. Keep its durable
                # receipt recoverable while the plan is applied by the host.
                self.core.conversations.transition(intake_pid,intake_turn,
                    'failed' if error or plan is None else 'resolving')
            except (ValueError,sqlite3.Error,RuntimeError) as exc:
                self._dialogue_error='Не удалось подтвердить состояние задания: '+str(exc)
                error=self._dialogue_error
        self.dialogue_worker=None;self.task_worker=None
        if hasattr(self,'_task_clock'):self._task_clock.stop()
        self.send_button.setEnabled(True)
        if error:
            self.task_failed('Не удалось понять сообщение: '+error);return
        if plan is None:
            self.task_failed('AI не вернул решение диалога');return
        workspace=self.core.workspace(plan.project_id)
        try:
            from dialogue_execution import validate_live_plan,canonical_request,compile_execution
            validate_live_plan(self.db,self.current_project,plan)
            a=plan.action;op=a['operation']
            if op in ('respond','clarify'):
                clarification=(canonical_request(plan),json.dumps(a,ensure_ascii=False)) if op=='clarify' else None
                # User reply, clarification registry and intake completion have
                # exactly one SQLite owner; never publish a partial outcome.
                self.core.conversations.publish_reply(plan.project_id,plan.turn_id,a['reply'],
                    timestamp=now(),clarification=clarification)
                self.append_chat('AI-менеджер',a['reply'])
                self.ai_status.setText('Нужно уточнение' if op=='clarify' else 'Готов')
                self._record_dialogue_projection(workspace,plan.turn_id,
                    status='question' if op=='clarify' else 'responded',side_effects=False)
                return
            if op=='cancel_pending':
                from dialogue_execution import cancel_pending
                result=cancel_pending(self.db,plan)
                if hasattr(self,'_pending_ai_tasks'):self._pending_ai_tasks.pop(self.current_project,None)
                self.record_project_diagnostic('pending_task_cancelled',result)
                self._dialogue_reply('Незавершённое задание отменено. Исходное поручение, контрольные точки и черновики сохранены; документы проекта остаются на месте.',complete_turn=True)
            elif op=='open_document':
                ident=a['target_ids'][0];kind=plan.snapshot['documents'][ident]['kind']
                self.show_section(kind);self.document_list.setCurrentRow(self.document_ids.index(ident))
                self.document_views.setCurrentWidget(self.document_preview)
                self._dialogue_reply('Открыт документ «'+plan.snapshot['documents'][ident]['title']+'».',complete_turn=True)
            elif op=='rename_document':
                ident=a['target_ids'][0]
                row=self.core.data.document(self.current_project,ident)
                if not row or not self.save_manager_document(doc_id_override=ident,
                        kind_override=row[0], title_override=a['new_title'], content_override=row[2]):
                    raise RuntimeError('Переименование документа и экспорт не подтверждены')
                self.show_section(row[0])
                self._dialogue_reply('Документ переименован: «'+a['new_title']+'».',complete_turn=True)
            elif op=='project_control':
                result=self.execute_project_control(a['control_scope'],a['target_ids'])
                if not isinstance(result,dict) or result.get('status') not in ('applied','cancelled','failed'):
                    raise ValueError('Операция проекта не вернула подтверждённый результат')
                status=result['status']
                self._dialogue_reply(result.get('reply') or 'Результат операции: '+status,complete_turn=(status=='applied'))
                self.ai_status.setText({'applied':'Готов','cancelled':'Операция отменена','failed':'Ошибка операции'}[status])
                self._record_dialogue_projection(workspace,plan.turn_id,status=status,operation_result=result,
                    completion_verified=result.get('completion_verified') is True)
                if status == 'applied':
                    self._finish_dialogue_intake(plan.project_id,plan.turn_id)
                else:
                    self._fail_dialogue_intake(plan.project_id,plan.turn_id)
                return
            elif op in ('image_edit','video_trim'):
                if str(self.source.path or '')!=str(plan.snapshot['ui_context'].get('source_path') or ''):
                    raise ValueError('Исходный файл изменился во время анализа')
                if str(self.reference.path or '')!=str(plan.snapshot['ui_context'].get('reference_path') or ''):
                    raise ValueError('Эталон изменился во время анализа')
                if a['use_reference'] and not self.reference.path:raise ValueError('AI выбрал эталон, но файл не задан')
                if op=='image_edit':
                    self.dispatch_semantic_image(plan.utterance,a['use_reference'])
                else:self._apply_task_result({'action':'video_trim','instruction':plan.utterance,'start':a['start'],'end':a['end']})
                media=self.cloud_worker or self.modern_worker if op=='image_edit' else self.video_worker
                if media is not None:
                    self._semantic_media_project=plan.project_id
                    self._semantic_media_identity=(plan.project_id,plan.turn_id,op)
                    self.projects.setEnabled(False)
                    media.finished.connect(self._semantic_media_finished)
                self._record_dialogue_projection(workspace,plan.turn_id,status='tool_dispatched' if media is not None else 'tool_not_started',completion_verified=False)
                if media is None:
                    self._fail_dialogue_intake(plan.project_id,plan.turn_id)
                # A dispatched tool is not an accepted artifact. The receipt
                # remains resolving until the registry commits its real output.
                return
            else:
                request,context=compile_execution(self.db,plan,workspace)
                context['capabilities']=available_tools()
                self.task_execution_claim, execution_receipt = self.core.start_dialogue_execution(
                    plan,request,context)
                self.task_attempt=execution_receipt.attempt
                if hasattr(self,'_pending_ai_tasks'):self._pending_ai_tasks.pop(plan.project_id,None)
                self.task_instruction=request;self.task_revision_id=context.get('revision_id')
                self.task_semantic_kind=context['semantic_execution_kind']
                # Files are projections of a committed task, never evidence that
                # an attempted worker launch succeeded.
                from durable_json import atomic_write_json
                if context.get('revision_id'):
                    atomic_write_json(workspace/'drafts'/('revision_input_'+context['revision_id']+'.json'),context['revision_seed'])
                self.record_task_lifecycle('started',request=request,source_message=plan.utterance,
                    task_mode=context['task_mode'],workflow=self.task_semantic_kind,dialogue_turn_id=plan.turn_id,
                    revision_id=self.task_revision_id,completion_verified=False)
                self._record_dialogue_projection(workspace,plan.turn_id,status='execution_started',request=request,
                    execution_kind=self.task_semantic_kind,revision_id=self.task_revision_id)
                worker=TaskWorker(plan.utterance,context);self.task_worker=worker
                issued=self.task_attempt
                worker.done.connect(lambda result,attempt=issued:self.task_ready_for_attempt(result,attempt))
                worker.failed.connect(lambda error,attempt=issued:self.task_failed_for_attempt(error,attempt))
                worker.progress.connect(self.task_progress)
                self._track_background(worker)
                self.send_button.setEnabled(False);self._task_progress_text='Выполняю поручение';self._task_clock.start(1000)
                # Worker start does not prove task completion. The task
                # lifecycle closes the dialogue only in its terminal commit.
                worker.start()
                return
            self.ai_status.setText('Готов')
            self._record_dialogue_projection(workspace,plan.turn_id,status='applied',completion_verified=True)
            self._finish_dialogue_intake(plan.project_id,plan.turn_id)
        except Exception as exc:
            import traceback
            self._fail_dialogue_intake(plan.project_id,plan.turn_id)
            try:
                self._record_dialogue_projection(workspace,plan.turn_id,status='application_failed',error=str(exc),traceback=traceback.format_exc())
            except (OSError,ValueError):
                pass
            self.task_failed(str(exc))

    def _finish_dialogue_intake(self, project_id, turn_id):
        """Close only the exact applied turn, never the active UI selection."""
        if not project_id or not turn_id:
            raise ValueError('Missing dialogue identity')
        self.core.conversations.transition(project_id,turn_id,'resolved')

    def _fail_dialogue_intake(self, project_id, turn_id):
        if project_id and turn_id:
            try:
                self.core.conversations.transition(project_id,turn_id,'failed')
            except (ValueError,sqlite3.Error,RuntimeError):
                # Preserve an unresolved receipt for manual recovery if SQLite
                # cannot persist the error. Never silently mark it successful.
                pass

    def _semantic_media_publication(self, project_id, kind):
        '''Bind a media result to the original dialogue, not current Qt selection.'''
        identity=getattr(self,'_semantic_media_identity',None)
        if not identity or identity[0]!=project_id:
            return {}
        expected='image_edit' if kind=='image' else 'video_trim'
        if identity[2]!=expected:
            raise ValueError('Media result kind differs from the original operation')
        return {'operation_id':'dialogue:'+identity[0]+':'+identity[1]+':'+kind,
                'dialogue_turn_id':identity[1]}

    def _semantic_media_accepted(self, project_id, kind):
        identity=getattr(self,'_semantic_media_identity',None)
        if not identity or identity[0]!=project_id:
            return
        self._semantic_media_identity=None
        try:
            from dialogue_runtime import save_turn
            save_turn(self.core.workspace(project_id),identity[1],status='applied',
                      operation=kind,completion_verified=True)
        except (OSError,ValueError,TypeError) as exc:
            self.record_project_diagnostic('dialogue_media_projection_deferred',{'error':str(exc)},project_id)

    def _semantic_media_finished(self):
        # Without an accepted result the durable intake must remain recoverable.
        # Do not keep a stale in-memory turn authority after a worker exits.
        identity=getattr(self,'_semantic_media_identity',None)
        if identity is not None:
            try:
                from dialogue_runtime import save_turn
                save_turn(self.core.workspace(identity[0]),identity[1],status='tool_incomplete',completion_verified=False)
            except (OSError,ValueError,TypeError):
                pass  # SQLite remains authoritative; replay will find the turn.
        self._semantic_media_identity=None
        self._semantic_media_project=None
        self.projects.setEnabled(True)

    def dispatch_semantic_image(self,instruction,use_reference):
        if not self.checked_source():raise ValueError('Выберите допустимый исходник')
        if getattr(self,'engine_mode',0)==1:self.cloud_generate(instruction,use_reference=use_reference)
        elif getattr(self,'engine_mode',0)==2:self.hf_generate(instruction,use_reference=use_reference)
        else:self.modern_local_generate(instruction,use_reference=use_reference)

    def record_project_diagnostic(self,event,details=None,project_id=None):
        try:
            from diagnostic_report import append_project_event
            pid=project_id or self.current_project
            if pid:append_project_event(self.core.workspace(pid),event,details or {})
        except Exception:pass  # Telemetry cannot alter task completion.

    def export_support_report(self):
        if not self.current_project:
            self.statusBar().showMessage('Выберите проект для отчёта.',6000);return
        if self.report_worker is not None:return
        default=Path.home()/'Desktop'
        if not default.is_dir():default=ROOT/'support_reports'
        default.mkdir(parents=True,exist_ok=True)
        name='AI_SMM_Report_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+self.current_project[:8]+'.zip'
        target,_=QFileDialog.getSaveFileName(self,'Сохранить отчёт для анализа',str(default/name),'ZIP-отчёт (*.zip)')
        if not target:return
        if not target.casefold().endswith('.zip'):target+='.zip'
        try:
            from diagnostic_report import snapshot_project
            state={'ai_status':self.ai_status.text(),'task_running':self.task_worker is not None,
                'task_instruction':getattr(self,'task_instruction','') if getattr(self,'task_project_id',None)==self.current_project else '',
                'active_section':self.active_section,'technical_log':self.technical_log.toPlainText(),
                'model':MODEL,'selected_document_id':self.active_document_id}
            if self.active_document_id and self.core.data.document_exists(self.current_project,self.active_document_id):
                state['editor_content']=self.document_editor.toPlainText()
            snapshot=snapshot_project(self.db,self.current_project,state)
            self.report_worker=DiagnosticReportWorker(snapshot,str(self.core.workspace(self.current_project)),target)
            self.report_worker.done.connect(self.support_report_ready)
            self.report_worker.failed.connect(self.support_report_failed)
            self.report_worker.finished.connect(self.support_report_finished)
            self.report_button.setText('Сборка отчёта…');self.report_button.setEnabled(False)
            self.report_worker.start()
        except Exception as exc:self.support_report_failed(str(exc));self.support_report_finished()

    def support_report_ready(self,result):
        self.last_report_path=result['path']
        self.statusBar().showMessage('Отчёт собран: '+result['path'],15000)
        if result['project_id']==self.current_project:
            self.append_chat('AI-менеджер','Отчёт для анализа собран. Отправьте один файл: '+result['path']+
                (f"\nВ отчёте отмечены недоступные или изменявшиеся данные: {result['warnings']}." if result['warnings'] else ''))

    def support_report_failed(self,error):
        self.statusBar().showMessage('Не удалось собрать отчёт: '+str(error),15000)
        self.append_chat('Система','Не удалось собрать отчёт: '+str(error))

    def support_report_finished(self):
        self.report_worker=None
        self.report_button.setText('Собрать отчёт');self.report_button.setEnabled(True)

    def closeEvent(self,event):
        from project_operation_guard import project_operation_busy
        from qt_worker_lifecycle import active_workers
        # QThread.done can precede QThread.finished. The active task pointer
        # may already be clear while the native thread is still unwinding.
        if active_workers(self):
            self.statusBar().showMessage('Завершаются фоновые операции. Закрытие пока заблокировано.',6000)
            event.ignore();return
        if project_operation_busy(self):
            self.statusBar().showMessage('AI ещё работает. Дождитесь завершения перед закрытием.',6000)
            event.ignore();return
        if self.report_worker is not None and self.report_worker.isRunning():
            self.statusBar().showMessage('Собирается отчёт. Дождитесь завершения перед закрытием.',6000)
            event.ignore();return
        try:
            self.core.close()
        except (RuntimeError, sqlite3.Error) as exc:
            self.statusBar().showMessage('Не удалось завершить хранение: '+str(exc),6000)
            event.ignore();return
        super().closeEvent(event)

    def set_pending_task(self,request,question):
        # This is only a Qt adapter: the application service owns the mutation.
        pid=getattr(self,'task_project_id',None)
        if not pid or pid!=self.current_project:
            raise RuntimeError('Pending task ownership changed during result publication')
        return self.core.outcomes.park_in_transaction(pid,request,question,
            revision_id=getattr(self,'task_revision_id',None))

    def update_task_clock(self):
        if self.task_worker is None:
            self._task_clock.stop()
            return
        elapsed=int(time.monotonic()-getattr(self,'_task_started',time.monotonic()))
        self.ai_status.setText(f"{getattr(self,'_task_progress_text','AI работает')} · {elapsed//60:02d}:{elapsed%60:02d}")

    def task_progress(self, info):
        self.record_project_diagnostic('task_progress',info,getattr(self,'task_project_id',None))
        if info.get('stage')=='substance_reader_started':
            self._task_progress_text='Другая модель оценивает содержание комплекта'
        if info.get('stage')=='ollama_call' and 'редакторская проверка' in str(info.get('stage_name','')):
            self._task_progress_text='Проверяю готовый текст по исходному заданию'
        elif info.get('stage')=='ollama_call' and 'доработка текста' in str(info.get('stage_name','')):
            self._task_progress_text='Исправляю замечания к тексту'
        elif info.get('stage')=='week_done':
            self._task_progress_text=f"Контент-план: {info.get('completed',0)}/{info.get('total',39)} · неделя {info.get('week','?')}"
        elif info.get('stage')=='batch_attempt':
            self._task_progress_text=f"Проверка недели {info.get('week','?')}, попытка {info.get('attempt','?')}"
        elif info.get('stage')=='targeted_repair_started':
            self._task_progress_text=f"Адресная доработка {info.get('publication','?')}, попытка {info.get('attempt','?')}"
        elif info.get('stage')=='single_accepted':
            self._task_progress_text=f"Принято: {info.get('completed',0)}/{info.get('total',0)} публикаций"
        elif info.get('stage')=='topic_week_accepted':
            self._task_progress_text=f"Планирование: {info.get('planned',0)}/{info.get('total',39)} тем"
        elif info.get('stage')=='topic_planning_degraded':
            self._task_progress_text='Восстановление: создаю публикации по готовым неделям'
        self.update_task_clock()

    def record_task_lifecycle(self,stage,**details):
        deferred=getattr(self,'_result_effects',None)
        if deferred is not None:
            import copy
            values=copy.deepcopy(details)
            deferred.append(lambda:self.record_task_lifecycle(stage,**values))
            return
        # Diagnostics do not grant acceptance. Canonical pending state is in SQLite.
        pid=getattr(self,'task_project_id',None)
        request=details.get('request',getattr(self,'task_instruction',''))
        if not pid or not isinstance(request,str) or not request.strip():
            return
        try:
            from durable_json import atomic_write_json
            signature=hashlib.sha256(request.encode('utf-8')).hexdigest()
            from task_checkpoint import checkpoint_path
            claim=getattr(self,'task_execution_claim',None)
            claim_source=(claim.snapshot.get('sources',{}).get('pending') if claim is not None
                          and getattr(self,'task_project_id',None)==claim.project_id==pid else None)
            active_id=(claim_source.get('task_id') if claim_source
                       and claim_source.get('request')==request else None)
            path=checkpoint_path(self.core.workspace(pid),'task_lifecycle',request,active_id)
            previous={}
            if path.is_file():
                value=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(value,dict) and value.get('request')==request:
                    previous=value
            self.record_project_diagnostic('task_lifecycle',{'stage':stage,'request':request,**details},pid)
            if getattr(self,'task_dialogue_turn_id',None):
                from dialogue_runtime import save_turn
                save_turn(self.core.workspace(pid),self.task_dialogue_turn_id,**{'status':stage,'request':request,**details})
            atomic_write_json(path,{**previous,'schema_version':1,'project_id':pid,
                'request':request,'request_sha256':signature,'stage':stage,
                'updated_at':datetime.now(timezone.utc).isoformat(),**details})
        except (OSError,ValueError,TypeError,sqlite3.Error) as exc:
            self.append_chat('Система','Не удалось сохранить диагностику задания: '+str(exc)[:220])

    def task_ready_for_attempt(self, task, attempt):
        if getattr(self,'task_attempt',None)!=attempt:
            return  # Late callback from a superseded execution; no mutation.
        from execution_runtime import ExecutionReceipt
        try:
            self.core.executions.authorize_result(
                ExecutionReceipt(attempt.project_id,attempt.task_id,attempt),
                active_project_id=self.current_project)
        except (ValueError, sqlite3.Error):
            return  # Worker lost authority; cannot apply files/UI/DB side effects.
        self.task_ready(task)

    def task_failed_for_attempt(self, error, attempt):
        if getattr(self,'task_attempt',None)!=attempt:
            return
        self.task_failed(error)

    def task_ready(self, task):
        try:
            self._apply_task_result(task)
        except Exception as exc:
            import traceback
            self.record_project_diagnostic('result_application_exception',{'traceback':traceback.format_exc()},getattr(self,'task_project_id',None))
            self.task_failed('Не удалось применить или сохранить результат: '+str(exc))

    def _after_result_commit(self, callback):
        deferred=getattr(self,'_result_effects',None)
        if deferred is None:callback()
        else:deferred.append(callback)

    def _apply_task_result(self, task):
        # Central transaction and ownership contract lives outside Qt's window.
        from result_application import apply_project_result
        return apply_project_result(self, task)

    def _apply_task_result_body(self, task):
        if hasattr(self,'_task_clock'): self._task_clock.stop()
        self.task_worker=None
        self.send_button.setEnabled(True)
        if getattr(self, 'task_project_id', None) != self.current_project:
            self.append_chat('Система', 'Ответ AI относится к другому проекту и не применён. Повторите задание в нужном проекте.')
            return
        semantic=bool(getattr(self,'task_semantic_kind',None))
        action=task['action']; instruction=task['instruction']
        self.record_task_lifecycle('result_received',returned_action=action,completion_verified=False)
        if action=='write':
            quality=task.get('quality_review')
            accepted=(isinstance(quality,dict) and quality.get('status')=='PASS' and quality.get('pass') is True)
            if semantic:
                from completion_gate import standalone_review_matches
                accepted=accepted and standalone_review_matches(quality,instruction,task.get('answer'))
            if not accepted:
                task['quality_review']={**(quality if isinstance(quality,dict) else {}),
                    'status':'BLOCKED','pass':False,
                    'reason':'Нет полной редакторской проверки текущего текста по исходному заданию.'}
                action='write_blocked'
        if action == 'write_blocked':
            self.ai_status.setText('Нужна доработка')
            self.record_task_lifecycle('pending_review',completion_verified=False)
            import hashlib
            from durable_json import atomic_write_json
            draft=str(task.get('answer') or '')
            review=task.get('quality_review') if isinstance(task.get('quality_review'),dict) else {}
            issues=review.get('issues') if isinstance(review.get('issues'),list) else []
            from task_checkpoint import checkpoint_path
            claim=getattr(self,'task_execution_claim',None)
            task_id=(claim.snapshot['sources']['pending']['task_id']
                     if semantic and claim is not None else None)
            checkpoint=checkpoint_path(self.core.workspace(self.current_project),'pending_text',instruction,task_id)
            try:
                atomic_write_json(checkpoint,{'request':instruction,'request_sha256':hashlib.sha256(instruction.encode('utf-8')).hexdigest(),
                    'task_id':task_id,'draft':draft,
                    'content_contract':task.get('content_contract'),
                    'issues':issues,'quality_review':review,
                    'updated_at':datetime.now(timezone.utc).isoformat()})
            except OSError as exc:
                self.ai_status.setText('Ошибка сохранения')
                self.record_task_lifecycle('failed',completion_verified=False,error=str(exc))
                self.append_chat('AI-менеджер','Черновик не принят редакторской проверкой, а контрольную копию не удалось сохранить: '+str(exc)[:300])
                return
            if not hasattr(self,'_pending_ai_tasks'):
                self._pending_ai_tasks={}
            question='Черновик не принят редакторской проверкой. Напишите «продолжи задание» для адресной доработки.'
            reasons='\n'.join('- '+str(item.get('reason') or 'Замечание без описания')
                              for item in issues if isinstance(item,dict))
            if not reasons:
                reasons=str(review.get('reason') or 'Редакторская проверка не завершилась.')
            answer=('Черновик не сохранён как готовый документ: после одной адресной доработки '
                    'он не прошёл проверку. Контрольная копия сохранена для продолжения.\n\n'+reasons+
                    '\n\nНапишите «продолжи задание», чтобы повторно обработать этот черновик.')
            self.core.outcomes.park_and_reply_in_transaction(self.current_project,instruction,question,answer,
                revision_id=getattr(self,'task_revision_id',None),timestamp=now())
            self._pending_ai_tasks[self.current_project]={'request':instruction,'question':question}
            self.append_chat('AI-менеджер',answer)
            return
        if action == 'compound_clarify':
            self.ai_status.setText('Нужно уточнение')
            self.record_task_lifecycle('pending_clarification',completion_verified=False)
            answer=str(task.get('answer') or 'Уточните сведения о проекте.')
            if not hasattr(self, '_pending_ai_tasks'):
                self._pending_ai_tasks = {}
            self.core.outcomes.park_and_reply_in_transaction(self.current_project,instruction,answer,answer,
                revision_id=getattr(self,'task_revision_id',None),timestamp=now())
            self._pending_ai_tasks[self.current_project] = {'request':instruction,'question':answer}
            self.append_chat('AI-менеджер',answer)
            return
        if action == 'compound_documents':
            from stage57_core import receipt, editorial_warnings
            documents=task.get('documents') or []
            expected_parameters=None
            claim=getattr(self,'task_execution_claim',None)
            if semantic and claim is not None:
                bound=claim.snapshot['sources']['pending']
                expected_parameters={'kind':bound.get('execution_kind'),'schedule':bound.get('schedule'),
                                     'requested_media':bound.get('requested_media')}
                from completion_gate import delivery_matches_parameters,calendar_matches_parameters,bundle_review_matches
                report=task.get('content_delivery') or {}
                mismatch=not delivery_matches_parameters(report,expected_parameters)
                # An unchanged count is not proof of ownership. The GUI uses
                # the SQLite task claim, never task identity supplied by AI.
                from workflow_authority import ExecutionContract
                owner_conflict=False
                bound_task_id=bound.get('task_id')
                if bound_task_id:
                    schedule=expected_parameters.get('schedule') or {}
                    try:
                        execution_authority=ExecutionContract.create(bound_task_id,instruction,
                            schedule.get('weeks'),schedule.get('per_week'))
                        owner_conflict=not execution_authority.matches(report.get('execution_contract'))
                        mismatch=mismatch or owner_conflict
                    except (ValueError,TypeError):
                        mismatch=True
                        owner_conflict=True
                artifact_items=[item for item in documents if isinstance(item,dict) and item.get('kind') in ('strategy','calendar')]
                artifacts={item['kind']:item.get('content') for item in artifact_items}
                duplicate_artifacts=len(documents)!=2 or len(artifact_items)!=2 or len(artifacts)!=2
                mismatch=mismatch or duplicate_artifacts or not calendar_matches_parameters(artifacts.get('calendar'),expected_parameters)
                missing_review=not bundle_review_matches(report.get('independent_editor'),instruction,artifacts)
                if mismatch or missing_review:
                    declared_scope=report.get('scope')
                    scope_conflict=isinstance(declared_scope,dict) and not delivery_matches_parameters(report,expected_parameters)
                    reason=('Заявленный объём результата не соответствует закреплённым параметрам исходного задания.' if scope_conflict else
                            'Содержимое календаря не соответствует закреплённому объёму или обязательным полям.' if 'calendar' in artifacts and mismatch else
                            'Редакторские заключения не подтверждают текущие версии документов и исходное задание.')
                    for item in documents:
                        if (isinstance(item,dict) and item.get('valid') is not False
                                and (item.get('kind')=='calendar' or missing_review or owner_conflict)):
                            item['valid']=False
                            previous=str(item.get('reason') or '').strip()
                            item['reason']='; '.join(part for part in (previous,reason+' Документ остаётся черновиком.') if part)
            results=[]
            verified_kinds=set()
            from task_artifacts import verify_saved_artifact
            for item in documents:
                kind=item.get('kind')
                body=str(item.get('content') or '').strip()
                editorial_issues=[] if semantic else editorial_warnings(kind,body)
                if editorial_issues:
                    item['valid']=False
                    previous_reason=str(item.get('reason') or '').strip()
                    item['reason']='; '.join(dict.fromkeys(([previous_reason] if previous_reason else []) + editorial_issues))[:1400]
                if kind not in ('strategy','calendar') or item.get('valid') is not True:
                    # Preserve the incomplete calendar as a clearly labelled draft,
                    # without placing it among accepted documents or claiming success.
                    draft_note=''
                    if kind in ('calendar','strategy') and body:
                        try:
                            drafts=self.core.workspace(self.current_project) / 'drafts'
                            drafts.mkdir(parents=True,exist_ok=True)
                            import hashlib
                            task_key=hashlib.sha256(instruction.encode('utf-8')).hexdigest()[:16]
                            draft_prefix='pending_content_calendar' if kind=='calendar' else 'pending_strategy'
                            draft_path=drafts / f'{draft_prefix}_{task_key}.md'
                            draft_path.write_text(body,encoding='utf-8')
                            draft_note=f'; черновик сохранён: {draft_path}'
                        except Exception as exc:
                            draft_note=f'; черновик не удалось сохранить: {exc}'
                    results.append(f"{kind}: не завершён ({item.get('reason') or 'проверка не пройдена'}{draft_note})")
                    receipt(self.db,self.current_project,instruction,str(kind),body,'rejected',item.get('reason') or '')
                    continue
                try:
                    from task_artifacts import verified_document
                    if verified_document(self.db,self.current_project,instruction,kind)!=body:
                        self.save_ai_document(instruction,body,kind=kind)
                    from task_artifacts import is_latest_document
                    check=verify_saved_artifact(self.db,self.current_project,kind,body,self._export_document_files)
                    if check: verified_kinds.add(kind)
                    results.append(f"{item.get('title')}: {'сохранён' if check else 'не подтверждено сохранение'}")
                    if check and kind=='calendar' and not semantic:
                        from stage60_quality import quality_warnings
                        warnings=quality_warnings(body)
                        if warnings:
                            results.append('Редакторские замечания (не блокируют сохранение черновика): '+ '; '.join(warnings))
                    receipt(self.db,self.current_project,instruction,kind,body,
                            ('reused' if item.get('already_saved') else 'saved') if check else 'failed',
                            json.dumps({'artifact_id':self.core.data.latest_document(self.current_project,kind=kind)[0],
                                        'storage_check':'manager_documents/export/readback'},ensure_ascii=False))
                except Exception as exc:
                    results.append(f"{item.get('title')}: ошибка сохранения ({exc})")
                    receipt(self.db,self.current_project,instruction,kind,body,'failed',str(exc))
            from task_artifacts import is_latest_document
            scope_report=task.get('content_delivery') or {}
            from completion_gate import can_close_task
            # Never close the pending request on an absent report or a count-only
            # success claim. Completion follows BOTH proof of storage AND full
            # validation of the original request and its required artifacts.
            stored_artifacts={kind:verified_document(self.db,self.current_project,instruction,kind) for kind in verified_kinds}
            # Completing the task requires the same host-owned identity at
            # generation, review and post-SQLite-persistence boundaries.
            execution_authority=None
            if semantic and claim is not None:
                from workflow_authority import ExecutionContract
                bound=claim.snapshot['sources']['pending']
                schedule=bound.get('schedule') or {}
                try:
                    execution_authority=ExecutionContract.create(bound['task_id'],instruction,
                        schedule.get('weeks'),schedule.get('per_week'))
                except (KeyError,TypeError,ValueError):
                    # Malformed ownership means no safe completion.
                    verified_kinds.clear()
            success = can_close_task(verified_kinds, scope_report,expected_parameters=expected_parameters,
                request=instruction,artifacts=stored_artifacts,execution_contract=execution_authority)
            self.ai_status.setText('Документы сохранены' if success else 'Задание не завершено')
            self.record_task_lifecycle('completed' if success else 'pending',
                completion_verified=bool(success),verified_documents=sorted(verified_kinds))
            if success:
                if hasattr(self,'_pending_ai_tasks'): self._pending_ai_tasks.pop(self.current_project,None)
                active_request=self.core.data.pending_request(self.current_project)
                if active_request is not None:
                    if active_request!=instruction:raise ValueError('Попытка завершить чужое задание')
                    from task_lifecycle import TaskLifecycle
                    from execution_runtime import ExecutionReceipt
                    (self.core.executions.finish(ExecutionReceipt(self.task_attempt.project_id,self.task_attempt.task_id,self.task_attempt),instruction)
                     if getattr(self,'task_attempt',None) is not None else
                     TaskLifecycle(self.db).finish(self.current_project,instruction,task_id=(getattr(self,'task_execution_claim',None).snapshot['sources']['pending']['task_id'] if getattr(self,'task_execution_claim',None) is not None else None)))
            else:
                # Do not claim full success or silently discard incomplete work.
                self.set_pending_task(instruction,'Часть документов не создана или не прошла проверку.')
            def publication_terminal():
                try:
                    signature=task.get('task_signature')
                    if isinstance(signature,str) and re.fullmatch(r'[0-9a-f]{64}',signature):
                        diagnostic=self.core.workspace(self.current_project)/'drafts'/(
                            'publication_diagnostics_'+signature[:16]+'.jsonl')
                        diagnostic.parent.mkdir(parents=True,exist_ok=True)
                        terminal={'time':datetime.now(timezone.utc).isoformat(),
                                  'stage':'task_done' if success else 'task_pending',
                                  'completion_verified':bool(success),
                                  'verified_documents':sorted(verified_kinds),
                                  'independent_editor':(scope_report.get('independent_editor') or {}).get('status')}
                        with diagnostic.open('a',encoding='utf-8') as handle:
                            handle.write(json.dumps(terminal,ensure_ascii=False)+'\n')
                except (OSError,TypeError,ValueError):
                    pass
            self._after_result_commit(publication_terminal)
            if task.get('error'):
                results.insert(0, 'Ошибка: '+str(task['error']))
            if scope_report:
                qty=scope_report.get('scope',{}).get('planned_total',0)
                count=scope_report.get('checked_posts',0)
                dims=scope_report.get('content_components_checked',{})
                results.append(f'Проверено содержимое: {count}/{qty}; текстов: {dims.get("caption",0)}, '
                               f'визуальных сценариев: {dims.get("visual",0)}, '
                               f'CTA: {dims.get("cta",0)}, KPI: {dims.get("kpi",0)}.')
                editorial=scope_report.get('independent_editor') or {}
                substantive=editorial.get('substance_review') or {}
                if substantive.get('expected_checks'):
                    good=sum(row.get('status')=='satisfied' for row in substantive.get('assessments',{}).values())
                    reading=(editorial.get('independent_reader') or {}).get('status','не завершено')
                    results.append(f'Редактор: {good}/{len(substantive["expected_checks"])} критериев без замечаний. '
                                   f'Независимое содержательное чтение: {reading}.')
                if scope_report.get('scope',{}).get('cadence_source')=='default':
                    results.append('Частота публикаций не указана явно: временно использована системная частота 3 в неделю.')
                if scope_report.get('rendered_media')=='not_created':
                    results.append('Готовые медиафайлы не созданы: сценарии не являются фото или видео.')
            response=('Оба документа сохранены. Автоматическая проверка завершена; неподтверждённые сведения необходимо уточнить по исходным данным бизнеса.' if success else 'Задание выполнено частично. Ниже указан фактический результат сохранения каждого документа. Подробные причины отклонения записаны в drafts/publication_diagnostics_*.jsonl. Напишите «продолжи задание» для продолжения. Результаты:')+'\n\n'+'\n'.join(results)
            self.append_chat('AI-менеджер',response)
            self.core.outcomes.record_answer_in_transaction(self.current_project,response,timestamp=now())
            return
        # A model may misclassify a question as a command. Do not write documents
        # unless the user's original request explicitly asks for creation.
        from agent_runtime import explicit_creation_request
        semantic=bool(getattr(self,'task_semantic_kind',None))
        if action == 'write' and not semantic and not explicit_creation_request(self.task_instruction):
            action = 'respond'
            from agent_runtime import conversational_answer
            try:
                task['answer'] = conversational_answer(self.task_instruction, {'project': '', 'recent_messages': []})['answer']
            except Exception:
                task['answer'] = 'Я могу обсудить эту тему и помочь с подготовкой документа. Что именно вас интересует?'
        # Also guard delayed/planned tasks against an incorrect Ollama response.
        if not semantic and action in ('write','respond') and self.is_direct_image_edit(instruction):
            self.dispatch_image_edit(instruction)
            return
        if action in ('write','respond'):
            answer=str(task.get('answer') or '').strip()
            # A model's action=write is not proof that it produced a usable document.
            if action == 'write':
                from agent_runtime import validate_document
                ok, reason = (bool(answer),'пустой результат') if semantic else validate_document(self.task_instruction, answer)
                if not semantic and ok and re.search(r'стратег', self.task_instruction, re.I) and re.search(r'контент[ -]?план', self.task_instruction, re.I):
                    ok = False
                    reason = ('Запрошены два отдельных документа, но исполнитель вернул один общий текст. '
                              'Не сохраняю его ошибочно как один контент-план; необходима отдельная генерация каждого документа.')
                if not ok:
                    action = 'respond'
                    answer = ('Документ не сохранён: ' + reason + '\n\n' +
                              (answer if answer else 'Уточните сведения о проекте, необходимые для задания.'))
            if action == 'respond' and getattr(self, 'task_instruction', ''):
                if not hasattr(self, '_pending_ai_tasks'):
                    self._pending_ai_tasks = {}
                from task_routing import creation_checkpoint_required
                if semantic or creation_checkpoint_required(self.task_instruction):
                    question='Результат не проверен и не сохранён как готовый материал. '+answer
                    self._pending_ai_tasks[self.current_project] = {
                        'request': self.task_instruction, 'question': question}
                    self.set_pending_task(self.task_instruction,question)
                    self.record_task_lifecycle('pending_response',completion_verified=False)
                    answer=('Задание не завершено: получен ответ модели, но выполнение и сохранение '
                            'запрошенных материалов не подтверждены.\n\n'+answer)
            elif action == 'write' and hasattr(self, '_pending_ai_tasks'):
                self._pending_ai_tasks.pop(self.current_project, None)
            if not answer:
                self.append_chat('Система','Модель не подготовила содержательный ответ. Попробуйте уточнить задачу.')
            else:
                if action!='write':
                    self.append_chat('AI-менеджер',answer)
                if action=='write':
                    self.text_editor.setPlainText(answer)
                    self.save_ai_document(self.task_instruction, answer,kind=self.task_semantic_kind if semantic else None)
                    latest=self.core.data.latest_document(self.current_project)
                    if (not latest or latest[1] != answer or (semantic and
                            not standalone_review_matches(task.get('quality_review'),self.task_instruction,latest[1]))):
                        self.ai_status.setText('Задание не завершено')
                        self.append_chat('AI-менеджер','Сохранение документа не подтверждено. Задание оставлено незавершённым.')
                        return
                    import hashlib
                    from durable_json import atomic_write_json
                    signature=hashlib.sha256((self.current_project+'\n'+self.task_instruction).encode('utf-8')).hexdigest()
                    quality=task.get('quality_review') or {}
                    from task_checkpoint import checkpoint_path
                    claim=getattr(self,'task_execution_claim',None)
                    task_id=(claim.snapshot['sources']['pending']['task_id']
                             if semantic and claim is not None else None)
                    receipt_path=(checkpoint_path(self.core.workspace(self.current_project),'text_receipt',self.task_instruction,task_id)
                        if task_id else self.core.workspace(self.current_project)/'drafts'/('text_receipt_'+signature[:16]+'.json'))
                    receipt_data={'request':self.task_instruction,
                        'request_sha256':hashlib.sha256(self.task_instruction.encode('utf-8')).hexdigest(),
                        'artifact_id':latest[0],
                        'artifact_sha256':hashlib.sha256(answer.encode('utf-8')).hexdigest(),
                        'quality_review':quality,'saved_and_read_back':False,
                        'completed_at':datetime.now(timezone.utc).isoformat()}
                    # Never write the receipt before commit: a SQL rollback must
                    # leave no durable success receipt on disk.
                    # Persist the file effects in the SAME SQLite transaction as the document.
                    # A process-kill after commit can replay them at next startup.
                    from file_effect_outbox import queue_document_completion, drain as drain_file_effects
                    pending_checkpoint=checkpoint_path(self.core.workspace(self.current_project),'pending_text',self.task_instruction,task_id)
                    if getattr(self, '_pending_result_export', None) != (self.current_project, latest[0]):
                        raise RuntimeError('Экспорт не связан с транзакцией документа')
                    queue_document_completion(self.db, project_id=self.current_project,
                        artifact_id=latest[0], content=answer, request=self.task_instruction,
                        receipt_path=receipt_path, pending_path=pending_checkpoint,
                        project_workspace=self.core.workspace(self.current_project), receipt_data=receipt_data)
                    # The project document and all post-commit effects now share
                    # one ledger. Task completion is a *derived* state, never a
                    # pre-commit GUI assertion or an unverified model response.
                    pid_for_export, aid_for_export = self.current_project, latest[0]
                    req_for_export = self.task_instruction
                    kind_for_export, title_for_export = latest[2], latest[3]
                    task_for_export = task_id
                    self._after_result_commit(lambda: self._finalize_ai_document_effects(
                        pid_for_export, aid_for_export, req_for_export,
                        kind_for_export, title_for_export, task_for_export))
                    self.ai_status.setText('Экспорт документа ожидает проверки')
                    return
                self.core.outcomes.record_answer_in_transaction(self.current_project,answer,timestamp=now())
        elif action=='image_edit':
            if not self.source.path or Path(self.source.path).suffix.lower() not in IMAGE_EXTENSIONS:
                self.append_chat('AI-менеджер','Выберите фотографию для редактирования. Твой запрос сохранён в истории проекта.')
            elif getattr(self,'engine_mode',0)==1:
                self.cloud_generate(instruction)
            elif getattr(self,'engine_mode',0)==2:
                self.hf_generate(instruction)
            else:
                self.modern_local_generate(instruction)
        elif action=='video_trim':
            src=self.source.path
            if not src or Path(src).suffix.lower() not in {'.mp4','.mov','.mkv','.avi','.webm'}:
                self.append_chat('AI-менеджер','Для монтажа выберите видеофайл как исходник.')
            elif not available_tools()['video_trim_ffmpeg']:
                self.append_chat('AI-менеджер','Для монтажа нужен бесплатный FFmpeg. Он не установлен или не найден в PATH. Автоматически ничего не скачиваю.')
            elif not isinstance(task.get('start'),(int,float)) or not isinstance(task.get('end'),(int,float)):
                self.append_chat('AI-менеджер','Укажите начало и конец нужного фрагмента в секундах.')
            else:
                out=self.core.workspace(self.current_project)/'processed'/(uuid4().hex+'.mp4')
                self.video_worker=VideoTrimWorker(src,out,task['start'],task['end'])
                self.video_worker.done.connect(lambda path, pid=self.current_project:
                                               self.video_done(path, project_id=pid))
                self.video_worker.failed.connect(lambda err:self.append_chat('Ошибка видеомонтажа',err))
                self.video_worker.finished.connect(self.video_finished)
                self.video_worker.start()
                self.append_chat('AI-менеджер','Обрабатываю видео локально. Исходник не изменяется.')
        from task_routing import creation_checkpoint_required
        incomplete_response=(action=='respond' and (semantic or creation_checkpoint_required(self.task_instruction)))
        self.ai_status.setText('Задание не завершено' if incomplete_response else
                               'Готов' if self.video_worker is None else 'Обработка видео…')

    def task_failed(self,error):
        # A worker failure belongs to its captured origin even when the GUI
        # has since displayed another project. One core transaction settles
        # the attempt and saves the failure explanation atomically.
        origin=getattr(self,'task_project_id',None)
        attempt=getattr(self,'task_attempt',None)
        response='Задание не завершено. Причина: '+str(error)
        failure_saved=False
        if origin and not self.db.in_transaction:
            try:
                self.core.record_execution_failure(project_id=origin,attempt=attempt,
                    response=response,timestamp=now())
                failure_saved=True
                self.task_attempt=None
            except (ValueError,RuntimeError,sqlite3.Error) as exc:
                self.statusBar().showMessage('Не удалось сохранить ошибку задания: '+str(exc),15000)
                # Do not clear an unsettled attempt. Startup recovery owns it.
        if hasattr(self,'_task_clock'): self._task_clock.stop()
        self.task_worker=None
        self.send_button.setEnabled(True)
        self.ai_status.setText('Ошибка AI')
        self.record_task_lifecycle('failed',completion_verified=False,error=str(error))
        if origin and origin!=self.current_project:
            self.append_chat('Система','Задание другого проекта завершилось ошибкой; откройте его диагностику в папке drafts этого проекта.')
            return
        self.append_chat('Система','Ошибка выполнения AI-задания: '+str(error))
        self.technical_log.setVisible(True)
        self.log_toggle.setText('▾ Скрыть технический журнал')
        if origin==self.current_project:
            self.append_chat('AI-менеджер',response if failure_saved else
                'Сохранение ошибки ожидает восстановления. '+response)

    def video_done(self, path, *, project_id=None):
        # Worker callbacks must never infer their owner from active GUI selection.
        pid = project_id
        try:
            if not isinstance(pid, str) or not pid:
                raise ValueError('Missing immutable video worker project identity')
            args=self._semantic_media_publication(pid,'video')
            self.core.publish_result(pid, 'video', path, active_project_id=pid,**args)
            self._semantic_media_accepted(pid,'video')
            if self.current_project == pid:
                self.register_ready_result(str(Path(path).resolve()),project_id=pid)
                self.append_chat('Система', 'Видео сохранено отдельным файлом: ' + str(path))
        except Exception as exc:
            self.statusBar().showMessage('Видео не принято: ' + str(exc), 15000)

    def video_finished(self):
        self.video_worker=None
        self.ai_status.setText('Готов')

    def finish_worker(self):
        self.send_button.setEnabled(True)
        self.projects.setEnabled(True)
        self.ai_status.setText('Готов')
        self.vision_button.setEnabled(True)
        self.worker = None

    def ai_done(self, response):
        # A late AI callback cannot attach itself to whichever project is open.
        project_id = getattr(self, 'task_project_id', None)
        try:
            if not isinstance(project_id, str) or not project_id:
                raise ValueError('Missing immutable AI worker project identity')
            self.core.publish_result(project_id, 'message', response, active_project_id=project_id)
            if project_id == self.current_project:
                self.append_chat('AI', response)
            else:
                self.append_chat('Система', 'AI-ответ сохранён в исходном проекте.')
        except Exception as exc:
            self.append_chat('Система', 'AI-ответ не принят: ' + str(exc))
        finally:
            self.finish_worker()

    def ai_failed(self, error):
        self.append_chat('Система', 'Задание не завершено: ' + error)
        self.finish_worker()

if __name__ == '__main__':
    try:
        (ROOT / 'logs' / 'hardware_profile.json').write_text(
            json.dumps(detect_hardware(), ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = Studio()
    window.showMaximized()
    QTimer.singleShot(0, window._recover_committed_document_exports)
    sys.exit(app.exec())
