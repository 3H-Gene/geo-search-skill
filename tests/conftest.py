"""Test configuration and fixtures for SRA_search tests."""
import pytest
import tempfile
import shutil
from pathlib import Path


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def sample_dataset_record():
    """Sample dataset record for testing."""
    return {
        "accession": "SRR1234567",
        "title": "Test RNA-Seq of lung cancer",
        "organism": "Homo sapiens",
        "library_strategy": "RNA-Seq",
        "platform": "ILLUMINA",
        "bioproject": "PRJNA123456",
        "sra_metadata": {
            "run_id": "SRR1234567",
            "sample_id": "SRS123456",
            "experiment_id": "SRX123456",
            "study_id": "SRP123456"
        }
    }


@pytest.fixture
def sample_pubmed_record():
    """Sample PubMed record for testing."""
    return {
        "pmid": "12345678",
        "title": "Test publication on lung cancer",
        "abstract": "This is a test abstract about lung cancer.",
        "authors": ["Doe J", "Smith A"],
        "journal": "Test Journal",
        "pubdate": "2023",
        "mesh_terms": ["Lung Neoplasms", "RNA-Seq"]
    }


@pytest.fixture
def sample_topic():
    """Sample topic for testing."""
    return {
        "disease": "Lung Cancer",
        "organ": "Lung",
        "omics": "RNA-Seq"
    }


@pytest.fixture
def ontologies_dir():
    """Path to ontologies directory."""
    return Path(__file__).parent.parent / "data" / "ontologies"
