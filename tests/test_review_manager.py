"""Tests for review_manager module."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from sra_search.review_manager.reviewer import Reviewer, ReviewResult, VALID_STATUSES, VALID_TRANSITIONS
from sra_search.review_manager.filters import ReviewFilters


class TestReviewerInit:
    """Test cases for Reviewer initialization."""

    def test_reviewer_requires_db(self):
        """Test that Reviewer requires a database instance."""
        mock_db = MagicMock()
        reviewer = Reviewer(db=mock_db)
        assert reviewer.db is mock_db


class TestReviewerMethods:
    """Test cases for Reviewer API (async methods require mocking)."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database for testing."""
        db = MagicMock()
        db.get_connection.return_value = MagicMock()
        return db

    @pytest.fixture
    def reviewer(self, mock_db):
        """Create a Reviewer with mock database."""
        return Reviewer(db=mock_db)

    def test_reviewer_has_mark_method(self, reviewer):
        """Test that Reviewer has an async mark() method."""
        assert hasattr(reviewer, "mark")
        assert callable(reviewer.mark)

    def test_reviewer_has_approve_method(self, reviewer):
        """Test that Reviewer has an async approve() method."""
        assert hasattr(reviewer, "approve")
        assert callable(reviewer.approve)

    def test_reviewer_has_delete_method(self, reviewer):
        """Test that Reviewer has an async delete() method."""
        assert hasattr(reviewer, "delete")
        assert callable(reviewer.delete)

    def test_reviewer_has_batch_mark_method(self, reviewer):
        """Test that Reviewer has an async batch_mark() method."""
        assert hasattr(reviewer, "batch_mark")
        assert callable(reviewer.batch_mark)

    def test_reviewer_has_undo_method(self, reviewer):
        """Test that Reviewer has an async undo() method."""
        assert hasattr(reviewer, "undo")
        assert callable(reviewer.undo)

    def test_review_result_dataclass(self):
        """Test ReviewResult dataclass fields."""
        result = ReviewResult(
            topic_id="topic-001",
            gse_id="GSE123456",
            action="approve",
            old_status="pending",
            new_status="approved",
            note="Looks good",
            success=True,
        )
        assert result.topic_id == "topic-001"
        assert result.gse_id == "GSE123456"
        assert result.action == "approve"
        assert result.old_status == "pending"
        assert result.new_status == "approved"
        assert result.success is True

    def test_review_result_failure(self):
        """Test ReviewResult with failure."""
        result = ReviewResult(
            topic_id="topic-001",
            gse_id="GSE123456",
            action="approve",
            old_status="pending",
            new_status="approved",
            note="",
            success=False,
            error="Dataset not found",
        )
        assert result.success is False
        assert result.error == "Dataset not found"


class TestReviewerAsync:
    """Test Reviewer async methods with mocked async database."""

    @pytest.fixture
    def mock_db_async(self):
        """Create an async-compatible mock database."""
        db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        db.get_connection.return_value = mock_conn
        # These are async methods called by Reviewer
        db.update_review = AsyncMock(return_value=None)
        db.insert_review_log = AsyncMock(return_value=None)
        return db, mock_conn, mock_cursor

    @pytest.mark.asyncio
    async def test_approve_success(self, mock_db_async):
        """Test successful approval of a dataset."""
        db, conn, cursor = mock_db_async
        cursor.fetchone.return_value = {"review_status": "pending"}

        reviewer = Reviewer(db=db)
        result = await reviewer.approve(gse_id="GSE123456", topic_id="topic-001", note="Good")

        assert result.success is True
        assert result.action == "approve"

    @pytest.mark.asyncio
    async def test_mark_invalid_status(self, mock_db_async):
        """Test marking with invalid status returns failure."""
        db, conn, cursor = mock_db_async

        reviewer = Reviewer(db=db)
        result = await reviewer.mark(
            gse_id="GSE123456",
            topic_id="topic-001",
            status="invalid_status",
        )

        assert result.success is False
        assert "Invalid status" in result.error

    @pytest.mark.asyncio
    async def test_delete_dataset(self, mock_db_async):
        """Test deleting a dataset from topic."""
        db, conn, cursor = mock_db_async
        cursor.fetchone.return_value = {"review_status": "approved"}

        reviewer = Reviewer(db=db)
        result = await reviewer.delete(gse_id="GSE123456", topic_id="topic-001", note="Remove")

        assert result.success is True
        assert result.action == "delete"

    @pytest.mark.asyncio
    async def test_undo_no_log(self, mock_db_async):
        """Test undo when no review log exists."""
        db, conn, cursor = mock_db_async
        cursor.fetchone.return_value = None

        reviewer = Reviewer(db=db)
        result = await reviewer.undo(gse_id="GSE123456", topic_id="topic-001")

        assert result.success is False
        assert "No review log found" in result.error


class TestValidStatuses:
    """Test valid status constants."""

    def test_valid_statuses(self):
        """Test that valid statuses are defined."""
        assert "pending" in VALID_STATUSES
        assert "approved" in VALID_STATUSES
        assert "irrelevant" in VALID_STATUSES
        assert "deleted" in VALID_STATUSES

    def test_valid_transitions_from_pending(self):
        """Test valid transitions from pending status."""
        assert "approved" in VALID_TRANSITIONS["pending"]
        assert "irrelevant" in VALID_TRANSITIONS["pending"]
        assert "deleted" in VALID_TRANSITIONS["pending"]

    def test_valid_transitions_from_approved(self):
        """Test valid transitions from approved status."""
        assert "irrelevant" in VALID_TRANSITIONS["approved"]
        assert "deleted" in VALID_TRANSITIONS["approved"]


class TestReviewFilters:
    """Test cases for ReviewFilters."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        db.get_connection.return_value = mock_conn
        return db, mock_conn, mock_cursor

    def test_review_filters_init(self, mock_db):
        """Test ReviewFilters requires a database."""
        db, _, _ = mock_db
        filters = ReviewFilters(db=db)
        assert filters.db is db

    def test_review_filters_has_get_pending(self, mock_db):
        """Test ReviewFilters has get_pending method."""
        db, _, _ = mock_db
        filters = ReviewFilters(db=db)
        assert hasattr(filters, "get_pending")
        assert callable(filters.get_pending)

    def test_review_filters_has_get_by_status(self, mock_db):
        """Test ReviewFilters has get_by_status method."""
        db, _, _ = mock_db
        filters = ReviewFilters(db=db)
        assert hasattr(filters, "get_by_status")
        assert callable(filters.get_by_status)

    def test_review_filters_has_get_review_summary(self, mock_db):
        """Test ReviewFilters has get_review_summary method."""
        db, _, _ = mock_db
        filters = ReviewFilters(db=db)
        assert hasattr(filters, "get_review_summary")
        assert callable(filters.get_review_summary)

    def test_review_filters_has_get_unreviewed_count(self, mock_db):
        """Test ReviewFilters has get_unreviewed_count method."""
        db, _, _ = mock_db
        filters = ReviewFilters(db=db)
        assert hasattr(filters, "get_unreviewed_count")
        assert callable(filters.get_unreviewed_count)

    def test_review_filters_has_get_review_log(self, mock_db):
        """Test ReviewFilters has get_review_log method."""
        db, _, _ = mock_db
        filters = ReviewFilters(db=db)
        assert hasattr(filters, "get_review_log")
        assert callable(filters.get_review_log)

    def test_get_review_summary_returns_dict(self, mock_db):
        """Test get_review_summary returns expected dict structure."""
        db, conn, cursor = mock_db
        cursor.fetchall.return_value = [
            {"review_status": "pending", "cnt": 5},
            {"review_status": "approved", "cnt": 3},
        ]

        filters = ReviewFilters(db=db)
        result = filters.get_review_summary()

        assert "total" in result
        assert "pending" in result
        assert "approved" in result
        assert "irrelevant" in result
        assert "deleted" in result
        assert result["pending"] == 5
        assert result["approved"] == 3
        assert result["total"] == 8

    def test_get_unreviewed_count(self, mock_db):
        """Test get_unreviewed_count returns pending count."""
        db, conn, cursor = mock_db
        cursor.fetchall.return_value = [
            {"review_status": "pending", "cnt": 10},
        ]

        filters = ReviewFilters(db=db)
        count = filters.get_unreviewed_count()
        assert count == 10

    def test_get_review_log(self, mock_db):
        """Test get_review_log returns list of log entries."""
        db, conn, cursor = mock_db
        cursor.fetchall.return_value = [
            {
                "id": "log-001",
                "topic_id": "topic-001",
                "gse_id": "GSE123456",
                "action": "approve",
                "old_status": "pending",
                "new_status": "approved",
                "note": "Looks good",
                "acted_at": "2024-01-01T00:00:00",
            }
        ]

        filters = ReviewFilters(db=db)
        result = filters.get_review_log(topic_id="topic-001", limit=20)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["gse_id"] == "GSE123456"
