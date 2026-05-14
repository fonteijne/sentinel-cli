"""Unit tests for GitLabClient."""

from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from src.config_loader import ConfigLoader
from src.gitlab_client import GitLabClient


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    config = MagicMock(spec=ConfigLoader)
    config.get_gitlab_config.return_value = {
        "base_url": "https://gitlab.com",
        "api_token": "test_token",
    }
    return config


@pytest.fixture
def gitlab_client(mock_config):
    """Create a GitLabClient instance with mocked config."""
    with patch("src.gitlab_client.get_config", return_value=mock_config):
        client = GitLabClient()
        return client


class TestGitLabClientInit:
    """Test GitLabClient initialization."""

    def test_init_success(self, mock_config):
        """Test successful initialization."""
        with patch("src.gitlab_client.get_config", return_value=mock_config):
            client = GitLabClient()
            assert client.base_url == "https://gitlab.com"
            assert client.api_token == "test_token"
            assert client.session.headers["PRIVATE-TOKEN"] == "test_token"

    def test_init_missing_config(self):
        """Test initialization with missing configuration."""
        config = MagicMock(spec=ConfigLoader)
        config.get_gitlab_config.return_value = {
            "base_url": "",
            "api_token": "",
        }

        with patch("src.gitlab_client.get_config", return_value=config):
            with pytest.raises(ValueError, match="GitLab configuration incomplete"):
                GitLabClient()

    def test_init_strips_trailing_slash(self):
        """Test that base URL trailing slashes are removed."""
        config = MagicMock(spec=ConfigLoader)
        config.get_gitlab_config.return_value = {
            "base_url": "https://gitlab.com/",
            "api_token": "test_token",
        }

        with patch("src.gitlab_client.get_config", return_value=config):
            client = GitLabClient()
            assert client.base_url == "https://gitlab.com"


class TestCreateMergeRequest:
    """Test create_merge_request method."""

    def test_create_merge_request_success(self, gitlab_client):
        """Test creating a merge request successfully."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "iid": 123,
            "web_url": "https://gitlab.com/acme/backend/-/merge_requests/123",
            "state": "opened",
            "title": "Draft: Test MR",
        }

        with patch.object(
            gitlab_client.session, "post", return_value=mock_response
        ) as mock_post:
            result = gitlab_client.create_merge_request(
                project_id="acme/backend",
                title="Test MR",
                source_branch="feature/test",
                target_branch="main",
                description="Test description",
                draft=True,
            )

            assert result["iid"] == 123
            assert result["web_url"] == "https://gitlab.com/acme/backend/-/merge_requests/123"
            assert result["state"] == "opened"
            assert result["title"] == "Draft: Test MR"

            # Verify URL encoding
            call_args = mock_post.call_args
            assert "acme%2Fbackend" in call_args[0][0]

    def test_create_merge_request_draft_prefix(self, gitlab_client):
        """Test that draft prefix is added to title."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "iid": 123,
            "web_url": "https://test.com",
            "state": "opened",
            "title": "Draft: Test",
        }

        with patch.object(
            gitlab_client.session, "post", return_value=mock_response
        ) as mock_post:
            gitlab_client.create_merge_request(
                project_id="test/project",
                title="Test",
                source_branch="feature",
                target_branch="main",
                draft=True,
            )

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["title"] == "Draft: Test"

    def test_create_merge_request_no_draft(self, gitlab_client):
        """Test creating a non-draft MR."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "iid": 123,
            "web_url": "https://test.com",
            "state": "opened",
            "title": "Test",
        }

        with patch.object(
            gitlab_client.session, "post", return_value=mock_response
        ) as mock_post:
            gitlab_client.create_merge_request(
                project_id="test/project",
                title="Test",
                source_branch="feature",
                target_branch="main",
                draft=False,
            )

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["title"] == "Test"

    def test_create_merge_request_existing_draft_prefix(self, gitlab_client):
        """Test that draft prefix is not duplicated."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "iid": 123,
            "web_url": "https://test.com",
            "state": "opened",
            "title": "Draft: Test",
        }

        with patch.object(
            gitlab_client.session, "post", return_value=mock_response
        ) as mock_post:
            gitlab_client.create_merge_request(
                project_id="test/project",
                title="Draft: Test",
                source_branch="feature",
                target_branch="main",
                draft=True,
            )

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["title"] == "Draft: Test"

    def test_create_merge_request_api_error(self, gitlab_client):
        """Test handling API errors."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

        with patch.object(gitlab_client.session, "post", return_value=mock_response):
            with pytest.raises(requests.HTTPError):
                gitlab_client.create_merge_request(
                    project_id="test/project",
                    title="Test",
                    source_branch="feature",
                    target_branch="main",
                )

    def test_create_merge_request_includes_raw(self, gitlab_client):
        """Test that raw response is included."""
        mock_data = {
            "iid": 123,
            "web_url": "https://test.com",
            "state": "opened",
            "title": "Test",
            "extra_field": "extra_value",
        }
        mock_response = Mock()
        mock_response.json.return_value = mock_data

        with patch.object(gitlab_client.session, "post", return_value=mock_response):
            result = gitlab_client.create_merge_request(
                project_id="test/project",
                title="Test",
                source_branch="feature",
                target_branch="main",
            )

            assert result["raw"] == mock_data


class TestUpdateMergeRequest:
    """Test update_merge_request method."""

    def test_update_merge_request_title(self, gitlab_client):
        """Test updating MR title."""
        mock_response = Mock()
        mock_response.json.return_value = {"title": "New Title"}

        with patch.object(
            gitlab_client.session, "put", return_value=mock_response
        ) as mock_put:
            result = gitlab_client.update_merge_request(
                project_id="test/project",
                mr_iid=123,
                title="New Title",
            )

            assert result == {"title": "New Title"}
            call_args = mock_put.call_args
            payload = call_args.kwargs["json"]
            assert payload["title"] == "New Title"
            assert "description" not in payload

    def test_update_merge_request_description(self, gitlab_client):
        """Test updating MR description."""
        mock_response = Mock()
        mock_response.json.return_value = {"description": "New Description"}

        with patch.object(
            gitlab_client.session, "put", return_value=mock_response
        ) as mock_put:
            gitlab_client.update_merge_request(
                project_id="test/project",
                mr_iid=123,
                description="New Description",
            )

            call_args = mock_put.call_args
            payload = call_args.kwargs["json"]
            assert payload["description"] == "New Description"

    def test_update_merge_request_state(self, gitlab_client):
        """Test updating MR state."""
        mock_response = Mock()
        mock_response.json.return_value = {"state": "closed"}

        with patch.object(
            gitlab_client.session, "put", return_value=mock_response
        ) as mock_put:
            gitlab_client.update_merge_request(
                project_id="test/project",
                mr_iid=123,
                state_event="close",
            )

            call_args = mock_put.call_args
            payload = call_args.kwargs["json"]
            assert payload["state_event"] == "close"

    def test_update_merge_request_all_fields(self, gitlab_client):
        """Test updating all fields at once."""
        mock_response = Mock()
        mock_response.json.return_value = {}

        with patch.object(
            gitlab_client.session, "put", return_value=mock_response
        ) as mock_put:
            gitlab_client.update_merge_request(
                project_id="test/project",
                mr_iid=123,
                title="New Title",
                description="New Description",
                state_event="close",
            )

            call_args = mock_put.call_args
            payload = call_args.kwargs["json"]
            assert payload["title"] == "New Title"
            assert payload["description"] == "New Description"
            assert payload["state_event"] == "close"


class TestGetMergeRequest:
    """Test get_merge_request method."""

    def test_get_merge_request_success(self, gitlab_client):
        """Test getting MR details successfully."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "iid": 123,
            "title": "Test MR",
            "state": "opened",
        }

        with patch.object(gitlab_client.session, "get", return_value=mock_response):
            result = gitlab_client.get_merge_request("test/project", 123)

            assert result["iid"] == 123
            assert result["title"] == "Test MR"

    def test_get_merge_request_api_error(self, gitlab_client):
        """Test handling API errors."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")

        with patch.object(gitlab_client.session, "get", return_value=mock_response):
            with pytest.raises(requests.HTTPError):
                gitlab_client.get_merge_request("test/project", 999)


class TestAddMergeRequestComment:
    """Test add_merge_request_comment method."""

    def test_add_merge_request_comment_success(self, gitlab_client):
        """Test adding a comment successfully."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 456,
            "body": "Test comment",
        }

        with patch.object(
            gitlab_client.session, "post", return_value=mock_response
        ) as mock_post:
            result = gitlab_client.add_merge_request_comment(
                project_id="test/project",
                mr_iid=123,
                body="Test comment",
            )

            assert result["id"] == 456
            assert result["body"] == "Test comment"

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["body"] == "Test comment"

    def test_add_merge_request_comment_api_error(self, gitlab_client):
        """Test handling API errors."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

        with patch.object(gitlab_client.session, "post", return_value=mock_response):
            with pytest.raises(requests.HTTPError):
                gitlab_client.add_merge_request_comment(
                    project_id="test/project",
                    mr_iid=123,
                    body="Test",
                )


class TestListMergeRequests:
    """Test list_merge_requests method."""

    def test_list_merge_requests_default(self, gitlab_client):
        """Test listing MRs with default parameters."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {"iid": 123, "title": "First MR"},
            {"iid": 124, "title": "Second MR"},
        ]

        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ) as mock_get:
            result = gitlab_client.list_merge_requests("test/project")

            assert len(result) == 2
            assert result[0]["iid"] == 123

            # Verify default state
            call_args = mock_get.call_args
            params = call_args.kwargs["params"]
            assert params["state"] == "opened"

    def test_list_merge_requests_custom_state(self, gitlab_client):
        """Test listing MRs with custom state."""
        mock_response = Mock()
        mock_response.json.return_value = []

        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ) as mock_get:
            gitlab_client.list_merge_requests("test/project", state="merged")

            call_args = mock_get.call_args
            params = call_args.kwargs["params"]
            assert params["state"] == "merged"

    def test_list_merge_requests_filter_by_branch(self, gitlab_client):
        """Test listing MRs filtered by source branch."""
        mock_response = Mock()
        mock_response.json.return_value = []

        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ) as mock_get:
            gitlab_client.list_merge_requests(
                "test/project",
                source_branch="feature/test",
            )

            call_args = mock_get.call_args
            params = call_args.kwargs["params"]
            assert params["source_branch"] == "feature/test"

    def test_list_merge_requests_empty(self, gitlab_client):
        """Test listing MRs when none exist."""
        mock_response = Mock()
        mock_response.json.return_value = []

        with patch.object(gitlab_client.session, "get", return_value=mock_response):
            result = gitlab_client.list_merge_requests("test/project")

            assert result == []


class TestGetProjectId:
    """Test get_project_id method."""

    def test_get_project_id_success(self, gitlab_client):
        """Test getting project ID successfully."""
        mock_response = Mock()
        mock_response.json.return_value = {"id": 12345}

        with patch.object(gitlab_client.session, "get", return_value=mock_response):
            result = gitlab_client.get_project_id("test/project")

            assert result == 12345

    def test_get_project_id_url_encoding(self, gitlab_client):
        """Test that project path is URL encoded."""
        mock_response = Mock()
        mock_response.json.return_value = {"id": 12345}

        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ) as mock_get:
            gitlab_client.get_project_id("test/project")

            call_args = mock_get.call_args
            url = call_args[0][0]
            assert "test%2Fproject" in url

    def test_get_project_id_api_error(self, gitlab_client):
        """Test handling API errors."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")

        with patch.object(gitlab_client.session, "get", return_value=mock_response):
            with pytest.raises(requests.HTTPError):
                gitlab_client.get_project_id("nonexistent/project")


class TestMarkAsReady:
    """Test mark_as_ready method."""

    def test_mark_as_ready_draft_prefix(self, gitlab_client):
        """Test marking a draft MR as ready."""
        # Mock get_merge_request
        get_response = Mock()
        get_response.json.return_value = {"title": "Draft: Test MR"}

        # Mock update_merge_request
        update_response = Mock()
        update_response.json.return_value = {"title": "Test MR"}

        with patch.object(gitlab_client.session, "get", return_value=get_response):
            with patch.object(
                gitlab_client.session, "put", return_value=update_response
            ) as mock_put:
                result = gitlab_client.mark_as_ready("test/project", 123)

                assert result == {"title": "Test MR"}

                # Verify the draft prefix was removed
                call_args = mock_put.call_args
                payload = call_args.kwargs["json"]
                assert payload["title"] == "Test MR"

    def test_mark_as_ready_lowercase_draft(self, gitlab_client):
        """Test removing lowercase draft prefix."""
        get_response = Mock()
        get_response.json.return_value = {"title": "draft: Test MR"}

        update_response = Mock()
        update_response.json.return_value = {"title": "Test MR"}

        with patch.object(gitlab_client.session, "get", return_value=get_response):
            with patch.object(
                gitlab_client.session, "put", return_value=update_response
            ) as mock_put:
                gitlab_client.mark_as_ready("test/project", 123)

                call_args = mock_put.call_args
                payload = call_args.kwargs["json"]
                assert payload["title"] == "Test MR"

    def test_mark_as_ready_no_draft_prefix(self, gitlab_client):
        """Test marking as ready when already not draft."""
        get_response = Mock()
        get_response.json.return_value = {"title": "Test MR"}

        update_response = Mock()
        update_response.json.return_value = {"title": "Test MR"}

        with patch.object(gitlab_client.session, "get", return_value=get_response):
            with patch.object(
                gitlab_client.session, "put", return_value=update_response
            ) as mock_put:
                result = gitlab_client.mark_as_ready("test/project", 123)

                # Title should remain unchanged
                call_args = mock_put.call_args
                payload = call_args.kwargs["json"]
                assert payload["title"] == "Test MR"


class TestMarkAsDraft:
    """Test mark_as_draft method."""

    def test_mark_as_draft_already_draft_is_noop(self, gitlab_client):
        """Test that an already-draft MR is not updated."""
        get_response = Mock()
        get_response.json.return_value = {"title": "Draft: Foo"}

        with patch.object(gitlab_client.session, "get", return_value=get_response):
            with patch.object(
                gitlab_client.session, "put"
            ) as mock_put:
                result = gitlab_client.mark_as_draft("test/project", 123)

                # update_merge_request must not be called
                mock_put.assert_not_called()
                # Returned dict has the original title
                assert result["title"] == "Draft: Foo"

    def test_mark_as_draft_already_draft_case_insensitive(self, gitlab_client):
        """Test idempotence with lowercase 'draft:' prefix."""
        get_response = Mock()
        get_response.json.return_value = {"title": "draft: foo"}

        with patch.object(gitlab_client.session, "get", return_value=get_response):
            with patch.object(
                gitlab_client.session, "put"
            ) as mock_put:
                result = gitlab_client.mark_as_draft("test/project", 123)

                mock_put.assert_not_called()
                assert result["title"] == "draft: foo"

    def test_mark_as_draft_adds_prefix_when_ready(self, gitlab_client):
        """Test that the draft prefix is added to a ready MR."""
        get_response = Mock()
        get_response.json.return_value = {"title": "Foo Bar"}

        update_response = Mock()
        update_response.json.return_value = {"title": "Draft: Foo Bar"}

        with patch.object(gitlab_client.session, "get", return_value=get_response):
            with patch.object(
                gitlab_client.session, "put", return_value=update_response
            ) as mock_put:
                gitlab_client.mark_as_draft("test/project", 123)

                call_args = mock_put.call_args
                payload = call_args.kwargs["json"]
                assert payload["title"] == "Draft: Foo Bar"

    def test_mark_as_draft_preserves_unicode_title(self, gitlab_client):
        """Test that unicode characters survive the prefix addition."""
        get_response = Mock()
        get_response.json.return_value = {"title": "Add 日本語 support"}

        update_response = Mock()
        update_response.json.return_value = {"title": "Draft: Add 日本語 support"}

        with patch.object(gitlab_client.session, "get", return_value=get_response):
            with patch.object(
                gitlab_client.session, "put", return_value=update_response
            ) as mock_put:
                gitlab_client.mark_as_draft("test/project", 123)

                call_args = mock_put.call_args
                payload = call_args.kwargs["json"]
                assert payload["title"] == "Draft: Add 日本語 support"


# ---------------------------------------------------------------------------
# Phase 3A: list_merged_mrs_since + list_pipelines_for_commit
# ---------------------------------------------------------------------------


class TestListMergedMrsSince:
    """Phase 3A — merged-MR pagination + URL encoding + error propagation."""

    def test_paginates_via_x_total_pages_header(self, gitlab_client):
        """X-Total-Pages=2 → walk page=1 then page=2; results concatenated."""
        page1 = Mock()
        page1.json.return_value = [{"iid": 1, "updated_at": "2026-01-01T00:00:00Z"}]
        page1.headers = {"X-Total-Pages": "2"}

        page2 = Mock()
        page2.json.return_value = [{"iid": 2, "updated_at": "2026-01-02T00:00:00Z"}]
        page2.headers = {"X-Total-Pages": "2"}

        with patch.object(
            gitlab_client.session, "get", side_effect=[page1, page2]
        ) as mock_get:
            result = gitlab_client.list_merged_mrs_since(
                "acme/backend", updated_after="2026-01-01T00:00:00Z"
            )

        assert len(result) == 2
        assert [mr["iid"] for mr in result] == [1, 2]
        assert mock_get.call_count == 2
        # First call is page=1, second is page=2.
        assert mock_get.call_args_list[0].kwargs["params"]["page"] == 1
        assert mock_get.call_args_list[1].kwargs["params"]["page"] == 2

    def test_falls_back_to_short_page_when_header_missing(self, gitlab_client):
        """No X-Total-Pages → loop terminates when batch < per_page."""
        # Page 1 returns exactly per_page=2 rows → must loop again.
        page1 = Mock()
        page1.json.return_value = [{"iid": 1}, {"iid": 2}]
        page1.headers = {}  # no X-Total-Pages

        # Page 2 returns 1 row (< per_page) → loop terminates.
        page2 = Mock()
        page2.json.return_value = [{"iid": 3}]
        page2.headers = {}

        with patch.object(
            gitlab_client.session, "get", side_effect=[page1, page2]
        ) as mock_get:
            result = gitlab_client.list_merged_mrs_since(
                "acme/backend",
                updated_after="2026-01-01T00:00:00Z",
                per_page=2,
            )

        assert [mr["iid"] for mr in result] == [1, 2, 3]
        assert mock_get.call_count == 2

    def test_url_encoding_and_required_params(self, gitlab_client):
        """URL encodes project path; params include the canonical query set."""
        mock_response = Mock()
        mock_response.json.return_value = []  # empty page → loop terminates
        mock_response.headers = {"X-Total-Pages": "1"}

        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ) as mock_get:
            gitlab_client.list_merged_mrs_since(
                "acme/backend",
                updated_after="2026-05-01T00:00:00Z",
                per_page=50,
            )

        # URL contains percent-encoded project path.
        url = mock_get.call_args.args[0]
        assert "acme%2Fbackend" in url

        params = mock_get.call_args.kwargs["params"]
        assert params["state"] == "merged"
        assert params["order_by"] == "updated_at"
        assert params["sort"] == "asc"
        assert params["updated_after"] == "2026-05-01T00:00:00Z"
        assert params["per_page"] == 50
        assert params["page"] == 1

    def test_raise_for_status_propagates(self, gitlab_client):
        """An HTTP error from the underlying session must propagate."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500")
        mock_response.headers = {}

        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ):
            with pytest.raises(requests.HTTPError):
                gitlab_client.list_merged_mrs_since(
                    "acme/backend", updated_after="2026-01-01T00:00:00Z"
                )


class TestListPipelinesForCommit:
    """Phase 3A — single-shot pipeline lookup for a merge commit."""

    def test_basic_call(self, gitlab_client):
        """Single GET; returns the parsed JSON list verbatim."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {"id": 99, "status": "failed", "ref": "main"},
            {"id": 100, "status": "success", "ref": "main"},
        ]

        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ) as mock_get:
            result = gitlab_client.list_pipelines_for_commit(
                "acme/backend", sha="abc123"
            )

        assert mock_get.call_count == 1
        assert len(result) == 2
        assert result[0]["id"] == 99

    def test_url_encoding_and_params(self, gitlab_client):
        """URL is %2F-encoded; ref defaults to 'main' and is overridable."""
        mock_response = Mock()
        mock_response.json.return_value = []

        # Default ref='main'.
        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ) as mock_get:
            gitlab_client.list_pipelines_for_commit("acme/backend", sha="deadbeef")

        url = mock_get.call_args.args[0]
        assert "acme%2Fbackend" in url
        params = mock_get.call_args.kwargs["params"]
        assert params["sha"] == "deadbeef"
        assert params["ref"] == "main"

        # Override ref.
        with patch.object(
            gitlab_client.session, "get", return_value=mock_response
        ) as mock_get2:
            gitlab_client.list_pipelines_for_commit(
                "acme/backend", sha="deadbeef", ref="release/2026.05"
            )
        assert mock_get2.call_args.kwargs["params"]["ref"] == "release/2026.05"
