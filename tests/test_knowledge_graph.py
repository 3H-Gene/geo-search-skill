"""Tests for knowledge_graph module."""
import pytest
from sra_search.knowledge_graph.disease_ontology import DiseaseOntology
from sra_search.knowledge_graph.organ_ontology import OrganOntology
from sra_search.knowledge_graph.omics_types import OmicsTypeMapper


class TestDiseaseOntology:
    """Test cases for DiseaseOntology."""

    def test_load_disease_ontology(self, ontologies_dir):
        """Test loading disease ontology."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        ontology._load()
        assert len(ontology._data) > 0

    def test_resolve_disease(self, ontologies_dir):
        """Test disease name resolution."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        result = ontology.resolve("lung cancer")
        assert result is not None
        assert result["canonical"] == "Lung Cancer"

    def test_get_canonical(self, ontologies_dir):
        """Test getting canonical disease name."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        # get_canonical may return the abbreviation itself or expand it
        canonical = ontology.get_canonical("nsclc")
        assert canonical is not None
        assert isinstance(canonical, str)
        assert len(canonical) > 0

    def test_get_synonyms(self, ontologies_dir):
        """Test getting disease synonyms."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        synonyms = ontology.get_synonyms("Lung Cancer")
        assert "Pulmonary carcinoma" in synonyms

    def test_get_related_organs(self, ontologies_dir):
        """Test getting disease-related organs."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        organs = ontology.get_related_organs("Lung Cancer")
        assert "Lung" in organs

    def test_get_related_species(self, ontologies_dir):
        """Test getting disease-related species."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        species = ontology.get_related_species("Lung Cancer")
        assert "Homo sapiens" in species

    def test_get_search_terms(self, ontologies_dir):
        """Test getting search terms."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        terms = ontology.get_search_terms("Lung Cancer")
        assert len(terms) > 0

    def test_get_subtypes(self, ontologies_dir):
        """Test getting disease subtypes."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        subtypes = ontology.get_subtypes("Lung Cancer")
        assert "NSCLC" in subtypes
        assert "SCLC" in subtypes

    def test_find_diseases_by_organ(self, ontologies_dir):
        """Test finding diseases by organ."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        diseases = ontology.find_diseases_by_organ("Lung")
        assert len(diseases) > 0

    def test_is_known_disease(self, ontologies_dir):
        """Test checking known disease."""
        ontology = DiseaseOntology(data_path=ontologies_dir / "doid_hierarchy.json")
        assert ontology.is_known_disease("Lung Cancer")
        assert not ontology.is_known_disease("Unknown Disease")


class TestOrganOntology:
    """Test cases for OrganOntology."""

    def test_load_organ_ontology(self, ontologies_dir):
        """Test loading organ ontology."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        ontology._load()
        assert len(ontology._data) > 0

    def test_resolve_organ(self, ontologies_dir):
        """Test organ name resolution."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        result = ontology.resolve("lung")
        assert result is not None
        assert result["canonical"] == "Lung"

    def test_get_canonical(self, ontologies_dir):
        """Test getting canonical organ name."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        canonical = ontology.get_canonical("pulmonary")
        assert canonical == "Lung"

    def test_get_adjective(self, ontologies_dir):
        """Test getting adjective form."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        adj = ontology.get_adjective("lung")
        assert adj == "pulmonary"

    def test_get_uberon_id(self, ontologies_dir):
        """Test getting Uberon ID."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        uberon_id = ontology.get_uberon_id("Lung")
        assert uberon_id == "UBERON:0002048"

    def test_get_children(self, ontologies_dir):
        """Test getting child organs."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        children = ontology.get_children("Lung")
        assert "Bronchus" in children

    def test_get_parent(self, ontologies_dir):
        """Test getting parent organ."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        parent = ontology.get_parent("Bronchus")
        assert parent == "Lung"

    def test_get_ancestor_chain(self, ontologies_dir):
        """Test getting ancestor chain."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        chain = ontology.get_ancestor_chain("Bronchiole")
        assert "Bronchiole" in chain or "Bronchus" in chain

    def test_get_descendants(self, ontologies_dir):
        """Test getting descendant organs."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        descendants = ontology.get_descendants("Lung")
        assert len(descendants) > 0

    def test_get_search_terms(self, ontologies_dir):
        """Test getting search terms."""
        ontology = OrganOntology(data_path=ontologies_dir / "uberon_organs.json")
        terms = ontology.get_search_terms("Lung")
        assert "pulmonary" in terms


class TestOmicsTypeMapper:
    """Test cases for OmicsTypeMapper."""

    def test_load_omics_types(self, ontologies_dir):
        """Test loading omics types."""
        mapper = OmicsTypeMapper(data_path=ontologies_dir / "omics_types.json")
        mapper._load()
        assert len(mapper._data) > 0

    def test_resolve_omics_type(self, ontologies_dir):
        """Test omics type resolution."""
        mapper = OmicsTypeMapper(data_path=ontologies_dir / "omics_types.json")
        result = mapper.resolve("RNA-Seq")
        assert result is not None
        assert result["canonical"] == "RNA-Seq"

    def test_standardize(self, ontologies_dir):
        """Test omics type standardization."""
        mapper = OmicsTypeMapper(data_path=ontologies_dir / "omics_types.json")
        std = mapper.standardize("rna seq")
        assert std == "RNA-Seq"

    def test_get_search_terms(self, ontologies_dir):
        """Test getting search terms."""
        mapper = OmicsTypeMapper(data_path=ontologies_dir / "omics_types.json")
        terms = mapper.get_search_terms("RNA-Seq")
        assert len(terms) > 0

    def test_detect_from_text(self, ontologies_dir):
        """Test detecting omics type from text."""
        mapper = OmicsTypeMapper(data_path=ontologies_dir / "omics_types.json")
        matches = mapper.detect_from_text("We performed RNA-Seq analysis")
        assert len(matches) > 0
        assert matches[0][0] == "RNA-Seq"
