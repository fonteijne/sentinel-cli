"""Unit tests for AttachmentManager."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.attachment_manager import AttachmentManager
from src.config_loader import ConfigLoader


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    config = MagicMock(spec=ConfigLoader)
    config.get.side_effect = lambda key, default=None: {
        "attachments.enabled": True,
        "attachments.max_text_size": 102400,
        "attachments.max_image_size": 5242880,
        "attachments.max_total_size": 20971520,
        "attachments.max_count": 20,
    }.get(key, default)
    return config


@pytest.fixture
def manager(mock_config):
    """Create an AttachmentManager with mocked config."""
    with patch("src.attachment_manager.get_config", return_value=mock_config):
        return AttachmentManager()


@pytest.fixture
def tmp_worktree(tmp_path):
    """Create a temporary worktree directory."""
    return tmp_path


class TestClassify:
    """Test attachment classification."""

    def test_text_by_extension(self, manager):
        assert manager.classify("readme.md") == "text"
        assert manager.classify("script.py") == "text"
        assert manager.classify("data.json") == "text"
        assert manager.classify("config.yaml") == "text"
        assert manager.classify("query.sql") == "text"
        assert manager.classify("notes.txt") == "text"
        assert manager.classify("style.css") == "text"

    def test_image_by_extension(self, manager):
        assert manager.classify("screenshot.png") == "image"
        assert manager.classify("photo.jpg") == "image"
        assert manager.classify("photo.jpeg") == "image"
        assert manager.classify("animation.gif") == "image"
        assert manager.classify("icon.webp") == "image"

    def test_skip_binary(self, manager):
        assert manager.classify("archive.zip") == "skip"
        assert manager.classify("document.pdf") == "skip"
        assert manager.classify("binary.exe") == "skip"
        assert manager.classify("data.xlsx") == "skip"

    def test_text_by_mime_fallback(self, manager):
        assert manager.classify("unknown_file", "text/plain") == "text"
        assert manager.classify("unknown_file", "application/json") == "text"

    def test_image_by_mime_fallback(self, manager):
        assert manager.classify("unknown_file", "image/png") == "image"
        assert manager.classify("unknown_file", "image/jpeg") == "image"

    def test_skip_unknown(self, manager):
        assert manager.classify("unknown_file") == "skip"
        assert manager.classify("unknown_file", "application/octet-stream") == "skip"

    def test_extension_takes_precedence(self, manager):
        """Extension should be checked before MIME type."""
        assert manager.classify("code.py", "application/octet-stream") == "text"
        assert manager.classify("image.png", "application/octet-stream") == "image"


class TestDownloadAttachments:
    """Test attachment downloading."""

    def test_empty_attachments(self, manager, tmp_worktree):
        session = Mock()
        result = manager.download_attachments(session, [], "TEST-1", tmp_worktree)
        assert result["text_attachments"] == []
        assert result["image_attachments"] == []
        assert result["skipped"] == []

    def test_disabled_returns_empty(self, mock_config, tmp_worktree):
        mock_config.get.side_effect = lambda key, default=None: {
            "attachments.enabled": False,
        }.get(key, default)

        with patch("src.attachment_manager.get_config", return_value=mock_config):
            mgr = AttachmentManager()

        session = Mock()
        attachments = [{"filename": "test.txt", "content": "http://example.com/test.txt",
                        "mimeType": "text/plain", "size": 100}]
        result = mgr.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert result["text_attachments"] == []

    def test_download_text_file(self, manager, tmp_worktree):
        session = Mock()
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"hello world"]
        session.get.return_value = response

        attachments = [{
            "filename": "notes.txt",
            "content": "http://jira.example.com/attachments/notes.txt",
            "mimeType": "text/plain",
            "size": 11,
        }]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)

        assert len(result["text_attachments"]) == 1
        assert result["text_attachments"][0]["filename"] == "notes.txt"
        assert result["text_attachments"][0]["content"] == "hello world"

    def test_download_image_file(self, manager, tmp_worktree):
        session = Mock()
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"\x89PNG\r\n"]
        session.get.return_value = response

        attachments = [{
            "filename": "screenshot.png",
            "content": "http://jira.example.com/attachments/screenshot.png",
            "mimeType": "image/png",
            "size": 6,
        }]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)

        assert len(result["image_attachments"]) == 1
        assert result["image_attachments"][0]["filename"] == "screenshot.png"
        assert Path(result["image_attachments"][0]["path"]).exists()

    def test_skip_unsupported_type(self, manager, tmp_worktree):
        session = Mock()
        attachments = [{
            "filename": "archive.zip",
            "content": "http://jira.example.com/attachments/archive.zip",
            "mimeType": "application/zip",
            "size": 1000,
        }]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)

        assert len(result["skipped"]) == 1
        assert "Unsupported type" in result["skipped"][0]["reason"]

    def test_skip_text_too_large(self, mock_config, tmp_worktree):
        mock_config.get.side_effect = lambda key, default=None: {
            "attachments.enabled": True,
            "attachments.max_text_size": 100,
            "attachments.max_image_size": 5242880,
            "attachments.max_total_size": 20971520,
            "attachments.max_count": 20,
        }.get(key, default)

        with patch("src.attachment_manager.get_config", return_value=mock_config):
            mgr = AttachmentManager()

        session = Mock()
        attachments = [{
            "filename": "big.txt",
            "content": "http://jira.example.com/big.txt",
            "mimeType": "text/plain",
            "size": 200,
        }]

        result = mgr.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["skipped"]) == 1
        assert "too large" in result["skipped"][0]["reason"]

    def test_skip_image_too_large(self, mock_config, tmp_worktree):
        mock_config.get.side_effect = lambda key, default=None: {
            "attachments.enabled": True,
            "attachments.max_text_size": 102400,
            "attachments.max_image_size": 100,
            "attachments.max_total_size": 20971520,
            "attachments.max_count": 20,
        }.get(key, default)

        with patch("src.attachment_manager.get_config", return_value=mock_config):
            mgr = AttachmentManager()

        session = Mock()
        attachments = [{
            "filename": "huge.png",
            "content": "http://jira.example.com/huge.png",
            "mimeType": "image/png",
            "size": 200,
        }]

        result = mgr.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["skipped"]) == 1
        assert "too large" in result["skipped"][0]["reason"]

    def test_max_count_limit(self, mock_config, tmp_worktree):
        mock_config.get.side_effect = lambda key, default=None: {
            "attachments.enabled": True,
            "attachments.max_text_size": 102400,
            "attachments.max_image_size": 5242880,
            "attachments.max_total_size": 20971520,
            "attachments.max_count": 2,
        }.get(key, default)

        with patch("src.attachment_manager.get_config", return_value=mock_config):
            mgr = AttachmentManager()

        session = Mock()
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"data"]
        session.get.return_value = response

        attachments = [
            {"filename": f"file{i}.txt", "content": f"http://jira.example.com/file{i}.txt",
             "mimeType": "text/plain", "size": 4}
            for i in range(5)
        ]

        result = mgr.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["text_attachments"]) == 2
        assert len(result["skipped"]) == 3

    def test_total_size_limit(self, mock_config, tmp_worktree):
        mock_config.get.side_effect = lambda key, default=None: {
            "attachments.enabled": True,
            "attachments.max_text_size": 102400,
            "attachments.max_image_size": 5242880,
            "attachments.max_total_size": 10,
            "attachments.max_count": 20,
        }.get(key, default)

        with patch("src.attachment_manager.get_config", return_value=mock_config):
            mgr = AttachmentManager()

        session = Mock()
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"12345"]
        session.get.return_value = response

        attachments = [
            {"filename": "a.txt", "content": "http://example.com/a.txt",
             "mimeType": "text/plain", "size": 5},
            {"filename": "b.txt", "content": "http://example.com/b.txt",
             "mimeType": "text/plain", "size": 5},
            {"filename": "c.txt", "content": "http://example.com/c.txt",
             "mimeType": "text/plain", "size": 5},
        ]

        result = mgr.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["text_attachments"]) == 2
        assert len(result["skipped"]) == 1
        assert "total size limit" in result["skipped"][0]["reason"]

    def test_cache_skips_redownload(self, manager, tmp_worktree):
        """Existing file with same size should not be re-downloaded."""
        session = Mock()

        # Pre-create the file
        attach_dir = tmp_worktree / ".agents" / "attachments" / "TEST-1"
        attach_dir.mkdir(parents=True)
        cached_file = attach_dir / "cached.txt"
        cached_file.write_text("hello")

        attachments = [{
            "filename": "cached.txt",
            "content": "http://jira.example.com/cached.txt",
            "mimeType": "text/plain",
            "size": 5,
        }]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)

        # Should NOT have called session.get for download
        session.get.assert_not_called()
        assert len(result["text_attachments"]) == 1
        assert result["text_attachments"][0]["content"] == "hello"

    def test_uses_rest_api_url_when_base_url_provided(self, manager, tmp_worktree):
        """When base_url is provided, should use REST API endpoint to bypass auth proxies."""
        session = Mock()
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"file data"]
        session.get.return_value = response

        attachments = [{
            "id": "14879",
            "filename": "spec.txt",
            "content": "https://jira.example.com/secure/attachment/14879/spec.txt",
            "mimeType": "text/plain",
            "size": 9,
        }]

        manager.download_attachments(
            session, attachments, "TEST-1", tmp_worktree,
            base_url="https://jira.example.com",
        )

        call_url = session.get.call_args[0][0]
        assert call_url == "https://jira.example.com/rest/api/2/attachment/14879/content"

    def test_uses_content_url_without_base_url(self, manager, tmp_worktree):
        """Without base_url, should use the direct content URL."""
        session = Mock()
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"file data"]
        session.get.return_value = response

        attachments = [{
            "id": "14879",
            "filename": "spec.txt",
            "content": "https://jira.cloud.com/attachments/spec.txt",
            "mimeType": "text/plain",
            "size": 9,
        }]

        manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)

        call_url = session.get.call_args[0][0]
        assert call_url == "https://jira.cloud.com/attachments/spec.txt"

    def test_no_content_url_skipped(self, manager, tmp_worktree):
        session = Mock()
        attachments = [{"filename": "broken.txt", "mimeType": "text/plain", "size": 10}]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["skipped"]) == 1
        assert "No download URL" in result["skipped"][0]["reason"]

    def test_download_failure_skipped(self, manager, tmp_worktree):
        session = Mock()
        session.get.side_effect = Exception("Connection refused")

        attachments = [{
            "filename": "fail.txt",
            "content": "http://jira.example.com/fail.txt",
            "mimeType": "text/plain",
            "size": 10,
        }]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["skipped"]) == 1
        assert "Connection refused" in result["skipped"][0]["reason"]

    def test_auth_proxy_html_response_for_image(self, manager, tmp_worktree):
        """Image download returning HTML (auth proxy redirect) should be skipped."""
        session = Mock()
        response = Mock()
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.iter_content.return_value = [b"<html>Login</html>"]
        response.raise_for_status = Mock()
        session.get.return_value = response

        attachments = [{
            "filename": "screenshot.png",
            "content": "http://jira.example.com/secure/attachment/123/screenshot.png",
            "mimeType": "image/png",
            "size": 5000,
        }]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["skipped"]) == 1
        assert "Auth proxy blocked download" in result["skipped"][0]["reason"]
        assert "View in browser" in result["skipped"][0]["reason"]

    def test_auth_proxy_html_response_for_text(self, manager, tmp_worktree):
        """Text download returning HTML login page should be skipped."""
        session = Mock()
        response = Mock()
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.iter_content.return_value = [b"<!DOCTYPE html><html><body>Please log in</body></html>"]
        response.raise_for_status = Mock()
        session.get.return_value = response

        attachments = [{
            "filename": "notes.txt",
            "content": "http://jira.example.com/secure/attachment/456/notes.txt",
            "mimeType": "text/plain",
            "size": 100,
        }]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["skipped"]) == 1
        assert "Auth proxy blocked download" in result["skipped"][0]["reason"]

    def test_legitimate_text_not_blocked(self, manager, tmp_worktree):
        """Normal text file starting with non-HTML content should download fine."""
        session = Mock()
        response = Mock()
        response.headers = {"Content-Type": "text/plain"}
        response.iter_content.return_value = [b"# My Markdown\nHello"]
        session.get.return_value = response

        attachments = [{
            "filename": "readme.md",
            "content": "http://jira.example.com/secure/attachment/789/readme.md",
            "mimeType": "text/plain",
            "size": 20,
        }]

        result = manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert len(result["text_attachments"]) == 1
        assert result["text_attachments"][0]["content"] == "# My Markdown\nHello"

    def test_creates_attachment_directory(self, manager, tmp_worktree):
        session = Mock()
        response = Mock()
        response.headers = {}
        response.iter_content.return_value = [b"data"]
        session.get.return_value = response

        attachments = [{
            "filename": "test.txt",
            "content": "http://jira.example.com/test.txt",
            "mimeType": "text/plain",
            "size": 4,
        }]

        manager.download_attachments(session, attachments, "TEST-1", tmp_worktree)
        assert (tmp_worktree / ".agents" / "attachments" / "TEST-1").is_dir()


class TestFormatForPrompt:
    """Test prompt formatting."""

    def test_empty_attachments(self, manager):
        result = manager.format_for_prompt({
            "text_attachments": [],
            "image_attachments": [],
            "skipped": [],
        })
        assert result == ""

    def test_text_attachment_inline(self, manager):
        data = {
            "text_attachments": [{
                "filename": "spec.md",
                "content": "# Feature Spec\nDo the thing.",
                "mime_type": "text/markdown",
                "path": "/tmp/spec.md",
            }],
            "image_attachments": [],
            "skipped": [],
        }

        result = manager.format_for_prompt(data)
        assert "spec.md" in result
        assert "# Feature Spec" in result
        assert "```" in result

    def test_image_attachment_reference(self, manager):
        data = {
            "text_attachments": [],
            "image_attachments": [{
                "filename": "mockup.png",
                "mime_type": "image/png",
                "path": "/tmp/attachments/mockup.png",
            }],
            "skipped": [],
        }

        result = manager.format_for_prompt(data)
        assert "mockup.png" in result
        assert "/tmp/attachments/mockup.png" in result
        assert "Read tool" in result

    def test_skipped_attachments_noted(self, manager):
        data = {
            "text_attachments": [{"filename": "a.txt", "content": "x", "mime_type": "text/plain", "path": "/a.txt"}],
            "image_attachments": [],
            "skipped": [{"filename": "big.zip", "reason": "Unsupported type"}],
        }

        result = manager.format_for_prompt(data)
        assert "big.zip" in result
        assert "Unsupported type" in result

    def test_mixed_attachments(self, manager):
        data = {
            "text_attachments": [
                {"filename": "readme.md", "content": "# Hello", "mime_type": "text/markdown", "path": "/readme.md"},
            ],
            "image_attachments": [
                {"filename": "ui.png", "mime_type": "image/png", "path": "/ui.png"},
            ],
            "skipped": [],
        }

        result = manager.format_for_prompt(data)
        assert "readme.md" in result
        assert "ui.png" in result
        assert "## Ticket Attachments" in result


class TestFormatMetadataOnly:
    """Test metadata-only formatting."""

    def test_empty_metadata(self, manager):
        assert manager.format_metadata_only([]) == ""

    def test_lists_files(self, manager):
        metadata = [
            {"filename": "spec.md", "size": 2048, "mimeType": "text/markdown"},
            {"filename": "mockup.png", "size": 512000, "mimeType": "image/png"},
        ]

        result = manager.format_metadata_only(metadata)
        assert "spec.md" in result
        assert "mockup.png" in result
        assert "metadata only" in result


class TestCleanup:
    """Test attachment cleanup."""

    def test_cleanup_removes_directory(self, tmp_path):
        attach_dir = tmp_path / ".agents" / "attachments" / "TEST-1"
        attach_dir.mkdir(parents=True)
        (attach_dir / "file.txt").write_text("data")

        AttachmentManager.cleanup("TEST-1", tmp_path)
        assert not attach_dir.exists()

    def test_cleanup_nonexistent_is_noop(self, tmp_path):
        """Cleaning up when no directory exists should not raise."""
        AttachmentManager.cleanup("TEST-999", tmp_path)

    def test_cleanup_preserves_other_tickets(self, tmp_path):
        attach_base = tmp_path / ".agents" / "attachments"
        (attach_base / "TEST-1").mkdir(parents=True)
        (attach_base / "TEST-2").mkdir(parents=True)
        (attach_base / "TEST-1" / "file.txt").write_text("data")
        (attach_base / "TEST-2" / "file.txt").write_text("data")

        AttachmentManager.cleanup("TEST-1", tmp_path)
        assert not (attach_base / "TEST-1").exists()
        assert (attach_base / "TEST-2").exists()
