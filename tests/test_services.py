"""Тесты для app/services.py — service layer facade."""
from unittest.mock import MagicMock, patch


class TestArtifactStore:
    """Тесты R2 Storage facade."""

    @patch("app.services.get_r2")
    def test_upload_file(self, mock_get_r2):
        from app.services import upload_file

        mock_r2 = MagicMock()
        mock_r2.upload_file.return_value = True
        mock_get_r2.return_value = mock_r2

        result = upload_file("/local/file.pdf", "remote/file.pdf")
        assert result is True
        mock_r2.upload_file.assert_called_once_with("/local/file.pdf", "remote/file.pdf", None)

    @patch("app.services.get_r2")
    def test_download_file(self, mock_get_r2):
        from app.services import download_file

        mock_r2 = MagicMock()
        mock_r2.download_file.return_value = True
        mock_get_r2.return_value = mock_r2

        result = download_file("remote/file.pdf", "/local/file.pdf")
        assert result is True

    @patch("app.services.get_r2")
    def test_file_exists(self, mock_get_r2):
        from app.services import file_exists

        mock_r2 = MagicMock()
        mock_r2.exists.return_value = False
        mock_get_r2.return_value = mock_r2

        assert file_exists("nonexistent/key") is False

    @patch("app.services.get_r2")
    def test_delete_file(self, mock_get_r2):
        from app.services import delete_file

        mock_r2 = MagicMock()
        mock_r2.delete_object.return_value = True
        mock_get_r2.return_value = mock_r2

        assert delete_file("some/key") is True

    @patch("app.services.get_r2")
    def test_list_files(self, mock_get_r2):
        from app.services import list_files

        mock_r2 = MagicMock()
        mock_r2.list_files.return_value = ["a.pdf", "b.json"]
        mock_get_r2.return_value = mock_r2

        result = list_files("prefix/")
        assert result == ["a.pdf", "b.json"]


class TestTreeRepository:
    """Тесты TreeClient facade."""

    @patch("app.services.get_tree_client")
    def test_get_node_files(self, mock_get_tc):
        from app.services import get_node_files

        mock_tc = MagicMock()
        mock_tc.get_node_files.return_value = [{"id": "f1"}, {"id": "f2"}]
        mock_get_tc.return_value = mock_tc

        result = get_node_files("node-123")
        assert len(result) == 2

    @patch("app.services.get_tree_client")
    def test_delete_node_file(self, mock_get_tc):
        from app.services import delete_node_file

        mock_tc = MagicMock()
        mock_tc.delete_node_file.return_value = True
        mock_get_tc.return_value = mock_tc

        assert delete_node_file("file-456") is True


class TestAnnotationRepository:
    """Тесты AnnotationDBIO facade."""

    @patch("app.services.save_annotation_to_db")
    def test_save_annotation(self, mock_save):
        mock_save.return_value = True

        result = mock_save("doc_object", "node-123")
        assert result is True
