"""Tests for group mapping validation.

Regression cover for a malformed data/group_mapping.csv: group names contain
commas, and an unquoted row parses as extra columns which pandas absorbs into an
index rather than rejecting. The mapping then "loads" as nonsense and every taxon
falls through to Group='NA', emptying the app's data entry sections.
"""

import pandas as pd
import pytest

from scripts.build_taxonomy import apply_group_mapping


def _taxonomy():
    return pd.DataFrame(
        {
            "taxon": ["Baetis", "Libellula", "Dytiscus"],
            "Order": ["Ephemeroptera", "Odonata", "Coleoptera"],
        }
    )


def _write(tmp_path, text):
    path = tmp_path / "group_mapping.csv"
    path.write_text(text)
    return path


def test_maps_orders_to_groups(tmp_path):
    mapping = _write(
        tmp_path,
        'order,group\n'
        'Ephemeroptera,"Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders"\n'
        'Odonata,"Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders"\n'
        'Coleoptera,Beetles - Order Coleoptera\n',
    )

    result = apply_group_mapping(_taxonomy(), mapping)

    assert result.loc[0, "Group"].endswith("EOT Orders")
    assert result.loc[2, "Group"] == "Beetles - Order Coleoptera"
    assert "NA" not in result["Group"].values


def test_unquoted_commas_are_rejected(tmp_path):
    """The exact malformed shape that shipped: group names unquoted."""
    mapping = _write(
        tmp_path,
        "order,group\n"
        "Ephemeroptera,Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders\n"
        "Odonata,Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders\n"
        "Coleoptera,Beetles - Order Coleoptera\n",
    )

    with pytest.raises(ValueError, match="quoted"):
        apply_group_mapping(_taxonomy(), mapping)


def test_missing_columns_are_rejected(tmp_path):
    mapping = _write(tmp_path, "Order,Group\nEphemeroptera,EOT\n")

    with pytest.raises(ValueError, match="missing required column"):
        apply_group_mapping(_taxonomy(), mapping)


def test_empty_mapping_is_rejected(tmp_path):
    mapping = _write(tmp_path, "order,group\n")

    with pytest.raises(ValueError, match="empty order->group mapping"):
        apply_group_mapping(_taxonomy(), mapping)


def test_partial_mapping_is_allowed(tmp_path):
    """An order legitimately absent from the mapping falls through to 'NA'."""
    mapping = _write(
        tmp_path,
        "order,group\n"
        "Ephemeroptera,EOT Orders\n"
        "Odonata,EOT Orders\n",
    )

    result = apply_group_mapping(_taxonomy(), mapping)

    assert result.loc[2, "Group"] == "NA"
    assert result.loc[0, "Group"] == "EOT Orders"


def test_shipped_mapping_file_is_well_formed():
    """Guard the real data/group_mapping.csv, not just synthetic fixtures."""
    from scripts.build_taxonomy import DATA_DIR

    result = apply_group_mapping(_taxonomy(), DATA_DIR / "group_mapping.csv")

    assert "NA" not in result["Group"].values, (
        "data/group_mapping.csv does not map the core wetland orders -- "
        "check that group names containing commas are quoted."
    )
