"""
Tests for pure (non-API) pipeline functions.

Run with:  pytest tests/
"""

import pandas as pd
import pytest

from scripts.build_taxonomy import get_taxon_and_level
from scripts.flag_regions import classify_in_region, propagate_in_region
from scripts.scrape_common import dedupe_common_names
from taxa_pipeline import append_tsns


# ---------------------------------------------------------------------------
# classify_in_region
# ---------------------------------------------------------------------------

TERM_MAP = {
    "north america": True,
    "continental us": True,
    "south america": False,
    "africa": False,
}


def _itis(jurisdiction=None, geo=None):
    """Build a minimal ITIS data dict for classify_in_region."""
    return {
        "jurisdiction": {"jurisdictionalOrigins": jurisdiction or []},
        "geo_divisions": {"geoDivisions": geo or []},
    }


def test_classify_in_region_true_when_no_terms():
    """No jurisdiction/geo data → True (unknown, conservative)."""
    assert classify_in_region(_itis(), TERM_MAP) is True


def test_classify_in_region_true_when_any_mapped_true():
    juris = [{"jurisdictionValue": "North America"}, {"jurisdictionValue": "Africa"}]
    assert classify_in_region(_itis(jurisdiction=juris), TERM_MAP) is True


def test_classify_in_region_false_when_all_mapped_false():
    juris = [{"jurisdictionValue": "South America"}, {"jurisdictionValue": "Africa"}]
    assert classify_in_region(_itis(jurisdiction=juris), TERM_MAP) is False


def test_classify_in_region_true_when_terms_all_unmapped():
    """Terms present but none in lookup → unknown → True."""
    juris = [{"jurisdictionValue": "Atlantis"}]
    assert classify_in_region(_itis(jurisdiction=juris), TERM_MAP) is True


def test_classify_in_region_geo_divisions_used():
    geo = [{"geographicValue": "Continental Us"}]
    assert classify_in_region(_itis(geo=geo), TERM_MAP) is True


def test_classify_in_region_normalises_case_and_punctuation():
    """Term lookup normalises to lowercase; mixed-case input should still match."""
    juris = [{"jurisdictionValue": "SOUTH AMERICA"}]
    assert classify_in_region(_itis(jurisdiction=juris), TERM_MAP) is False


# ---------------------------------------------------------------------------
# propagate_in_region
# ---------------------------------------------------------------------------

def _make_tax(rows):
    """rows: list of (tsn, parentTsn) tuples."""
    return pd.DataFrame(rows, columns=["tsn", "parentTsn"])


def test_propagate_single_leaf():
    tax = _make_tax([("1", "0")])
    result = propagate_in_region(tax, {"1": True})
    assert result["1"] is True


def test_propagate_parent_true_if_any_child_true():
    #   root (2)
    #   ├── leaf A (1) → True
    #   └── leaf B (3) → False
    tax = _make_tax([("1", "2"), ("3", "2"), ("2", "NA")])
    result = propagate_in_region(tax, {"1": True, "3": False})
    assert result["2"] is True


def test_propagate_parent_false_if_all_children_false():
    tax = _make_tax([("1", "2"), ("3", "2"), ("2", "NA")])
    result = propagate_in_region(tax, {"1": False, "3": False})
    assert result["2"] is False


def test_propagate_deep_chain():
    # species(1) → genus(2) → family(3) → order(4)
    tax = _make_tax([("1", "2"), ("2", "3"), ("3", "4"), ("4", "NA")])
    result = propagate_in_region(tax, {"1": False})
    assert result["2"] is False
    assert result["3"] is False
    assert result["4"] is False


def test_propagate_isolated_node_defaults_true():
    """A TSN with no children and no entry in leaf_in_region → defaults True."""
    tax = _make_tax([("1", "NA"), ("2", "NA")])
    result = propagate_in_region(tax, {})
    assert result["1"] is True
    assert result["2"] is True


# ---------------------------------------------------------------------------
# get_taxon_and_level
# ---------------------------------------------------------------------------

def _row(**kwargs):
    all_ranks = [
        "Kingdom", "Subkingdom", "Infrakingdom", "Superphylum", "Phylum",
        "Subphylum", "Superclass", "Class", "Subclass", "Infraclass",
        "Superorder", "Order", "Suborder", "Infraorder", "Superfamily",
        "Family", "Subfamily", "Tribe", "Subtribe", "Genus", "Subgenus", "Species",
    ]
    data = {rank: pd.NA for rank in all_ranks}
    data.update(kwargs)
    return pd.Series(data)


def test_get_taxon_and_level_genus():
    row = _row(Kingdom="Animalia", Phylum="Arthropoda", Order="Diptera", Genus="Chironomus")
    result = get_taxon_and_level(row)
    assert result["taxon"] == "Chironomus"
    assert result["level"] == "Genus"


def test_get_taxon_and_level_species():
    row = _row(Kingdom="Animalia", Genus="Chironomus", Species="Chironomus riparius")
    result = get_taxon_and_level(row)
    assert result["taxon"] == "Chironomus riparius"
    assert result["level"] == "Species"


def test_get_taxon_and_level_order_only():
    row = _row(Kingdom="Animalia", Order="Diptera")
    result = get_taxon_and_level(row)
    assert result["taxon"] == "Diptera"
    assert result["level"] == "Order"


def test_get_taxon_and_level_all_na():
    row = _row()
    result = get_taxon_and_level(row)
    assert pd.isna(result["taxon"])
    assert pd.isna(result["level"])


# ---------------------------------------------------------------------------
# dedupe_common_names
# ---------------------------------------------------------------------------

def test_dedupe_removes_case_variants():
    result = dedupe_common_names(["firefly", "Firefly", "FIREFLY"])
    assert len(result) == 1
    assert result[0] == "Firefly"


def test_dedupe_keeps_longest_variant():
    # "lightning bugs" and "lightningbugs" normalise to same key
    result = dedupe_common_names(["lightningbugs", "lightning bugs"])
    assert len(result) == 1
    assert result[0] == "Lightning Bugs"


def test_dedupe_preserves_distinct_names():
    result = dedupe_common_names(["mayfly", "stonefly"])
    assert len(result) == 2


def test_dedupe_title_cases_output():
    result = dedupe_common_names(["giant water bug"])
    assert result == ["Giant Water Bug"]


def test_dedupe_empty_input():
    assert dedupe_common_names([]) == []


# ---------------------------------------------------------------------------
# append_tsns (pipeline dedup logic)
# ---------------------------------------------------------------------------

def test_append_tsns_adds_new(tmp_path):
    csv = tmp_path / "tsn_list_20240101.csv"
    csv.write_text("TSN,genus\n100,Chironomus\n")

    import taxa_pipeline
    orig_input = taxa_pipeline.INPUT_DIR
    orig_output = taxa_pipeline.OUTPUT_DIR

    taxa_pipeline.INPUT_DIR = tmp_path
    taxa_pipeline.OUTPUT_DIR = tmp_path
    try:
        out = append_tsns(["200"])
        df = pd.read_csv(out, dtype={"TSN": str})
        assert "200" in df["TSN"].values
        assert "100" in df["TSN"].values
    finally:
        taxa_pipeline.INPUT_DIR = orig_input
        taxa_pipeline.OUTPUT_DIR = orig_output


def test_append_tsns_skips_existing(tmp_path):
    csv = tmp_path / "tsn_list_20240101.csv"
    csv.write_text("TSN,genus\n100,Chironomus\n")

    import taxa_pipeline
    orig_input = taxa_pipeline.INPUT_DIR
    orig_output = taxa_pipeline.OUTPUT_DIR

    taxa_pipeline.INPUT_DIR = tmp_path
    taxa_pipeline.OUTPUT_DIR = tmp_path
    try:
        out = append_tsns(["100"])
        df = pd.read_csv(out, dtype={"TSN": str})
        assert df["TSN"].tolist().count("100") == 1
    finally:
        taxa_pipeline.INPUT_DIR = orig_input
        taxa_pipeline.OUTPUT_DIR = orig_output
