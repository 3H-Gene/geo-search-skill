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

    def test_dataset_record_creation(self):
        """Test creating a DatasetRecord with gse_id field."""
        record = DatasetRecord(
            gse_id="GSE123456",
            title="Test RNA-Seq of lung cancer",
            organism="Homo sapiens",
            platform="Illumina",
        )
        assert record.gse_id == "GSE123456"
        assert record.title == "Test RNA-Seq of lung cancer"
        assert record.organism == "Homo sapiens"

    def test_dataset_record_to_dict(self):
        """Test converting DatasetRecord to dict."""
        record = DatasetRecord(
            gse_id="GSE123456",
            title="Test RNA-Seq",
            organism="Homo sapiens",
        )
        data = record.to_db_row()
        assert data["gse_id"] == "GSE123456"

    def test_dataset_record_from_db_row(self):
        """Test creating DatasetRecord from database row."""
        row = {
            "gse_id": "GSE654321",
            "title": "Test dataset",
            "pubmed_ids": "[]",
            "sra_ids": "[]",
            "bioproject_ids": "[]",
            "organism": "Mus musculus",
            "disease": "lung cancer",
            "organ": "lung",
            "omics_type": "RNA-Seq",
            "omics_granularity": "bulk",
            "sample_count": 10,
            "platform": "Illumina",
            "publication_date": "2023-01-01",
            "journal": "Nature",
            "abstract": "Test abstract",
            "keywords": "[]",
            "first_seen_at": "2023-01-01T00:00:00",
            "last_updated": "2023-01-01T00:00:00",
            "version": 1,
            "change_log": "[]",
            "availability_status": "unverified",
            "availability_note": "",
            "availability_checked_at": "",
            "access_type": "public",
            "has_gse": True,
            "metadata_hash": "",
        }
        record = DatasetRecord.from_db_row(row)
        assert record.gse_id == "GSE654321"
        assert record.organism == "Mus musculus"
        assert record.sample_count == 10

    def test_topic_record_creation(self):
        """Test creating a TopicRecord."""
        record = TopicRecord(
            topic_id="test-topic-001",
            name="lung cancer scRNA",
            description="Lung cancer single-cell study",
        )
        assert record.topic_id == "test-topic-001"
        assert record.name == "lung cancer scRNA"
        assert record.description == "Lung cancer single-cell study"

    def test_topic_dataset_relation(self):
        """Test creating topic-dataset relation."""
        relation = TopicDatasetRelation(
            id="rel-001",
            topic_id="topic-001",
            gse_id="GSE123456",
            match_keyword="lung cancer scRNA",
            match_source="geo",
            match_score=0.95,
        )
        assert relation.topic_id == "topic-001"
        assert relation.gse_id == "GSE123456"
        assert relation.match_score == 0.95

    def test_enums(self):
        """Test enum values."""
        assert AvailabilityStatus.AVAILABLE.value == "available"
        assert AccessType.PUBLIC.value == "public"
        assert ReviewStatus.PENDING.value == "pending"
        assert OmicsGranularity.SINGLE_CELL.value == "single_cell"


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

        # Test rat
        assert normalize_organism("rat") == "Rattus norvegicus"

    def test_normalize_organ(self):
        """Test organ normalization."""
        # normalize_organ returns lowercase canonical form per the implementation
        # (it strips, lowercases, and looks up in _ORGAN_ALIASES)
        assert normalize_organ("lung") == "lung"
        assert normalize_organ("pulmonary") == "lung"
        assert normalize_organ("Lung") == "lung"
        assert normalize_organ("liver") == "liver"
        assert normalize_organ("hepatic") == "liver"
        assert normalize_organ("kidney") == "kidney"
        assert normalize_organ("brain") == "brain"

    def test_normalize_disease(self):
        """Test disease normalization."""
        # Returns lowercase per implementation
        assert normalize_disease("lung cancer") == "lung cancer"
        assert normalize_disease("Lung Cancer") == "lung cancer"
        assert normalize_disease("nsclc") == "non-small cell lung cancer"
        assert normalize_disease("breast cancer") == "breast cancer"

    def test_normalize_platform(self):
        """Test platform normalization."""
        # normalize_platform capitalizes the first letter
        assert normalize_platform("illumina") == "Illumina"
        assert normalize_platform("Illumina") == "Illumina"
        # ION TORRENT not in aliases - returns as-is (capitalized)
        assert normalize_platform("ION TORRENT") == "ION TORRENT"
        assert normalize_platform("pacbio") == "PacBio"
        assert normalize_platform("nanopore") == "Oxford Nanopore"
        assert normalize_platform("ont") == "Oxford Nanopore"

    def test_normalize_dataset_fields(self):
        """Test dataset fields normalization with named args."""
        result = normalize_dataset_fields(
            organism="human",
            disease="lung cancer",
            organ="lung",
            platform="illumina",
        )
        assert result["organism"] == "Homo sapiens"
        assert result["disease"] == "lung cancer"
        assert result["organ"] == "lung"
        assert result["platform"] == "Illumina"

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
        assert normalize_organ("") == ""
        assert normalize_disease("") == ""
        assert normalize_platform("") == ""

        # Whitespace-only
        assert normalize_organism("  ") == ""
        assert normalize_organ("  ") == ""

        # Case insensitivity for organism
        assert normalize_organism("HOMO SAPIENS") == "Homo sapiens"
        assert normalize_organism("MUS MUSCULUS") == "Mus musculus"

        # Unknown input returns as-is
        assert normalize_disease("totally unknown disease xyz") == "totally unknown disease xyz"
