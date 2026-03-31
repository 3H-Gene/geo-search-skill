"""Tests for metadata_extractor module."""
import pytest
from sra_search.metadata_extractor.models import (
    DatasetRecord, TopicRecord, TopicDatasetRelation,
    AvailabilityStatus, AccessType, ReviewStatus, OmicsGranularity
)
from sra_search.metadata_extractor.normalizer import (
    normalize_organism, normalize_organ, normalize_disease,
    normalize_platform, normalize_dataset_fields
)


class TestModels:
    """Test cases for data models."""

    def test_dataset_record_creation(self, sample_dataset_record):
        """Test creating a DatasetRecord."""
        record = DatasetRecord(**sample_dataset_record)
        assert record.accession == "SRR1234567"
        assert record.title == "Test RNA-Seq of lung cancer"

    def test_dataset_record_to_dict(self, sample_dataset_record):
        """Test converting DatasetRecord to dict."""
        record = DatasetRecord(**sample_dataset_record)
        data = record.to_dict()
        assert data["accession"] == "SRR1234567"

    def test_topic_record_creation(self, sample_topic):
        """Test creating a TopicRecord."""
        record = TopicRecord(
            disease=sample_topic["disease"],
            organ=sample_topic["organ"],
            omics_type=sample_topic["omics"]
        )
        assert record.disease == "Lung Cancer"
        assert record.organ == "Lung"

    def test_topic_dataset_relation(self):
        """Test creating topic-dataset relation."""
        relation = TopicDatasetRelation(
            topic_id=1,
            dataset_id="SRR1234567",
            relevance_score=0.95,
            source="pubmed"
        )
        assert relation.topic_id == 1
        assert relation.relevance_score == 0.95


class TestNormalizer:
    """Test cases for field normalization."""

    def test_normalize_organism(self):
        """Test organism normalization."""
        # Test human variations
        assert normalize_organism("human") == "Homo sapiens"
        assert normalize_organism("Human") == "Homo sapiens"
        assert normalize_organism("Homo sapiens") == "Homo sapiens"
        
        # Test mouse variations
        assert normalize_organism("mouse") == "Mus musculus"
        assert normalize_organism("Mouse") == "Mus musculus"
        assert normalize_organism("Mus musculus") == "Mus musculus"

    def test_normalize_organ(self):
        """Test organ normalization."""
        # Test lung variations
        assert normalize_organ("lung") == "Lung"
        assert normalize_organ("pulmonary") == "Lung"
        assert normalize_organ("Lung") == "Lung"
        
        # Test liver variations
        assert normalize_organ("liver") == "Liver"
        assert normalize_organ("hepatic") == "Liver"

    def test_normalize_disease(self):
        """Test disease normalization."""
        # Test cancer variations
        assert normalize_disease("lung cancer") == "Lung Cancer"
        assert normalize_disease("Lung carcinoma") == "Lung Cancer"
        assert normalize_disease("Lung Cancer") == "Lung Cancer"

    def test_normalize_platform(self):
        """Test platform normalization."""
        assert normalize_platform("illumina") == "ILLUMINA"
        assert normalize_platform("Illumina") == "ILLUMINA"
        assert normalize_platform("ION TORRENT") == "ION_TORRENT"
        assert normalize_platform("pacbio") == "PACBIO"

    def test_normalize_dataset_fields(self):
        """Test dataset fields normalization."""
        data = {"organism": "human", "organ": "lung"}
        result = normalize_dataset_fields(data)
        assert "organism" in result or "organ" in result

    def test_normalize_unknown(self):
        """Test normalization with unknown input."""
        result = normalize_organism("unknown organism")
        assert result == "unknown organism"
        
        result = normalize_organ("unknown organ")
        assert result == "unknown organ"

    def test_normalize_edge_cases(self):
        """Test edge cases in normalization."""
        # Empty string
        assert normalize_organism("") == ""
        
        # None-like input
        assert normalize_organism("  ") == ""
        
        # Case insensitivity
        assert normalize_organism("HOMO SAPIENS") == "Homo sapiens"
