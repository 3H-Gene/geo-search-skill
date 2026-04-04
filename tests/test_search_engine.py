"""Tests for search_engine module."""
import pytest
from unittest.mock import AsyncMock, patch
from sra_search.search_engine.sra_searcher import SRASearcher, build_scrna_query
from sra_search.search_engine.query_builder import SmartQueryBuilder


class TestSRASearcher:
    """Test cases for SRASearcher."""

    @pytest.fixture
    def searcher(self):
        """Create an SRASearcher instance (no email arg needed)."""
        return SRASearcher()

    def test_searcher_created(self, searcher):
        """Test SRASearcher can be instantiated."""
        assert searcher is not None
        assert hasattr(searcher, "search")
        assert hasattr(searcher, "fetch_summaries")
        assert hasattr(searcher, "search_and_fetch")

    def test_parse_sra_xml_empty(self, searcher):
        """Test parsing empty XML returns empty list."""
        result = searcher._parse_sra_xml("<EXPERIMENT_PACKAGE_SET></EXPERIMENT_PACKAGE_SET>")
        assert isinstance(result, list)

    def test_parse_sra_xml_invalid(self, searcher):
        """Test parsing invalid XML doesn't crash."""
        result = searcher._parse_sra_xml("not xml at all")
        assert isinstance(result, list)


class TestBuildScRNAQuery:
    """Test cases for build_scrna_query function."""

    def test_basic_query(self):
        """Test basic query without extra filters."""
        q = build_scrna_query("gout single cell")
        assert "gout single cell" in q
        assert '"public"[Access]' in q
        assert '"has data"[Properties]' in q
        assert '"platform illumina"[Filter]' in q

    def test_with_organisms(self):
        """Test query adds organism filter."""
        q = build_scrna_query("gout single cell", organisms=["human"])
        assert '"Homo sapiens"[Organism]' in q

    def test_with_multiple_organisms(self):
        """Test query adds OR-combined organism filter."""
        q = build_scrna_query("gout single cell", organisms=["human", "mouse"])
        assert "Homo sapiens" in q
        assert "Mus musculus" in q

    def test_with_date_range(self):
        """Test query adds date range filter."""
        q = build_scrna_query("gout", min_date="2022/01/01", max_date="2024/12/31")
        assert "2022/01/01" in q
        assert "2024/12/31" in q
        assert "[PDAT]" in q

    def test_strict_scrna_excludes_smartseq(self):
        """Test strict mode adds Smart-seq exclusion."""
        q = build_scrna_query("gout", strict_scrna=True)
        assert "Smart-seq" in q
        assert "NOT" in q

    def test_no_organism_no_org_filter(self):
        """Test that without organisms, no organism filter is added."""
        q = build_scrna_query("gout")
        assert "[Organism]" not in q


class TestSmartQueryBuilder:
    """Test cases for SmartQueryBuilder."""

    def test_builder_created(self):
        """Test SmartQueryBuilder can be instantiated."""
        builder = SmartQueryBuilder()
        assert builder is not None
        assert hasattr(builder, "build_query")

    def test_build_query_returns_string(self):
        """Test build_query returns a non-empty string."""
        builder = SmartQueryBuilder()
        query, info = builder.build_query("gout single cell")
        assert isinstance(query, str)
        assert len(query) > 0

    def test_build_query_with_disease(self):
        """Test build_query with known disease."""
        builder = SmartQueryBuilder()
        query, info = builder.build_query("lung cancer RNA-seq")
        assert isinstance(query, str)
        assert len(query) > 0

    def test_build_query_classification_info(self):
        """Test build_query returns classification info dict."""
        builder = SmartQueryBuilder()
        query, info = builder.build_query("gout single cell")
        assert isinstance(info, dict)


class TestSearchEngineIntegration:
    """Integration stubs for search engine."""

    def test_keyword_generation(self):
        """Placeholder: full pipeline test."""
        pass
