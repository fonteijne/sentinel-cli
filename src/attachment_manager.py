"""Attachment manager for downloading and processing Jira ticket attachments."""

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List

import requests

from src.config_loader import get_config

logger = logging.getLogger(__name__)

# File extensions considered as text
TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".rb", ".php", ".c", ".cpp", ".h", ".hpp",
    ".css", ".scss", ".less", ".html", ".xml", ".svg",
    ".json", ".yaml", ".yml", ".toml", ".csv", ".sql",
    ".sh", ".bash", ".zsh", ".bat", ".ps1",
    ".conf", ".cfg", ".ini", ".env", ".log",
    ".dockerfile", ".makefile", ".gitignore",
}

# File extensions considered as images (Claude-supported formats)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# MIME type fallbacks
IMAGE_MIME_PREFIXES = ("image/png", "image/jpeg", "image/gif", "image/webp")
TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml", "application/yaml")

# Default size limits
DEFAULT_MAX_TEXT_SIZE = 102400  # 100KB
DEFAULT_MAX_IMAGE_SIZE = 5242880  # 5MB
DEFAULT_MAX_TOTAL_SIZE = 20971520  # 20MB
DEFAULT_MAX_COUNT = 20


class AttachmentManager:
    """Manages downloading, classifying, and formatting Jira ticket attachments."""

    def __init__(self) -> None:
        """Initialize attachment manager with config-based limits."""
        config = get_config()
        self.enabled = config.get("attachments.enabled", True)
        self.max_text_size = config.get("attachments.max_text_size", DEFAULT_MAX_TEXT_SIZE)
        self.max_image_size = config.get("attachments.max_image_size", DEFAULT_MAX_IMAGE_SIZE)
        self.max_total_size = config.get("attachments.max_total_size", DEFAULT_MAX_TOTAL_SIZE)
        self.max_count = config.get("attachments.max_count", DEFAULT_MAX_COUNT)

    def classify(self, filename: str, mime_type: str = "") -> str:
        """Classify an attachment as text, image, or skip.

        Args:
            filename: Attachment filename
            mime_type: MIME type from Jira API

        Returns:
            "text", "image", or "skip"
        """
        ext = Path(filename).suffix.lower()

        if ext in TEXT_EXTENSIONS:
            return "text"
        if ext in IMAGE_EXTENSIONS:
            return "image"

        # Fallback to MIME type
        mime_lower = mime_type.lower()
        if mime_lower.startswith(IMAGE_MIME_PREFIXES):
            return "image"
        if mime_lower.startswith(TEXT_MIME_PREFIXES):
            return "text"

        return "skip"

    def download_attachments(
        self,
        session: requests.Session,
        attachments_metadata: List[Dict[str, Any]],
        ticket_id: str,
        worktree_path: Path,
        base_url: str = "",
    ) -> Dict[str, Any]:
        """Download and process attachments from a Jira ticket.

        When base_url is provided (self-hosted Jira), downloads via the REST
        API endpoint to bypass auth proxies on /secure/ paths. Otherwise
        uses the direct content URL from the attachment metadata.

        Args:
            session: Authenticated requests session from Jira client
            attachments_metadata: List of attachment dicts from Jira API
            ticket_id: Ticket ID for directory naming
            worktree_path: Path to worktree root
            base_url: Jira base URL. When provided, downloads via REST API
                endpoint /rest/api/2/attachment/{id}/content instead of the
                direct content URL.

        Returns:
            Dictionary with:
                - text_attachments: list of {filename, content, mime_type, path}
                - image_attachments: list of {filename, mime_type, path}
                - skipped: list of {filename, reason}
        """
        result: Dict[str, Any] = {
            "text_attachments": [],
            "image_attachments": [],
            "skipped": [],
        }

        if not self.enabled or not attachments_metadata:
            return result

        # Limit number of attachments
        to_process = attachments_metadata[:self.max_count]
        if len(attachments_metadata) > self.max_count:
            for att in attachments_metadata[self.max_count:]:
                result["skipped"].append({
                    "filename": att.get("filename", "unknown"),
                    "reason": f"Exceeded max attachment count ({self.max_count})",
                })

        # Create attachment directory
        attach_dir = worktree_path / ".agents" / "attachments" / ticket_id
        attach_dir.mkdir(parents=True, exist_ok=True)

        total_downloaded = 0

        for attachment in to_process:
            filename = attachment.get("filename", "unknown")
            mime_type = attachment.get("mimeType", "")
            size = attachment.get("size", 0)
            attachment_id = attachment.get("id", "")

            # Use REST API endpoint when base_url provided (bypasses auth proxies)
            # The /secure/attachment/ URLs require browser-based cookie auth on
            # self-hosted Jira behind reverse proxies, but REST API accepts PAT
            if base_url and attachment_id:
                content_url = f"{base_url}/rest/api/2/attachment/{attachment_id}/content"
            else:
                content_url = attachment.get("content", "")

            if not content_url:
                result["skipped"].append({"filename": filename, "reason": "No download URL"})
                continue

            classification = self.classify(filename, mime_type)

            if classification == "skip":
                result["skipped"].append({
                    "filename": filename,
                    "reason": f"Unsupported type: {mime_type or Path(filename).suffix}",
                })
                continue

            # Check size limits
            if classification == "text" and size > self.max_text_size:
                result["skipped"].append({
                    "filename": filename,
                    "reason": f"Text file too large ({size} bytes, max {self.max_text_size})",
                })
                continue

            if classification == "image" and size > self.max_image_size:
                result["skipped"].append({
                    "filename": filename,
                    "reason": f"Image too large ({size} bytes, max {self.max_image_size})",
                })
                continue

            # Check total size limit
            if total_downloaded + size > self.max_total_size:
                result["skipped"].append({
                    "filename": filename,
                    "reason": f"Would exceed total size limit ({self.max_total_size} bytes)",
                })
                continue

            # Check cache — skip download if file exists with same size
            file_path = attach_dir / filename
            if file_path.exists() and file_path.stat().st_size == size:
                logger.debug(f"Cached: {filename}")
            else:
                # Download — use X-Atlassian-Token header for Jira Server
                try:
                    headers = {"X-Atlassian-Token": "no-check"}
                    response = session.get(
                        content_url, stream=True, headers=headers,
                    )
                    response.raise_for_status()

                    # Detect auth proxy redirect — if response is HTML when
                    # we expected a different type, the proxy intercepted it
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" in content_type and classification != "text":
                        result["skipped"].append({
                            "filename": filename,
                            "reason": f"Auth proxy blocked download. View in browser: {content_url}",
                        })
                        continue

                    # Stream to file, checking first chunk for HTML login pages
                    proxy_blocked = False
                    with open(file_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if not proxy_blocked and chunk:
                                stripped = chunk.lstrip()[:15].lower()
                                if stripped.startswith((b"<!doctype", b"<html")):
                                    proxy_blocked = True
                                    break
                            f.write(chunk)

                    if proxy_blocked:
                        file_path.unlink(missing_ok=True)
                        result["skipped"].append({
                            "filename": filename,
                            "reason": f"Auth proxy blocked download. View in browser: {content_url}",
                        })
                        continue

                    logger.info(f"Downloaded: {filename} ({size} bytes)")
                except Exception as e:
                    logger.warning(f"Failed to download {filename}: {e}")
                    result["skipped"].append({"filename": filename, "reason": str(e)})
                    continue

            total_downloaded += size

            if classification == "text":
                # Read text content
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    logger.warning(f"Failed to read {filename}: {e}")
                    result["skipped"].append({"filename": filename, "reason": str(e)})
                    continue

                result["text_attachments"].append({
                    "filename": filename,
                    "content": content,
                    "mime_type": mime_type,
                    "path": str(file_path),
                })
            elif classification == "image":
                result["image_attachments"].append({
                    "filename": filename,
                    "mime_type": mime_type,
                    "path": str(file_path),
                })

        return result

    def format_for_prompt(self, attachments_data: Dict[str, Any]) -> str:
        """Format downloaded attachments as context for an agent prompt.

        Args:
            attachments_data: Result from download_attachments()

        Returns:
            Formatted string to append to agent prompt
        """
        parts: List[str] = []

        text_attachments = attachments_data.get("text_attachments", [])
        image_attachments = attachments_data.get("image_attachments", [])
        skipped = attachments_data.get("skipped", [])

        if not text_attachments and not image_attachments:
            return ""

        parts.append("\n## Ticket Attachments\n")

        # Text attachments — inline content
        for att in text_attachments:
            filename = att["filename"]
            content = att["content"]
            parts.append(f"### File: {filename}")
            parts.append(f"```\n{content}\n```\n")

        # Image attachments — reference paths for Read tool
        if image_attachments:
            parts.append("### Images")
            parts.append("The following images are attached to this ticket. "
                         "Use the Read tool to view them:")
            for att in image_attachments:
                parts.append(f"- `{att['path']}` ({att['filename']})")
            parts.append("")

        # Note skipped files
        if skipped:
            parts.append("### Skipped Attachments")
            for s in skipped:
                parts.append(f"- {s['filename']}: {s['reason']}")
            parts.append("")

        return "\n".join(parts)

    def format_metadata_only(self, attachments_metadata: List[Dict[str, Any]]) -> str:
        """Format attachment metadata when download is not possible.

        Args:
            attachments_metadata: Raw attachment metadata from Jira API

        Returns:
            Formatted string listing attachment names and sizes
        """
        if not attachments_metadata:
            return ""

        parts = ["\n## Ticket Attachments (metadata only, not downloaded)\n"]
        for att in attachments_metadata:
            filename = att.get("filename", "unknown")
            size = att.get("size", 0)
            mime_type = att.get("mimeType", "")
            size_kb = size / 1024
            parts.append(f"- {filename} ({size_kb:.1f} KB, {mime_type})")

        return "\n".join(parts)

    @staticmethod
    def cleanup(ticket_id: str, worktree_path: Path) -> None:
        """Remove downloaded attachments for a ticket.

        Args:
            ticket_id: Ticket ID
            worktree_path: Path to worktree root
        """
        attach_dir = worktree_path / ".agents" / "attachments" / ticket_id
        if attach_dir.exists():
            shutil.rmtree(attach_dir)
            logger.info(f"Cleaned up attachments for {ticket_id}")
