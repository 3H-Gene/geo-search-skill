"""Tests for data_store module."""
import pytest
import tempfile
import shutil
from pathlib import Path
from sra_search.data_store import schema
from sra_search.data_store.database import Database
from sra_search.data_store.exporter import Exporter


class TestDatabaseSchema:
    """Test cases for DatabaseSchema."""

    def test_create_tables_sql(self):
        """Test generating CREATE TABLE statements."""
        sql = schema.CREATE_DATASETS_TABLE
        
        assert "CREATE TABLE" in sql
        assert "datasets" in sql.lower()

    def test_create_indexes_sql(self):
        """Test generating CREATE INDEX statements."""
        indexes = schema.CREATE_INDEXES
        
        assert len(indexes) > 0
        assert "INDEX" in indexes[0]

    def test_schema_tables(self):
        """Test schema table definitions."""
        # Check datasets table
        assert "CREATE_DATASETS_TABLE" in dir(schema)
        
        # Check topics table
        assert "CREATE_TOPICS_TABLE" in dir(schema)


class TestDatabase:
    """Test cases for Database."""

    @pytest.fixture
    def db_manager(self, temp_dir):
        """Create a database manager with temporary database."""
        db_path = temp_dir / "test.db"
        return Database(db_path=db_path)

    @pytest.mark.asyncio
    async def test_init_database(self, db_manager):
        """Test database initialization."""
        await db_manager.init_database()
        
        # Check tables exist
        tables = await db_manager.get_tables()
        assert "datasets" in tables
        assert "topics" in tables

    @pytest.mark.asyncio
    async def test_insert_dataset(self, db_manager):
        """Test inserting a dataset."""
        await db_manager.init_database()
        
        dataset = {
            "accession": "SRR1234567",
            "title": "Test Dataset",
            "organism": "Homo sapiens",
            "library_strategy": "RNA-Seq",
            "platform": "ILLUMINA"
        }
        
        await db_manager.insert_dataset(dataset)
        
        # Query back
        result = await db_manager.get_dataset("SRR1234567")
        assert result is not None
        assert result["accession"] == "SRR1234567"

    @pytest.mark.asyncio
    async def test_insert_topic(self, db_manager):
        """Test inserting a topic."""
        await db_manager.init_database()
        
        topic = {
            "disease": "Lung Cancer",
            "organ": "Lung",
            "omics_type": "RNA-Seq"
        }
        
        topic_id = await db_manager.insert_topic(topic)
        assert topic_id > 0

    @pytest.mark.asyncio
    async def test_insert_topic_dataset_relation(self, db_manager):
        """Test inserting topic-dataset relation."""
        await db_manager.init_database()
        
        # Insert topic
        topic_id = await db_manager.insert_topic({
            "disease": "Lung Cancer",
            "organ": "Lung",
            "omics_type": "RNA-Seq"
        })
        
        # Insert dataset
        await db_manager.insert_dataset({
            "accession": "SRR1234567",
            "title": "Test",
            "organism": "Homo sapiens"
        })
        
        # Insert relation
        await db_manager.insert_topic_dataset_relation(
            topic_id=topic_id,
            dataset_id="SRR1234567",
            relevance_score=0.95
        )
        
        # Query relations
        relations = await db_manager.get_topic_datasets(topic_id)
        assert len(relations) > 0

    @pytest.mark.asyncio
    async def test_query_datasets_by_topic(self, db_manager):
        """Test querying datasets by topic."""
        await db_manager.init_database()
        
        # Insert data
        topic_id = await db_manager.insert_topic({
            "disease": "Lung Cancer",
            "organ": "Lung",
            "omics_type": "RNA-Seq"
        })
        
        await db_manager.insert_dataset({
            "accession": "SRR1234567",
            "title": "Lung Cancer RNA-Seq",
            "organism": "Homo sapiens"
        })
        
        await db_manager.insert_topic_dataset_relation(
            topic_id=topic_id,
            dataset_id="SRR1234567",
            relevance_score=0.95
        )
        
        # Query
        datasets = await db_manager.get_datasets_by_topic(topic_id)
        assert len(datasets) > 0

    @pytest.mark.asyncio
    async def test_close(self, db_manager):
        """Test closing database."""
        await db_manager.init_database()
        await db_manager.close()
        
        # Should not raise error


class TestExporter:
    """Test cases for Exporter."""

    def test_export_to_csv(self, temp_dir):
        """Test exporting to CSV."""
        exporter = Exporter()
        
        data = [
            {"accession": "SRR1", "title": "Test 1"},
            {"accession": "SRR2", "title": "Test 2"}
        ]
        
        output_path = temp_dir / "test.csv"
        exporter.export_to_csv(data, output_path)
        
        assert output_path.exists()
        content = output_path.read_text()
        assert "SRR1" in content
        assert "SRR2" in content

    def test_export_to_json(self, temp_dir):
        """Test exporting to JSON."""
        exporter = Exporter()
        
        data = [
            {"accession": "SRR1", "title": "Test 1"},
            {"accession": "SRR2", "title": "Test 2"}
        ]
        
        output_path = temp_dir / "test.json"
        exporter.export_to_json(data, output_path)
        
        assert output_path.exists()

    def test_export_to_tsv(self, temp_dir):
        """Test exporting to TSV."""
        exporter = Exporter()
        
        data = [
            {"accession": "SRR1", "title": "Test 1"},
            {"accession": "SRR2", "title": "Test 2"}
        ]
        
        output_path = temp_dir / "test.tsv"
        exporter.export_to_tsv(data, output_path)
        
        assert output_path.exists()

    def test_export_empty_data(self, temp_dir):
        """Test exporting empty data."""
        exporter = Exporter()
        
        output_path = temp_dir / "empty.csv"
        exporter.export_to_csv([], output_path)
        
        assert output_path.exists()
