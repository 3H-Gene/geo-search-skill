"""Tests for data_store module."""
import pytest
import tempfile
import shutil
from pathlib import Path
from sra_search.data_store import schema
from sra_search.data_store.database import Database, reset_database
from sra_search.metadata_extractor.models import DatasetRecord, TopicRecord, TopicDatasetRelation


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
        assert "CREATE_DATASETS_TABLE" in dir(schema)
        assert "CREATE_TOPICS_TABLE" in dir(schema)

    def test_all_tables_list(self):
        """Test ALL_TABLES contains multiple tables."""
        assert len(schema.ALL_TABLES) >= 4  # datasets, topics, topic_datasets, search_history...


class TestDatabase:
    """Test cases for Database (sync CRUD API)."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a fresh in-memory-like database in a temp directory."""
        reset_database()
        db_path = tmp_path / "test.db"
        database = Database(db_path=str(db_path))
        # Trigger connection + table init
        database.get_connection()
        yield database
        database.close()
        reset_database()

    def test_connection_created(self, db):
        """Test that database connection is established."""
        conn = db.get_connection()
        assert conn is not None

    def test_tables_initialized(self, db):
        """Test that required tables are created on init."""
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "datasets" in tables
        assert "topics" in tables
        assert "topic_datasets" in tables

    def test_get_dataset_not_found(self, db):
        """Test that get_dataset returns None for unknown ID."""
        result = db.get_dataset("GSE_NONEXISTENT")
        assert result is None

    def test_list_datasets_empty(self, db):
        """Test listing datasets on empty db."""
        results = db.list_datasets()
        assert results == []

    def test_get_topic_not_found(self, db):
        """Test that get_topic returns None for unknown ID."""
        result = db.get_topic("TOPIC_NONEXISTENT")
        assert result is None

    def test_list_topics_empty(self, db):
        """Test listing topics on empty db."""
        results = db.list_topics()
        assert results == []

    def test_count_datasets_empty(self, db):
        """Test count on empty db."""
        count = db.count_datasets()
        assert count == 0

    def test_close(self, db):
        """Test closing database connection doesn't raise."""
        db.close()
        # Second close should also be safe
        db.close()

    @pytest.mark.asyncio
    async def test_upsert_dataset_and_retrieve(self, db):
        """Test inserting a dataset via write queue and reading back."""
        record = DatasetRecord(gse_id="GSE123456", title="Test scRNA-seq Dataset")
        await db.start_write_queue()
        await db.upsert_dataset(record)
        await db.stop_write_queue()

        result = db.get_dataset("GSE123456")
        assert result is not None
        assert result.gse_id == "GSE123456"
        assert result.title == "Test scRNA-seq Dataset"

    @pytest.mark.asyncio
    async def test_insert_topic_and_retrieve(self, db):
        """Test inserting a topic via write queue and reading back."""
        topic = TopicRecord(topic_id="test-topic-001", name="Lung Cancer scRNA-seq", description="Test topic")
        await db.start_write_queue()
        await db.insert_topic(topic)
        await db.stop_write_queue()

        result = db.get_topic_by_name("Lung Cancer scRNA-seq")
        assert result is not None
        assert result.name == "Lung Cancer scRNA-seq"


class TestDatabaseSchema2:
    """Additional schema content checks."""

    def test_datasets_table_has_required_columns(self):
        """Test datasets table includes key fields."""
        sql = schema.CREATE_DATASETS_TABLE
        for col in ["gse_id", "title", "organism", "omics_type", "sample_count"]:
            assert col in sql

    def test_topics_table_has_required_columns(self):
        """Test topics table includes key fields."""
        sql = schema.CREATE_TOPICS_TABLE
        for col in ["topic_id", "name"]:
            assert col in sql
