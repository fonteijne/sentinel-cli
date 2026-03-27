"""Tests for the `sentinel info --attachment` CLI flow."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

from src.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def base_ticket():
    """Minimal ticket data returned by get_ticket."""
    return {
        "summary": "Implement feature X",
        "status": "In Progress",
        "assignee": "alice",
        "type": "Story",
        "priority": "High",
        "description": "Build the thing.",
        "attachments": [],
    }


SAMPLE_ATTACHMENTS = [
    {"filename": "spec.md", "size": 2048, "mimeType": "text/markdown", "id": "101", "content": "https://jira.example.com/att/101"},
    {"filename": "screenshot.png", "size": 524288, "mimeType": "image/png", "id": "102", "content": "https://jira.example.com/att/102"},
    {"filename": "data.zip", "size": 1048576, "mimeType": "application/zip", "id": "103", "content": "https://jira.example.com/att/103"},
]


# ── Listing attachments ──────────────────────────────────────────────

@patch("src.cli.get_jira_client")
def test_info_lists_attachments(mock_get_jira, runner, base_ticket):
    """Attachments section is shown when ticket has attachments."""
    base_ticket["attachments"] = SAMPLE_ATTACHMENTS
    mock_get_jira.return_value.get_ticket.return_value = base_ticket

    result = runner.invoke(cli, ["info", "ACME-123"])

    assert result.exit_code == 0
    assert "Attachments (3)" in result.output
    assert "spec.md" in result.output
    assert "screenshot.png" in result.output
    assert "data.zip" in result.output


@patch("src.cli.get_jira_client")
def test_info_no_attachments_section_when_empty(mock_get_jira, runner, base_ticket):
    """Attachments section is omitted when ticket has none."""
    mock_get_jira.return_value.get_ticket.return_value = base_ticket

    result = runner.invoke(cli, ["info", "ACME-123"])

    assert result.exit_code == 0
    assert "Attachments" not in result.output


# ── Viewing a specific text attachment ───────────────────────────────

@patch("src.cli.get_jira_client")
def test_info_attachment_shows_text_content(mock_get_jira, runner, base_ticket):
    """--attachment <filename> downloads and displays text content inline."""
    base_ticket["attachments"] = SAMPLE_ATTACHMENTS
    mock_client = mock_get_jira.return_value
    mock_client.get_ticket.return_value = base_ticket
    mock_client.base_url = "https://jira.example.com"
    mock_client.session = MagicMock()

    download_result = {
        "text_attachments": [{"filename": "spec.md", "content": "# Feature Spec\nDetails here."}],
        "image_attachments": [],
        "skipped": [],
    }

    with patch("src.attachment_manager.AttachmentManager") as MockMgr:
        mgr_instance = MockMgr.return_value
        mgr_instance.classify.return_value = "text"
        mgr_instance.download_attachments.return_value = download_result

        result = runner.invoke(cli, ["info", "ACME-123", "--attachment", "spec.md"])

    assert result.exit_code == 0
    assert "spec.md" in result.output
    assert "# Feature Spec" in result.output
    assert "Details here." in result.output


@patch("src.cli.get_jira_client")
def test_info_attachment_image_noted(mock_get_jira, runner, base_ticket):
    """--attachment for an image file shows a message that it cannot be displayed."""
    base_ticket["attachments"] = SAMPLE_ATTACHMENTS
    mock_client = mock_get_jira.return_value
    mock_client.get_ticket.return_value = base_ticket
    mock_client.base_url = "https://jira.example.com"
    mock_client.session = MagicMock()

    download_result = {
        "text_attachments": [],
        "image_attachments": [{"filename": "screenshot.png", "path": "/tmp/screenshot.png"}],
        "skipped": [],
    }

    with patch("src.attachment_manager.AttachmentManager") as MockMgr:
        mgr_instance = MockMgr.return_value
        mgr_instance.classify.return_value = "image"
        mgr_instance.download_attachments.return_value = download_result

        result = runner.invoke(cli, ["info", "ACME-123", "--attachment", "screenshot.png"])

    assert result.exit_code == 0
    assert "screenshot.png" in result.output
    assert "cannot display" in result.output.lower() or "Image file" in result.output


# ── --attachment all ─────────────────────────────────────────────────

@patch("src.cli.get_jira_client")
def test_info_attachment_all_shows_all_text(mock_get_jira, runner, base_ticket):
    """--attachment all downloads and shows all text attachments."""
    base_ticket["attachments"] = SAMPLE_ATTACHMENTS
    mock_client = mock_get_jira.return_value
    mock_client.get_ticket.return_value = base_ticket
    mock_client.base_url = "https://jira.example.com"
    mock_client.session = MagicMock()

    download_result = {
        "text_attachments": [{"filename": "spec.md", "content": "The spec content"}],
        "image_attachments": [],
        "skipped": [],
    }

    def classify_side_effect(filename, mime):
        if filename.endswith(".md"):
            return "text"
        if filename.endswith(".png"):
            return "image"
        return "skip"

    with patch("src.attachment_manager.AttachmentManager") as MockMgr:
        mgr_instance = MockMgr.return_value
        mgr_instance.classify.side_effect = classify_side_effect
        mgr_instance.download_attachments.return_value = download_result

        result = runner.invoke(cli, ["info", "ACME-123", "--attachment", "all"])

    assert result.exit_code == 0
    assert "The spec content" in result.output
    # Only text attachments passed to download
    call_args = mgr_instance.download_attachments.call_args
    downloaded = call_args[0][1]  # second positional arg = targets
    assert len(downloaded) == 1
    assert downloaded[0]["filename"] == "spec.md"


@patch("src.cli.get_jira_client")
def test_info_attachment_all_no_text(mock_get_jira, runner, base_ticket):
    """--attachment all with no text attachments shows warning."""
    base_ticket["attachments"] = [SAMPLE_ATTACHMENTS[1]]  # only image
    mock_client = mock_get_jira.return_value
    mock_client.get_ticket.return_value = base_ticket
    mock_client.base_url = "https://jira.example.com"

    with patch("src.attachment_manager.AttachmentManager") as MockMgr:
        mgr_instance = MockMgr.return_value
        mgr_instance.classify.return_value = "image"

        result = runner.invoke(cli, ["info", "ACME-123", "--attachment", "all"])

    assert result.exit_code == 0
    assert "No text attachments" in result.output


# ── Attachment not found ─────────────────────────────────────────────

@patch("src.cli.get_jira_client")
def test_info_attachment_not_found_exits_1(mock_get_jira, runner, base_ticket):
    """--attachment with non-existent filename exits with error and lists available."""
    base_ticket["attachments"] = SAMPLE_ATTACHMENTS
    mock_client = mock_get_jira.return_value
    mock_client.get_ticket.return_value = base_ticket

    result = runner.invoke(cli, ["info", "ACME-123", "--attachment", "missing.txt"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()
    assert "spec.md" in result.output  # lists available files


# ── No attachments on ticket ─────────────────────────────────────────

@patch("src.cli.get_jira_client")
def test_info_attachment_flag_but_no_attachments(mock_get_jira, runner, base_ticket):
    """--attachment flag when ticket has no attachments shows warning."""
    mock_get_jira.return_value.get_ticket.return_value = base_ticket

    result = runner.invoke(cli, ["info", "ACME-123", "--attachment", "spec.md"])

    assert result.exit_code == 0
    assert "no attachments" in result.output.lower()


# ── Skipped attachments ──────────────────────────────────────────────

@patch("src.cli.get_jira_client")
def test_info_attachment_skipped_shown(mock_get_jira, runner, base_ticket):
    """Skipped attachments show reason in output."""
    base_ticket["attachments"] = SAMPLE_ATTACHMENTS
    mock_client = mock_get_jira.return_value
    mock_client.get_ticket.return_value = base_ticket
    mock_client.base_url = "https://jira.example.com"
    mock_client.session = MagicMock()

    download_result = {
        "text_attachments": [],
        "image_attachments": [],
        "skipped": [{"filename": "spec.md", "reason": "Auth proxy detected"}],
    }

    with patch("src.attachment_manager.AttachmentManager") as MockMgr:
        mgr_instance = MockMgr.return_value
        mgr_instance.classify.return_value = "text"
        mgr_instance.download_attachments.return_value = download_result

        result = runner.invoke(cli, ["info", "ACME-123", "--attachment", "spec.md"])

    assert result.exit_code == 0
    assert "Auth proxy detected" in result.output


# ── Size formatting ──────────────────────────────────────────────────

@patch("src.cli.get_jira_client")
def test_info_attachment_size_formatting_kb(mock_get_jira, runner, base_ticket):
    """Small attachments show size in KB."""
    base_ticket["attachments"] = [
        {"filename": "small.txt", "size": 5120, "mimeType": "text/plain"},
    ]
    mock_get_jira.return_value.get_ticket.return_value = base_ticket

    result = runner.invoke(cli, ["info", "ACME-123"])

    assert result.exit_code == 0
    assert "5.0 KB" in result.output


@patch("src.cli.get_jira_client")
def test_info_attachment_size_formatting_mb(mock_get_jira, runner, base_ticket):
    """Large attachments show size in MB."""
    base_ticket["attachments"] = [
        {"filename": "big.zip", "size": 5242880, "mimeType": "application/zip"},
    ]
    mock_get_jira.return_value.get_ticket.return_value = base_ticket

    result = runner.invoke(cli, ["info", "ACME-123"])

    assert result.exit_code == 0
    assert "5.0 MB" in result.output
