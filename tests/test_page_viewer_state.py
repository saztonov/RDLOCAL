"""Тесты для PageViewer state machine."""

from app.gui.page_viewer_state import DRAG_STATES, EDITING_STATES, ViewerState


class TestViewerState:
    """Тесты для ViewerState enum."""

    def test_idle_is_default(self):
        state = ViewerState.IDLE
        assert state == ViewerState.IDLE

    def test_all_states_defined(self):
        expected = {
            "IDLE", "DRAWING_RECT", "DRAWING_POLYGON", "SELECTING",
            "MOVING_BLOCK", "RESIZING_BLOCK", "PANNING",
            "DRAGGING_POLYGON_VERTEX", "DRAGGING_POLYGON_EDGE",
            "RIGHT_BUTTON_DOWN",
        }
        actual = {s.name for s in ViewerState}
        assert actual == expected

    def test_states_are_unique(self):
        values = [s.value for s in ViewerState]
        assert len(values) == len(set(values))

    def test_drag_states_are_subset(self):
        for state in DRAG_STATES:
            assert isinstance(state, ViewerState)
        # IDLE and DRAWING_POLYGON should NOT be in drag states
        assert ViewerState.IDLE not in DRAG_STATES
        assert ViewerState.DRAWING_POLYGON not in DRAG_STATES

    def test_editing_states_are_subset(self):
        for state in EDITING_STATES:
            assert isinstance(state, ViewerState)
        # IDLE, PANNING, SELECTING should NOT be in editing states
        assert ViewerState.IDLE not in EDITING_STATES
        assert ViewerState.PANNING not in EDITING_STATES
        assert ViewerState.SELECTING not in EDITING_STATES

    def test_mutually_exclusive(self):
        """Невозможные комбинации из старых boolean: drawing + moving_block."""
        # С enum — состояние всегда одно
        state = ViewerState.DRAWING_RECT
        assert state != ViewerState.MOVING_BLOCK
        assert state != ViewerState.RESIZING_BLOCK


class TestMainWindowState:
    """Тесты для MainWindowState dataclass."""

    def test_default_values(self):
        from app.gui.main_window_state import MainWindowState

        state = MainWindowState()
        assert state.pdf_document is None
        assert state.annotation_document is None
        assert state.current_page == 0
        assert state.current_pdf_path is None
        assert state.current_node_id is None
        assert state.current_node_locked is False
        assert state.page_images == {}
        assert state.undo_stack == []
        assert state.redo_stack == []
        assert state.blocks_clipboard == []

    def test_has_document(self):
        from app.gui.main_window_state import MainWindowState

        state = MainWindowState()
        assert not state.has_document
        assert not state.has_annotation

        state.pdf_document = "dummy"
        assert state.has_document
        assert not state.has_annotation

        state.annotation_document = "dummy_ann"
        assert state.has_annotation

    def test_can_undo_redo(self):
        from app.gui.main_window_state import MainWindowState

        state = MainWindowState()
        assert not state.can_undo
        assert not state.can_redo

        state.undo_stack.append(("page", "data"))
        assert state.can_undo

        state.redo_stack.append(("page", "data"))
        assert state.can_redo

    def test_reset(self):
        from app.gui.main_window_state import MainWindowState

        state = MainWindowState()
        state.pdf_document = "doc"
        state.annotation_document = "ann"
        state.current_page = 5
        state.current_pdf_path = "/test.pdf"
        state.undo_stack.append("item")
        state.page_images["key"] = "value"

        state.reset()

        assert state.pdf_document is None
        assert state.annotation_document is None
        assert state.current_page == 0
        assert state.current_pdf_path is None
        assert state.undo_stack == []
        assert state.page_images == {}
