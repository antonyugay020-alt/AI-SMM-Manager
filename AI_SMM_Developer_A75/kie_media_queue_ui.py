"""Project-scoped Qt view of durable Kie media jobs. Offline only.

This panel intentionally cannot submit a paid task or guess provider status
routes. It shows verified local task state and can discard unpaid drafts only.
"""
from __future__ import annotations

import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
    QPlainTextEdit, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox,
)
from kie_media_contracts import nano_banana_pro
from kie_media_jobs import prepare
from kie_media_budget import snapshot, set_monthly_limit
from smm_media_router import (TASKS, MediaRequest, select_executor,
                              default_executors)
from kie_media_jobs import list_project_tasks, abandon_prepared, preview_prepared_task

_COLUMNS = ("Создано (UTC)", "Модель", "Состояние", "ID задания", "Результат / ошибка")


class KieMediaQueuePanel(QWidget):
    """Resolve project identity on every read, and freeze it for every action."""

    def __init__(self, db, current_project, parent=None):
        super().__init__(parent)
        if not callable(current_project):
            raise TypeError("current_project must be callable")
        self._db = db
        self._current_project = current_project
        self._visible_project = None
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Kie.ai · локальная очередь заданий. Платная генерация отключена. "
            "Загрузка файлов и опрос API ещё не подключены; состояние обновляется "
            "только из сохранённой базы проекта."
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
        save_budget = QPushButton("Сохранить лимит")
        save_budget.clicked.connect(self.save_budget)
        budget_row.addWidget(save_budget)
        self.budget_status = QLabel("")
        budget_row.addWidget(self.budget_status,1)
        layout.addLayout(budget_row)
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
        draft_row.addWidget(self.resolution)
        create_button = QPushButton("Сохранить черновик")
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
        discard_button = QPushButton("Удалить неоплаченную заготовку")
        discard_button.clicked.connect(self.discard_selected)
        toolbar.addWidget(discard_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
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
        proposed=int(round(self.monthly_limit.value()*100))
        confirmation=QMessageBox.question(
            self,"Месячный лимит Kie.ai",
            f"Изменить общий месячный лимит платной генерации на ${proposed/100:.2f}? "
            "Уже зарезервированные расходы сохранятся.")
        if confirmation!=QMessageBox.Yes:
            self.refresh_budget()
            return
        try:
            set_monthly_limit(self._db,proposed)
        except (ValueError,RuntimeError,OSError) as exc:
            QMessageBox.warning(self,"Kie.ai",str(exc))
        self.refresh_budget()

    def refresh_budget(self):
        try:
            state=snapshot(self._db)
        except (ValueError,RuntimeError,OSError) as exc:
            self.budget_status.setText("Бюджет недоступен: "+str(exc))
            return
        self.monthly_limit.setValue(state["limit_cents"]/100)
        self.budget_status.setText(
            f'{state["month_utc"]} UTC: резерв ${state["allocated_cents"]/100:.2f}, '
            f'остаток ${state["remaining_cents"]/100:.2f}')

    def refresh(self):
        self.refresh_budget()
        owner = self._owner()
        if owner != self._visible_project:
            # Unsubmitted prompts must not leak across project selections.
            self.prompt.clear()
        self.table.setRowCount(0)
        self._visible_project = None
        if owner is None:
            self.note.setText("Сначала выберите проект.")
            return
        try:
            jobs = list_project_tasks(self._db, owner, limit=200)
        except (ValueError, RuntimeError, OSError) as exc:
            self.note.setText("Очередь недоступна: " + str(exc))
            return
        self._visible_project = owner
        for index, job in enumerate(jobs):
            self.table.insertRow(index)
            cells = (
                job["created_at"], job["model"], job["state"],
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
        try:
            payload = nano_banana_pro(
                self.prompt.toPlainText(), (),
                aspect_ratio=self.aspect_ratio.currentText(),
                resolution=self.resolution.currentText())
            if self._owner() != owner:
                raise RuntimeError("Проект изменился во время подготовки задания")
            prepare(self._db, owner, payload)
        except (ValueError, RuntimeError, OSError) as exc:
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
        except (ValueError,RuntimeError,OSError) as exc:
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
        except (ValueError, RuntimeError, OSError) as exc:
            QMessageBox.warning(self, "Kie.ai", str(exc))
        self.refresh()
