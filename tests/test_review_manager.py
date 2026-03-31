"""Tests for review_manager module."""
import pytest
from sra_search.review_manager.reviewer import Reviewer
from sra_search.review_manager.filters import (
    ReviewFilters
)
from sra_search.review_manager import reviewer


class TestReviewer:
    """Test cases for Reviewer."""

    @pytest.fixture
    def reviewer(self):
        """Create a Reviewer instance."""
        return Reviewer()

    @pytest.fixture
    def sample_datasets(self):
        """Sample datasets for testing."""
        return [
            {
                "accession": "SRR001",
                "title": "Test RNA-Seq Dataset 1",
                "platform": "ILLUMINA",
                "status": "pending",
                "relevance_score": 0.9
            },
            {
                "accession": "SRR002",
                "title": "Test RNA-Seq Dataset 2",
                "platform": "ILLUMINA",
                "status": "approved",
                "relevance_score": 0.8
            },
            {
                "accession": "SRR003",
                "title": "Test WGS Dataset",
                "platform": "PACBIO",
                "status": "rejected",
                "relevance_score": 0.3
            }
        ]

    def test_approve_dataset(self, reviewer, sample_datasets):
        """Test approving a dataset."""
        result = reviewer.approve_dataset(sample_datasets[0], "Looks good")
        
        assert result["status"] == "approved"
        assert result["review_comment"] == "Looks good"

    def test_reject_dataset(self, reviewer, sample_datasets):
        """Test rejecting a dataset."""
        result = reviewer.reject_dataset(sample_datasets[0], "Not relevant")
        
        assert result["status"] == "rejected"
        assert result["review_comment"] == "Not relevant"

    def test_skip_dataset(self, reviewer, sample_datasets):
        """Test skipping a dataset."""
        result = reviewer.skip_dataset(sample_datasets[0])
        
        assert result["status"] == "skipped"

    def test_bulk_approve(self, reviewer, sample_datasets):
        """Test bulk approval."""
        ids = ["SRR001", "SRR002"]
        result = reviewer.bulk_approve(ids)
        
        assert result["approved_count"] == 2

    def test_bulk_reject(self, reviewer, sample_datasets):
        """Test bulk rejection."""
        ids = ["SRR001", "SRR003"]
        result = reviewer.bulk_reject(ids, "Not relevant to topic")
        
        assert result["rejected_count"] == 2


class TestReviewFilter:
    """Test cases for ReviewFilter."""

    def test_filter_by_status(self):
        """Test filtering by status."""
        filter_func = ReviewFilter.filter_by_status("approved")
        
        datasets = [
            {"status": "approved"},
            {"status": "pending"},
            {"status": "rejected"}
        ]
        
        result = filter_func(datasets)
        
        assert len(result) == 1
        assert result[0]["status"] == "approved"

    def test_filter_by_platform(self):
        """Test filtering by platform."""
        filter_func = ReviewFilter.filter_by_platform("ILLUMINA")
        
        datasets = [
            {"platform": "ILLUMINA"},
            {"platform": "PACBIO"},
            {"platform": "ILLUMINA"}
        ]
        
        result = filter_func(datasets)
        
        assert len(result) == 2

    def test_filter_by_relevance(self):
        """Test filtering by relevance score."""
        filter_func = ReviewFilter.filter_by_relevance(0.7)
        
        datasets = [
            {"relevance_score": 0.9},
            {"relevance_score": 0.5},
            {"relevance_score": 0.8}
        ]
        
        result = filter_func(datasets)
        
        assert len(result) == 2
        assert all(d["relevance_score"] >= 0.7 for d in result)


class TestStatusFilter:
    """Test cases for StatusFilter."""

    def test_approved_filter(self):
        """Test approved status filter."""
        datasets = [
            {"status": "approved"},
            {"status": "pending"},
            {"status": "rejected"}
        ]
        
        result = StatusFilter.approved(datasets)
        
        assert len(result) == 1

    def test_pending_filter(self):
        """Test pending status filter."""
        datasets = [
            {"status": "approved"},
            {"status": "pending"},
            {"status": "pending"}
        ]
        
        result = StatusFilter.pending(datasets)
        
        assert len(result) == 2

    def test_rejected_filter(self):
        """Test rejected status filter."""
        datasets = [
            {"status": "approved"},
            {"status": "rejected"}
        ]
        
        result = StatusFilter.rejected(datasets)
        
        assert len(result) == 1


class TestPlatformFilter:
    """Test cases for PlatformFilter."""

    def test_filter_by_platform(self):
        """Test platform filtering."""
        datasets = [
            {"platform": "ILLUMINA"},
            {"platform": "PACBIO"},
            {"platform": "ILLUMINA"}
        ]
        
        result = PlatformFilter.filter(datasets, "ILLUMINA")
        
        assert len(result) == 2

    def test_filter_multiple_platforms(self):
        """Test filtering with multiple platforms."""
        datasets = [
            {"platform": "ILLUMINA"},
            {"platform": "PACBIO"},
            {"platform": "ONT"}
        ]
        
        result = PlatformFilter.filter(datasets, ["ILLUMINA", "PACBIO"])
        
        assert len(result) == 2


class TestDateRangeFilter:
    """Test cases for DateRangeFilter."""

    def test_filter_by_date_range(self):
        """Test date range filtering."""
        datasets = [
            {"created_at": "2023-01-01"},
            {"created_at": "2023-06-01"},
            {"created_at": "2023-12-01"}
        ]
        
        result = DateRangeFilter.filter(
            datasets,
            start_date="2023-06-01",
            end_date="2023-12-31"
        )
        
        assert len(result) == 2


class TestRelevanceFilter:
    """Test cases for RelevanceFilter."""

    def test_filter_min_relevance(self):
        """Test minimum relevance filter."""
        datasets = [
            {"relevance_score": 0.9},
            {"relevance_score": 0.5},
            {"relevance_score": 0.8}
        ]
        
        result = RelevanceFilter.min_score(datasets, 0.7)
        
        assert len(result) == 2
        assert all(d["relevance_score"] >= 0.7 for d in result)

    def test_filter_range(self):
        """Test relevance range filter."""
        datasets = [
            {"relevance_score": 0.9},
            {"relevance_score": 0.5},
            {"relevance_score": 0.7}
        ]
        
        result = RelevanceFilter.range(datasets, 0.6, 0.8)
        
        assert len(result) == 2

    def test_sort_by_relevance(self):
        """Test sorting by relevance."""
        datasets = [
            {"relevance_score": 0.5},
            {"relevance_score": 0.9},
            {"relevance_score": 0.7}
        ]
        
        result = RelevanceFilter.sort_by_relevance(datasets, descending=True)
        
        assert result[0]["relevance_score"] == 0.9
