"""Tests for topic_manager module."""
import pytest
from sra_search.topic_manager.topic import TopicParser, TopicDefinition
from sra_search.topic_manager.keyword_generator import KeywordGenerator


class TestTopicParser:
    """Test cases for TopicParser."""

    def test_parse_returns_topic_definition(self):
        """Test that parse() returns a TopicDefinition object."""
        parser = TopicParser()
        topic = parser.parse("lung cancer scRNA-seq")

        assert isinstance(topic, TopicDefinition)
        assert topic.name == "lung cancer scRNA-seq"

    def test_parse_with_disease_and_organ(self):
        """Test parsing disease and organ from text."""
        parser = TopicParser()
        topic = parser.parse("lung cancer, lung, RNA-Seq")

        # diseases, organs are lists (may be empty if KG not loaded)
        assert isinstance(topic.diseases, list)
        assert isinstance(topic.organs, list)
        assert isinstance(topic.omics_types, list)
        assert isinstance(topic.species, list)

    def test_parse_partial_topic(self):
        """Test parsing partial topic (missing components)."""
        parser = TopicParser()

        # Only disease text
        topic = parser.parse("lung cancer")
        assert topic.name == "lung cancer"
        assert isinstance(topic.diseases, list)

    def test_topic_definition_fields(self):
        """Test TopicDefinition has all required fields."""
        parser = TopicParser()
        topic = parser.parse("test topic")

        # Required fields exist
        assert hasattr(topic, "topic_id")
        assert hasattr(topic, "name")
        assert hasattr(topic, "description")
        assert hasattr(topic, "diseases")
        assert hasattr(topic, "organs")
        assert hasattr(topic, "omics_types")
        assert hasattr(topic, "species")
        assert hasattr(topic, "keywords_used")
        assert hasattr(topic, "extra_keywords")

        # Fields are lists
        assert isinstance(topic.diseases, list)
        assert isinstance(topic.organs, list)
        assert isinstance(topic.omics_types, list)
        assert isinstance(topic.species, list)

    def test_parse_from_dimensions(self):
        """Test creating topic from explicit dimensions."""
        parser = TopicParser()

        topic = parser.parse_from_dimensions(
            name="bladder cancer study",
            diseases=["bladder cancer"],
            organs=["urinary bladder"],
            omics_types=["scRNA-seq", "ATAC-seq"],
            species=["Homo sapiens"],
            description="Bladder cancer single-cell study",
        )

        assert topic.name == "bladder cancer study"
        assert "bladder cancer" in topic.diseases
        assert "urinary bladder" in topic.organs
        assert "scRNA-seq" in topic.omics_types
        assert topic.description == "Bladder cancer single-cell study"


class TestKeywordGenerator:
    """Test cases for KeywordGenerator."""

    def test_generate_requires_topic_definition(self):
        """Test that generate() accepts a TopicDefinition."""
        generator = KeywordGenerator()

        topic = TopicDefinition(
            topic_id="test-001",
            name="test topic",
            description="test",
            diseases=["lung cancer"],
            organs=["lung"],
            omics_types=["scRNA-seq"],
            species=["Homo sapiens"],
        )

        result = generator.generate(topic, max_queries=10)
        assert isinstance(result, list)
        assert len(result) > 0

        # Each result is a (keyword, weight) tuple
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
            keyword, weight = item
            assert isinstance(keyword, str)
            assert isinstance(weight, float)

    def test_generate_disease_omics_combinations(self):
        """Test that disease x omics combinations are generated."""
        generator = KeywordGenerator()

        topic = TopicDefinition(
            topic_id="test-002",
            name="cancer study",
            description="",
            diseases=["lung cancer"],
            organs=[],
            omics_types=["RNA-Seq"],
            species=["Homo sapiens"],
        )

        result = generator.generate(topic)
        keywords = [kw for kw, _ in result]

        # Should include disease-related terms
        disease_terms = [kw for kw in keywords if "cancer" in kw.lower() or "lung" in kw.lower()]
        assert len(disease_terms) > 0

    def test_generate_respects_max_queries(self):
        """Test that max_queries limit is respected."""
        generator = KeywordGenerator()

        topic = TopicDefinition(
            topic_id="test-003",
            name="large topic",
            description="",
            diseases=["lung cancer", "breast cancer", "colorectal cancer"],
            organs=["lung", "breast", "colon"],
            omics_types=["scRNA-seq", "ATAC-seq", "bulk RNA-seq"],
            species=["Homo sapiens"],
        )

        result = generator.generate(topic, max_queries=5)
        assert len(result) <= 5

    def test_generate_empty_topic(self):
        """Test generating keywords for a minimal topic."""
        generator = KeywordGenerator()

        topic = TopicDefinition(
            topic_id="test-004",
            name="minimal",
            description="",
            diseases=[],
            organs=[],
            omics_types=[],
            species=[],
        )

        # Should still produce some default results
        result = generator.generate(topic)
        assert isinstance(result, list)

    def test_keyword_weights_sorted(self):
        """Test that keywords are sorted by weight descending."""
        generator = KeywordGenerator()

        topic = TopicDefinition(
            topic_id="test-005",
            name="weighted topic",
            description="",
            diseases=["lung cancer"],
            organs=["lung"],
            omics_types=["scRNA-seq"],
            species=["Homo sapiens"],
        )

        result = generator.generate(topic)
        weights = [w for _, w in result]

        # Weights should be in descending order
        for i in range(len(weights) - 1):
            assert weights[i] >= weights[i + 1]

    def test_default_omics(self):
        """Test that default omics types are applied."""
        generator = KeywordGenerator()

        assert len(generator.default_omics) > 0
        assert "scRNA-seq" in generator.default_omics
        assert "bulk RNA-seq" in generator.default_omics

    def test_weights_config(self):
        """Test that weight configuration exists."""
        generator = KeywordGenerator()

        assert "disease_omics" in generator.weights
        assert "organ_omics" in generator.weights
        assert generator.weights["disease_omics"] > generator.weights["omics_only"]
