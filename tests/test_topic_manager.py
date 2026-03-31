"""Tests for topic_manager module."""
import pytest
from sra_search.topic_manager.topic import TopicParser
from sra_search.topic_manager.keyword_generator import KeywordGenerator
from sra_search.topic_manager.report_builder import ReportBuilder


class TestTopicParser:
    """Test cases for TopicParser."""

    def test_parse_disease_organ_omics(self):
        """Test parsing disease, organ, and omics from text."""
        parser = TopicParser()
        
        # Test basic parsing
        topic = parser.parse("lung cancer, lung, RNA-Seq")
        assert topic.disease == "Lung Cancer"
        assert topic.organ == "Lung"
        assert topic.omics_type == "RNA-Seq"

    def test_parse_with_synonyms(self):
        """Test parsing with synonyms."""
        parser = TopicParser()
        
        # Test synonym resolution
        topic = parser.parse("NSCLC, pulmonary, RNA seq")
        assert topic.disease is not None
        assert topic.organ is not None

    def test_parse_partial_topic(self):
        """Test parsing partial topic (missing components)."""
        parser = TopicParser()
        
        # Only disease
        topic = parser.parse("lung cancer")
        assert topic.disease == "Lung Cancer"
        assert topic.organ is None
        
        # Disease and organ
        topic = parser.parse("lung cancer, lung")
        assert topic.disease == "Lung Cancer"
        assert topic.organ == "Lung"

    def test_topic_validation(self):
        """Test topic validation."""
        parser = TopicParser()
        
        # Valid topic
        topic = parser.parse("lung cancer, lung, RNA-Seq")
        assert parser.is_valid(topic)
        
        # Invalid topic (no disease)
        topic = parser.parse(",,RNA-Seq")
        assert not parser.is_valid(topic)


class TestKeywordGenerator:
    """Test cases for KeywordGenerator."""

    def test_generate_search_keywords(self):
        """Test generating search keywords."""
        generator = KeywordGenerator()
        
        keywords = generator.generate_search_keywords(
            disease="Lung Cancer",
            organ="Lung",
            omics_type="RNA-Seq"
        )
        
        assert len(keywords) > 0
        # Should include disease-related keywords
        disease_kws = [k for k in keywords if "cancer" in k.lower() or "tumor" in k.lower()]
        assert len(disease_kws) > 0

    def test_generate_pubmed_keywords(self):
        """Test generating PubMed-specific keywords."""
        generator = KeywordGenerator()
        
        keywords = generator.generate_pubmed_keywords(
            disease="Lung Cancer",
            organ="Lung",
            omics_type="RNA-Seq"
        )
        
        assert len(keywords) > 0

    def test_generate_sra_keywords(self):
        """Test generating SRA-specific keywords."""
        generator = KeywordGenerator()
        
        keywords = generator.generate_sra_keywords(
            disease="Lung Cancer",
            organ="Lung",
            omics_type="RNA-Seq"
        )
        
        assert len(keywords) > 0

    def test_keyword_variations(self):
        """Test generating keyword variations."""
        generator = KeywordGenerator()
        
        keywords = generator.generate_search_keywords(
            disease="Lung Cancer",
            organ="Lung",
            omics_type="RNA-Seq"
        )
        
        # Should have multiple variations
        assert len(keywords) >= 3


class TestReportBuilder:
    """Test cases for ReportBuilder."""

    def test_create_summary_report(self):
        """Test creating summary report."""
        builder = ReportBuilder()
        
        datasets = [
            {"accession": "SRR123", "title": "Test 1", "platform": "ILLUMINA"},
            {"accession": "SRR456", "title": "Test 2", "platform": "ILLUMINA"}
        ]
        
        report = builder.create_summary_report(
            topic="Lung Cancer, Lung, RNA-Seq",
            datasets=datasets
        )
        
        assert "Lung Cancer" in report
        assert "SRR123" in report

    def test_create_dataset_table(self):
        """Test creating dataset table."""
        builder = ReportBuilder()
        
        datasets = [
            {"accession": "SRR123", "title": "Test 1", "platform": "ILLUMINA"},
            {"accession": "SRR456", "title": "Test 2", "platform": "ILLUMINA"}
        ]
        
        table = builder.create_dataset_table(datasets)
        
        assert "SRR123" in table
        assert "SRR456" in table

    def test_create_empty_report(self):
        """Test creating report with no datasets."""
        builder = ReportBuilder()
        
        report = builder.create_summary_report(
            topic="Lung Cancer, Lung, RNA-Seq",
            datasets=[]
        )
        
        assert "0" in report or "No datasets" in report.lower()

    def test_report_formatting(self):
        """Test report formatting."""
        builder = ReportBuilder()
        
        datasets = [{"accession": "SRR123", "title": "Test Dataset"}]
        
        report = builder.create_summary_report(
            topic="Test Topic",
            datasets=datasets
        )
        
        # Check markdown formatting
        assert "#" in report or "Test Topic" in report
