"""
Миксин для создания панелей UI
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from app.gui.ocr_preview import OcrPreviewWidget
from app.gui.page_viewer import PageViewer
from app.gui.project_tree import ProjectTreeWidget


class PanelsSetupMixin:
    """Миксин для создания панелей интерфейса"""

    def _setup_ui(self):
        """Настройка интерфейса с док-панелями"""
        # Центральный виджет — только PageViewer
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Контейнер для page_viewer с кнопкой закрытия
        viewer_container = QWidget()
        viewer_layout = QVBoxLayout(viewer_container)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(0)

        self.page_viewer = PageViewer()
        self.page_viewer.blockDrawn.connect(self._on_block_drawn)
        self.page_viewer.polygonDrawn.connect(self._on_polygon_drawn)
        self.page_viewer.block_selected.connect(self._on_block_selected)
        self.page_viewer.blocks_selected.connect(self._on_blocks_selected)
        self.page_viewer.blockDeleted.connect(self._on_block_deleted)
        self.page_viewer.blocks_deleted.connect(self._on_blocks_deleted)
        self.page_viewer.blockMoved.connect(self._on_block_moved)

        # Кнопка закрытия в правом верхнем углу
        self.close_pdf_btn = QToolButton(self.page_viewer)
        self.close_pdf_btn.setText("✕")
        self.close_pdf_btn.setToolTip("Закрыть файл")
        self.close_pdf_btn.setFixedSize(QSize(28, 28))
        self.close_pdf_btn.setStyleSheet(
            """
            QToolButton {
                background-color: rgba(60, 60, 60, 180);
                color: white;
                border: none;
                border-radius: 14px;
                font-size: 16px;
                font-weight: bold;
            }
            QToolButton:hover {
                background-color: rgba(200, 60, 60, 220);
            }
        """
        )
        self.close_pdf_btn.clicked.connect(self._clear_interface)
        self.close_pdf_btn.hide()

        viewer_layout.addWidget(self.page_viewer)
        main_layout.addWidget(viewer_container)

        # Создаём док-панели
        self._setup_dock_panels()

    def _setup_dock_panels(self):
        """Создать все док-панели"""
        # Дерево проектов (слева)
        self.project_dock = QDockWidget("Дерево проектов", self)
        self.project_dock.setObjectName("ProjectTreeDock")
        self.project_tree_widget = ProjectTreeWidget()
        self.project_tree_widget.file_uploaded_r2.connect(
            self._on_tree_file_uploaded_r2
        )
        self.project_tree_widget.document_selected.connect(
            self._on_tree_document_selected
        )
        self.project_tree_widget.annotation_replaced.connect(
            self._on_annotation_replaced
        )
        self.project_dock.setWidget(self.project_tree_widget)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.project_dock)
        self.resizeDocks([self.project_dock], [280], Qt.Horizontal)

        # Блоки (справа)
        self.blocks_dock = QDockWidget("Блоки", self)
        self.blocks_dock.setObjectName("BlocksDock")
        blocks_widget = self._create_blocks_widget()
        self.blocks_dock.setWidget(blocks_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, self.blocks_dock)

        # Устанавливаем размер правого дока
        self.resizeDocks([self.blocks_dock], [320], Qt.Horizontal)

    def _create_blocks_widget(self) -> QWidget:
        """Создать виджет блоков"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Главные вкладки: Блоки | OCR
        self.main_right_tabs = QTabWidget()

        # === Вкладка "Блоки" ===
        blocks_container = QWidget()
        blocks_layout = QVBoxLayout(blocks_container)
        blocks_layout.setContentsMargins(4, 4, 4, 4)

        # Кнопки перемещения блоков
        move_buttons_layout = QHBoxLayout()
        self.move_block_up_btn = QPushButton("↑ Вверх")
        self.move_block_up_btn.clicked.connect(self._move_block_up)
        move_buttons_layout.addWidget(self.move_block_up_btn)

        self.move_block_down_btn = QPushButton("↓ Вниз")
        self.move_block_down_btn.clicked.connect(self._move_block_down)
        move_buttons_layout.addWidget(self.move_block_down_btn)

        blocks_layout.addLayout(move_buttons_layout)

        # Дерево блоков
        self.blocks_tree = QTreeWidget()
        self.blocks_tree.setHeaderLabels(["Название", "Тип", "Категория"])
        self.blocks_tree.setColumnWidth(0, 100)
        self.blocks_tree.setColumnWidth(1, 50)
        self.blocks_tree.setColumnWidth(2, 70)
        self.blocks_tree.setSortingEnabled(False)
        self.blocks_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.blocks_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.blocks_tree.customContextMenuRequested.connect(
            lambda pos: self.blocks_tree_manager.on_tree_context_menu(pos)
        )
        self.blocks_tree.itemClicked.connect(self._on_tree_block_clicked)
        self.blocks_tree.installEventFilter(self)

        # QSplitter: дерево блоков + inline OCR preview
        blocks_splitter = QSplitter(Qt.Vertical)
        blocks_splitter.addWidget(self.blocks_tree)

        self.ocr_preview_inline = OcrPreviewWidget()
        blocks_splitter.addWidget(self.ocr_preview_inline)
        blocks_splitter.setSizes([300, 200])

        blocks_layout.addWidget(blocks_splitter)

        self.main_right_tabs.addTab(blocks_container, "Блоки")

        # === Вкладка "OCR" ===
        self.ocr_preview = OcrPreviewWidget()
        self.main_right_tabs.addTab(self.ocr_preview, "OCR")

        # Подключаем переключение вкладок для управления Remote OCR panel
        self.main_right_tabs.currentChanged.connect(self._on_right_tab_changed)

        layout.addWidget(self.main_right_tabs)

        return widget

    def _on_right_tab_changed(self, index: int):
        """Обработка переключения вкладок правой панели"""
        if not hasattr(self, "remote_ocr_panel") or not self.remote_ocr_panel:
            return

        # Скрываем Remote OCR panel на вкладке OCR (индекс 1)
        if index == 1:  # OCR
            self.remote_ocr_panel.hide()
        else:  # Блоки
            self.remote_ocr_panel.show()

