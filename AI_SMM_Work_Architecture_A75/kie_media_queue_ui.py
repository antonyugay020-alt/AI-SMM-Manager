"""Project-scoped Qt view of durable Kie media jobs and explicit background result refresh.

Paid submission stays disabled without a verified quote and approval. Result
refresh uses a separately owned worker and never resubmits a paid task.
"""
from __future__ import annotations

import shutil
import sqlite3
import weakref

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
    QPlainTextEdit, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox,
    QListWidget,QListWidgetItem,QLineEdit,QInputDialog,
)
from kie_media_contracts import nano_banana_pro
from kie_media_jobs import prepare
from durable_cost_gate import DurableCostGate
from smm_media_router import (TASKS, MediaRequest, select_executor,
                              default_executors)
from kie_media_jobs import (list_project_tasks, abandon_prepared,
                            preview_prepared_task, recover_pending)

_COLUMNS = ("Создано (UTC)", "Модель", "Состояние", "ID задания", "Результат / ошибка")


class KieMediaQueuePanel(QWidget):
    """Resolve project identity on every read, and freeze it for every action."""

    def __init__(self, db, current_project, parent=None, *, cost_gate=None, result_worker_factory=None,media_service=None):
        super().__init__(parent)
        if not callable(current_project):
            raise TypeError("current_project must be callable")
        if cost_gate is not None and not isinstance(cost_gate, DurableCostGate):
            raise TypeError("cost_gate must be the shared durable paid ledger")
        self._media_service = media_service
        self._recovered_projects = set()
        self._result_worker_factory = result_worker_factory
        self._result_worker = None
        self._db = db
        self._cost_gate = cost_gate
        self._current_project = current_project
        self._visible_project = None
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Kie.ai · подготовка и генерация медиа. Платный запуск требует "
            "отдельного подтверждения максимальной стоимости и общего бюджета. "
            "Список читается из базы проекта. Кнопка получения результата "
            "проверяет только уже подтверждённые задания Kie.ai; повторной оплаты нет."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        route_row=QHBoxLayout()
        route_row.addWidget(QLabel("SMM-операция:"))
        self.smm_task=QComboBox()
        self.smm_task.addItems(sorted(TASKS))
        route_row.addWidget(self.smm_task,1)
        route_row.addWidget(QLabel("Исходников:"))
        self.smm_source_count=QSpinBox()
        self.smm_source_count.setRange(0,30)
        self.smm_source_count.setValue(1)
        route_row.addWidget(self.smm_source_count)
        self.smm_reference=QCheckBox("Эталон")
        route_row.addWidget(self.smm_reference)
        route_button=QPushButton("Проверить маршрут")
        route_button.clicked.connect(self.preview_route)
        route_row.addWidget(route_button)
        layout.addLayout(route_row)
        self.route_status=QLabel(
            "Маршрут только планируется: внешние API и платные операции не запускаются.")
        self.route_status.setWordWrap(True)
        layout.addWidget(self.route_status)
        budget_row = QHBoxLayout()
        budget_row.addWidget(QLabel("Месячный лимит Kie.ai (USD):"))
        self.monthly_limit = QDoubleSpinBox()
        self.monthly_limit.setRange(0, 100000)
        self.monthly_limit.setDecimals(2)
        self.monthly_limit.setValue(20.00)
        budget_row.addWidget(self.monthly_limit)
        self.save_budget_button = QPushButton("Сохранить лимит")
        self.save_budget_button.clicked.connect(self.save_budget)
        budget_row.addWidget(self.save_budget_button)
        self.budget_status = QLabel("")
        budget_row.addWidget(self.budget_status,1)
        layout.addLayout(budget_row)
        mode_row=QHBoxLayout()
        mode_row.addWidget(QLabel("Режим:"))
        self.generation_mode=QComboBox()
        for label,mode in [("Изображение · Pro","image_pro"),("Редактирование изображения","image_edit"),
                           ("Видео из исходников","video_reference"),("Редактирование видео","video_edit")]:
            self.generation_mode.addItem(label,mode)
        mode_row.addWidget(self.generation_mode)
        self.duration=QSpinBox();self.duration.setRange(3,15);self.duration.setValue(5)
        mode_row.addWidget(QLabel("Видео, сек.:"));mode_row.addWidget(self.duration)
        layout.addLayout(mode_row)
        self.sources=QListWidget()
        self.sources.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.sources.setMaximumHeight(85)
        self.sources.itemSelectionChanged.connect(self._source_selection_changed)
        self.sources.setToolTip("Выберите зарегистрированные файлы текущего проекта. Ctrl — несколько файлов.")
        layout.addWidget(self.sources)
        draft_row = QHBoxLayout()
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Описание изображения для Nano Banana Pro")
        self.prompt.setMaximumHeight(76)
        draft_row.addWidget(self.prompt, 1)
        self.aspect_ratio = QComboBox()
        self.aspect_ratio.addItems(["1:1", "9:16", "16:9", "4:5", "3:4"])
        draft_row.addWidget(self.aspect_ratio)
        self.resolution = QComboBox()
        self.resolution.addItems(["1K", "2K", "4K"])
        self.generation_mode.currentIndexChanged.connect(self._mode_changed)
        draft_row.addWidget(self.resolution)
        create_button = QPushButton("Сохранить черновик")
        self.create_button=create_button
        create_button.setToolTip("Только локальная запись в SQLite, без загрузки и генерации")
        create_button.clicked.connect(self.create_draft)
        draft_row.addWidget(create_button)
        layout.addLayout(draft_row)
        toolbar = QHBoxLayout()
        preview_button = QPushButton("Просмотреть черновик")
        preview_button.clicked.connect(self.preview_selected)
        toolbar.addWidget(preview_button)
        reload_button = QPushButton("Обновить список")
        reload_button.clicked.connect(self.refresh)
        toolbar.addWidget(reload_button)
        self.fetch_button = QPushButton("Получить результат Kie.ai")
        self.fetch_button.setEnabled(callable(result_worker_factory))
        self.fetch_button.clicked.connect(self.fetch_selected_result)
        toolbar.addWidget(self.fetch_button)
        self.prepare_button=QPushButton("Подготовить исходники")
        self.prepare_button.setEnabled(media_service is not None)
        self.prepare_button.clicked.connect(self.prepare_selected)
        toolbar.addWidget(self.prepare_button)
        discard_button = QPushButton("Удалить неоплаченную заготовку")
        discard_button.clicked.connect(self.discard_selected)
        toolbar.addWidget(discard_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        pricing=QHBoxLayout()
        self.maximum_cost=QDoubleSpinBox();self.maximum_cost.setDecimals(6)
        self.maximum_cost.setRange(0,100000);self.maximum_cost.setValue(0)
        pricing.addWidget(QLabel("Проверенный максимум USD:"));pricing.addWidget(self.maximum_cost)
        self.price_evidence=QLineEdit()
        self.price_evidence.setPlaceholderText("Источник проверенного тарифа / ссылка на расчёт")
        pricing.addWidget(self.price_evidence,1)
        self.price_confirmed=QCheckBox("Максимальная стоимость проверена")
        pricing.addWidget(self.price_confirmed)
        self.generate_button=QPushButton("Запустить генерацию")
        self.generate_button.setEnabled(media_service is not None)
        self.generate_button.clicked.connect(self.submit_selected)
        pricing.addWidget(self.generate_button)
        layout.addLayout(pricing)
        stopping=QHBoxLayout()
        self.stop_button=QPushButton("Остановить локальную обработку")
        self.stop_button.clicked.connect(self.stop_selected);stopping.addWidget(self.stop_button)
        self.resume_button=QPushButton("Возобновить получение результата")
        self.resume_button.clicked.connect(self.resume_selected);stopping.addWidget(self.resume_button)
        self.recovery_button=QPushButton('Восстановить ID из Kie.ai')
        self.recovery_button.setEnabled(media_service is not None)
        self.recovery_button.clicked.connect(self.recover_selected_submission)
        stopping.addWidget(self.recovery_button)
        layout.addLayout(stopping)
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        # The editor must never briefly advertise a second, disconnected
        # paid budget before the user clicks Refresh.
        self.refresh_budget()

    def _owner(self):
        owner = self._current_project()
        if not isinstance(owner, str) or not owner:
            return None
        return owner

    def preview_route(self):
        # No provider calls. Availability is based only on observed local
        # executable presence; cloud adapters must register separately.
        try:
            request=MediaRequest(
                task=self.smm_task.currentText(),
                source_count=self.smm_source_count.value(),
                prompt=self.prompt.toPlainText(),
                has_style_reference=self.smm_reference.isChecked())
            candidates=default_executors(ffmpeg_ready=bool(shutil.which("ffmpeg")))
            decision=select_executor(request,candidates)
        except (ValueError,TypeError) as exc:
            self.route_status.setText("Маршрут недоступен: "+str(exc))
            return
        if decision.state=="ready":
            self.route_status.setText(
                "Подходящий исполнитель: "+decision.executor+
                " (локально). Планирование завершено; исполнение не запускалось.")
        else:
            self.route_status.setText(
                "Нет подтверждённого исполнителя для этой SMM-задачи. "
                "Неподключённые облачные демо не считаются доступным API.")

    def save_budget(self):
        if self._cost_gate is None:
            self.refresh_budget()
            return
        proposed=int(round(self.monthly_limit.value()*100))
        confirmation=QMessageBox.question(
            self,"Месячный лимит Kie.ai",
            f"Изменить общий месячный лимит платной генерации на ${proposed/100:.2f}? "
            "Уже зарезервированные расходы сохранятся.")
        if confirmation!=QMessageBox.Yes:
            self.refresh_budget()
            return
        try:
            self._cost_gate.set_monthly_cap(f"{proposed/100:.2f}")
        except (ValueError,RuntimeError,OSError,PermissionError) as exc:
            QMessageBox.warning(self,"Kie.ai",str(exc))
        self.refresh_budget()

    def refresh_budget(self):
        # Only the same durable gate used for network dispatch is authoritative.
        # Without an injected shared ledger the budget editor is disabled.
        active=self._cost_gate is not None
        # An unreadable or corrupt ledger must never look editable.
        self.monthly_limit.setEnabled(False)
        self.save_budget_button.setEnabled(False)
        if not active:
            self.budget_status.setText(
                "Платный журнал не подключён; лимит здесь не редактируется.")
            return
        try:
            state=self._cost_gate.usage()
            legacy=self._db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='kie_media_allocations'").fetchone()
            unresolved=bool(legacy and self._db.execute(
                "SELECT 1 FROM kie_media_allocations LIMIT 1").fetchone())
        except (ValueError,RuntimeError,OSError,PermissionError) as exc:
            self.budget_status.setText("Бюджет недоступен: "+str(exc))
            return
        self.monthly_limit.setValue(float(state["cap_usd"]))
        self.monthly_limit.setEnabled(True)
        self.save_budget_button.setEnabled(True)
        if unresolved:
            self.budget_status.setText(
                "Обнаружены старые резервы Kie: платные запросы заблокированы "
                "до сверки расходов.")
        else:
            self.budget_status.setText(
                f'Резерв ${state["pending_usd"]:.2f}, '
                f'подтверждено ${state["spent_usd"]:.2f}, '
                f'остаток ${state["available_usd"]:.2f}' +
                (" · Платные вызовы заморожены" if state["paid_calls_frozen"] else ""))

    def refresh(self):
        self.refresh_budget()
        owner = self._owner()
        if owner != self._visible_project:
            # Unsubmitted prompts must not leak across project selections.
            self.prompt.clear()
            self.price_evidence.clear();self.price_confirmed.setChecked(False);self.maximum_cost.setValue(0)
        self._load_sources(owner)
        self.table.setRowCount(0)
        self._visible_project = None
        if owner is None:
            self.note.setText("Сначала выберите проект.")
            return
        try:
            # Reconcile interrupted local dispatch before rendering the queue.
            # No provider request or automatic paid retry is ever made here.
            recover_pending(self._db, owner)
            jobs = list_project_tasks(self._db, owner, limit=200)
            from media_generation_runtime import install,recover_intents
            if self._media_service is not None:
                if owner not in self._recovered_projects and self._result_worker is None:
                    recover_intents(self._db,owner);self._recovered_projects.add(owner)
                install(self._db);self._db.commit()
                existing={job['id'] for job in jobs}
                for ident,model,state,stop,error,created in self._db.execute(
                        'SELECT id,model,state,stop_requested,error_code,created_at FROM media_generation_intents WHERE project_id=? ORDER BY created_at DESC LIMIT 200',(owner,)):
                    if ident not in existing:
                        jobs.append({'id':ident,'model':model,'state':state,'local_stop_requested':bool(stop),
                                     'output_path':None,'error_code':error,'created_at':created})
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            self.note.setText("Очередь недоступна: " + str(exc))
            return
        self._visible_project = owner
        for index, job in enumerate(jobs):
            self.table.insertRow(index)
            cells = (
                job["created_at"], job["model"], job["state"] +
                (" · остановлено локально" if job.get('local_stop_requested') else ""),
                job["id"], job["output_path"] or job["error_code"] or "",
            )
            for column, value in enumerate(cells):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, job["id"])
                self.table.setItem(index, column, item)
        self.note.setText(f"Сохранено заданий: {len(jobs)}. "
                          "Неизвестные платные отправки нельзя повторять или удалять.")

    def create_draft(self):
        # Local-only preflight: validation and SQLite write, no API calls.
        owner = self._owner()
        if owner is None:
            self.note.setText("Сначала выберите проект.")
            return
        if self._result_worker is not None:return
        try:
            selected=[item.data(Qt.UserRole) for item in self.sources.selectedItems()]
            mode=self.generation_mode.currentData()
            if mode!='image_pro' or selected:
                if self._media_service is None:raise RuntimeError('Сервис подготовки медиа не подключён')
                request={'mode':mode,'prompt':self.prompt.toPlainText(),'sources':selected,
                    'aspect_ratio':self.aspect_ratio.currentText(),'resolution':self.resolution.currentText(),
                    'duration':self.duration.value()}
                worker=self._media_service.intent_worker(owner,request)
                self._launch_worker(worker)
                self.note.setText('Проверяю исходники и сохраняю локальное задание…')
                return
            payload = nano_banana_pro(
                self.prompt.toPlainText(), (),
                aspect_ratio=self.aspect_ratio.currentText(),
                resolution=self.resolution.currentText())
            if self._owner() != owner:
                raise RuntimeError("Проект изменился во время подготовки задания")
            prepare(self._db, owner, payload)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            self.note.setText("Черновик не сохранён: " + str(exc))
            return
        self.prompt.clear()
        self.refresh()

    def preview_selected(self):
        owner=self._owner()
        if owner is None or owner!=self._visible_project:
            self.refresh()
            return
        row=self.table.currentRow()
        if row<0:
            return
        item=self.table.item(row,3)
        if item is None:
            return
        try:
            info=preview_prepared_task(self._db,owner,item.data(Qt.UserRole))
        except (ValueError,RuntimeError,OSError,sqlite3.Error) as exc:
            QMessageBox.warning(self,"Kie.ai","Черновик недоступен: "+str(exc))
            self.refresh()
            return
        if self._owner()!=owner:
            self.refresh()
            return
        details="\n".join(f"{key}: {value}" for key,value in info["options"].items())
        QMessageBox.information(self,"Черновик Kie.ai",
            "Модель: "+info["model"]+"\n"+
            "Запрос: "+info["prompt"]+"\n"+
            "Исходных файлов: "+str(info["reference_count"])+
            ("\n"+details if details else "")+
            "\n\nТолько просмотр: платный запрос не отправляется.")

    def discard_selected(self):
        # A project switch after rendering must not retarget a stale row.
        owner = self._owner()
        if owner is None or owner != self._visible_project:
            self.refresh()
            return
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 3)
        if item is None:
            return
        job_id = item.data(Qt.UserRole)
        state_item = self.table.item(row, 2)
        if state_item is None or state_item.text() != "prepared":
            QMessageBox.warning(self, "Kie.ai", "Удалять можно только неоплаченные заготовки.")
            return
        answer = QMessageBox.question(
            self, "Kie.ai", "Удалить неоплаченную заготовку этого проекта?")
        if answer != QMessageBox.Yes:
            return
        # Check owner again after the modal confirmation.
        if owner != self._owner():
            self.refresh()
            return
        try:
            abandon_prepared(self._db, owner, job_id)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Kie.ai", str(exc))
        self.refresh()

    def fetch_selected_result(self):
        if self._result_worker is not None or not callable(self._result_worker_factory):
            return
        owner = self._owner()
        if owner is None or owner != self._visible_project:
            self.refresh()
            return
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 3)
        if item is None:
            return
        job = item.data(Qt.UserRole)
        try:
            worker = self._result_worker_factory(owner, job)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            self.note.setText("Результат недоступен: " + type(exc).__name__)
            return
        # Factory must not launch work before the frozen owner is checked.
        if self._owner() != owner:
            return
        self._launch_worker(worker)

    def _launch_worker(self,worker):
        self._result_worker = worker
        worker.setParent(self)
        worker.done.connect(self._result_done)
        worker.failed.connect(self._result_failed)
        worker.stopped.connect(self._result_stopped)
        worker.finished.connect(self._result_finished)
        window=self.window()
        if not hasattr(window,'_background_workers'):window._background_workers=[]
        window._background_workers.append(worker)
        self._worker_window=weakref.ref(window)
        for button in (self.fetch_button,self.prepare_button,self.generate_button,self.create_button):button.setEnabled(False)
        worker.start()

    def _result_done(self, owner, job, output):
        if owner == self._owner():
            self.refresh()
            operation=getattr(self._result_worker,'operation','refresh')
            if operation=='draft':
                if self.prompt.toPlainText()==self._result_worker.request.get('prompt'):self.prompt.clear()
                self.note.setText('Задание сохранено локально. Разрешите загрузку через «Подготовить исходники».')
            elif operation=='prepare':self.note.setText('Исходники подготовлены. Платный запрос ещё не отправлен.')
            elif operation=='reconcile':self.note.setText('Удалённый ID проверен и сохранён. Повторной оплаты нет.')
            elif operation=='submit':self.note.setText('Задание принято Kie.ai. Получите результат после завершения.')
            else:self.note.setText("Результат сохранён и проверен." if output else
                                   "Задание ещё выполняется в Kie.ai.")

    def _result_failed(self, owner, job, code):
        if owner == self._owner():
            self.note.setText("Не удалось получить результат: " + code +
                              ". Повторная платная отправка не выполнялась.")

    def _result_finished(self):
        worker = self._result_worker
        if worker is not None:
            window=self._worker_window()
            workers=getattr(window,'_background_workers',[]) if window is not None else []
            if worker in workers:workers.remove(worker)
            self._worker_window=None
            self._result_worker = None
            worker.deleteLater()
        self.fetch_button.setEnabled(callable(self._result_worker_factory))
        self.prepare_button.setEnabled(self._media_service is not None)
        self.generate_button.setEnabled(self._media_service is not None)
        self.create_button.setEnabled(True)

    def closeEvent(self, event):
        if self._result_worker is not None and self._result_worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)

    def _mode_changed(self):
        video=self.generation_mode.currentData().startswith('video')
        self.resolution.clear();self.resolution.addItems(['720p','1080p','4k'] if video else ['1K','2K','4K'])
        self.duration.setEnabled(video)
        self.resolution.setEnabled(self.generation_mode.currentData()!='image_edit')
        self.aspect_ratio.clear()
        self.aspect_ratio.addItems(['16:9','9:16','1:1','auto'] if video else ['1:1','9:16','16:9','4:5','3:4'])
        self._source_selection_changed()

    def _source_selection_changed(self):
        if not hasattr(self,'aspect_ratio') or not self.generation_mode.currentData().startswith('video'):return
        from pathlib import Path
        chosen=[Path(item.data(Qt.UserRole)) for item in self.sources.selectedItems()]
        if chosen and all(path.suffix.lower() in ('.mp4','.mov') for path in chosen):
            self.aspect_ratio.setCurrentText('auto')
        elif self.aspect_ratio.currentText()=='auto':self.aspect_ratio.setCurrentText('16:9')

    def _load_sources(self,owner):
        selected={item.data(Qt.UserRole) for item in self.sources.selectedItems()} if owner==self._visible_project else set()
        self.sources.clear()
        if not owner:return
        for table,column,label in [('assets','stored_path','original_name'),('project_files','path','display_name')]:
            if not self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():continue
            try:rows=self._db.execute('SELECT '+column+','+label+' FROM '+table+' WHERE project_id=?',(owner,)).fetchall()
            except sqlite3.Error:continue
            for path,name in rows:
                from pathlib import Path
                if Path(path).suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.mp4','.mov'):continue
                item=QListWidgetItem(str(name));item.setData(Qt.UserRole,path)
                self.sources.addItem(item);item.setSelected(path in selected)

    def _selection(self):
        owner=self._owner()
        if owner is None or owner!=self._visible_project:
            self.refresh();return None
        row=self.table.currentRow()
        item=self.table.item(row,3) if row>=0 else None
        return (owner,item.data(Qt.UserRole)) if item is not None else None

    def prepare_selected(self):
        chosen=self._selection()
        if not chosen or self._media_service is None or self._result_worker is not None:return
        owner,job=chosen
        answer=QMessageBox.question(self,'Загрузка исходников',
            'Разрешить загрузку выбранных исходников проекта в Kie.ai? Генерация этим действием не оплачивается.')
        if answer!=QMessageBox.Yes or owner!=self._owner():return
        try:worker=self._media_service.preparation_worker(owner,job)
        except (ValueError,RuntimeError,OSError,sqlite3.Error) as exc:
            self.note.setText('Подготовка недоступна: '+type(exc).__name__);return
        self._launch_worker(worker)

    def submit_selected(self):
        chosen=self._selection()
        if not chosen or self._media_service is None or self._result_worker is not None:return
        owner,job=chosen
        if not self.price_confirmed.isChecked() or self.maximum_cost.value()<=0 or not self.price_evidence.text().strip():
            self.note.setText('Укажите и подтвердите проверенную максимальную стоимость и её источник.');return
        try:info=preview_prepared_task(self._db,owner,job)
        except (ValueError,RuntimeError,sqlite3.Error):
            self.note.setText('Сначала подготовьте неоплаченное задание.');return
        maximum=f'{self.maximum_cost.value():.6f}'
        answer=QMessageBox.question(self,'Платная генерация Kie.ai',
            'Модель: '+info['model']+'\nЗапрос: '+info['prompt']+
            '\nМаксимальная стоимость: $'+maximum+'\nИсточник: '+self.price_evidence.text()+
            '\nРазрешить один платный запрос с резервированием общего бюджета?')
        if answer!=QMessageBox.Yes or owner!=self._owner():return
        try:
            token=self._media_service.approve_generation(owner,job,maximum,self.price_evidence.text(),confirmed=True)
            worker=self._media_service.submission_worker(owner,job,token)
        except (ValueError,RuntimeError,PermissionError,OSError,sqlite3.Error) as exc:
            self.note.setText('Запуск заблокирован: '+type(exc).__name__);return
        self.price_confirmed.setChecked(False)
        self._launch_worker(worker)

    def stop_selected(self):
        worker=self._result_worker
        if worker is not None and getattr(worker,'operation',None)=='draft' and worker.project_id==self._owner():
            worker.requestInterruption()
            self.note.setText('Останавливаю проверку исходников до сохранения задания.');return
        chosen=self._selection()
        if not chosen:return
        owner,job=chosen
        try:
            if self._media_service is not None:self._media_service.stop_generation(owner,job)
            else:
                from media_task_control import request_stop
                request_stop(self._db,owner,job)
            worker=self._result_worker
            if worker is not None and worker.project_id==owner and worker.job_id==job:
                worker.requestInterruption()
        except (ValueError,RuntimeError,sqlite3.Error) as exc:
            self.note.setText('Остановка недоступна: '+type(exc).__name__);return
        self.refresh()
        self.note.setText('Локальная остановка сохранена. Уже отправленное платное задание может продолжаться в Kie.ai.')

    def resume_selected(self):
        chosen=self._selection()
        if not chosen:return
        from media_task_control import resume_confirmed
        try:resume_confirmed(self._db,*chosen)
        except (ValueError,RuntimeError,sqlite3.Error) as exc:
            self.note.setText('Возобновление недоступно: '+type(exc).__name__);return
        self.refresh()
        self.note.setText('Получение результата разрешено. Новая платная отправка не выполнялась.')

    def _result_stopped(self,owner,job):
        if owner==self._owner():
            self.refresh()
            self.note.setText('Локальная обработка остановлена; повторной оплаты нет.')

    def recover_selected_submission(self):
        chosen=self._selection()
        if not chosen or self._media_service is None or self._result_worker is not None:return
        owner,job=chosen
        provider_id,ok=QInputDialog.getText(self,'Восстановление после сбоя','ID оплаченного задания из журнала Kie.ai:')
        if not ok or owner!=self._owner():return
        evidence,ok=QInputDialog.getText(self,'Журнал Kie.ai','Ссылка или номер записи, подтверждающей эту отправку:')
        if not ok or owner!=self._owner():return
        answer=QMessageBox.question(self,'Проверить удалённое задание?',
            'Проверить модель и точный запрос задания '+provider_id+'? Новая генерация не запускается, резерв расходов сохраняется.')
        if answer!=QMessageBox.Yes or owner!=self._owner():return
        try:worker=self._media_service.recovery_worker(owner,job,provider_id.strip(),evidence,confirmed=True)
        except (ValueError,RuntimeError,PermissionError,sqlite3.Error) as exc:
            self.note.setText('Восстановление недоступно: '+type(exc).__name__);return
        self._launch_worker(worker)
