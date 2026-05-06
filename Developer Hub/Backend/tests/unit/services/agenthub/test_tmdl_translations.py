"""Unit tests for the TMDL translations helper."""

from __future__ import annotations

from services.agenthub.tmdl_translations import (
    ApplyItem,
    empty_culture,
    merge_items,
    parse_culture,
    serialize_culture,
)


def test_serialize_empty_yields_minimal_header() -> None:
    cm = empty_culture("de-DE")
    out = serialize_culture(cm)
    assert out.startswith("cultureInfo de-DE")
    assert "translations" not in out


def test_merge_then_serialize_roundtrips() -> None:
    cm = empty_culture("de-DE")
    n = merge_items(
        cm,
        [
            ApplyItem("Table", "Sales", "Umsatz"),
            ApplyItem("Column", "Sales[Amount]", "Betrag"),
            ApplyItem("Measure", "Sales[Total Sales]", "Gesamtumsatz"),
        ],
    )
    assert n == 3

    out = serialize_culture(cm)
    # All three entries land in the body
    assert "table Sales" in out
    assert "caption: Umsatz" in out
    assert "column Amount" in out
    assert "caption: Betrag" in out
    # Names with spaces get single-quoted
    assert "measure 'Total Sales'" in out
    assert "caption: Gesamtumsatz" in out

    # Round-trip parse → serialize is idempotent
    cm2 = parse_culture(out)
    assert serialize_culture(cm2) == out


def test_merge_preserves_existing_entries_and_linguistic_metadata() -> None:
    existing = (
        "cultureInfo de-DE\n"
        "\n"
        "\ttranslations\n"
        "\t\ttable Customers\n"
        "\t\t\tcaption: Kunden\n"
        "\t\t\tcolumn Name\n"
        "\t\t\t\tcaption: Name\n"
        "\n"
        "\tlinguisticMetadata = ```\n"
        '\t\t{"Version":"1.0.0","Language":"de-DE"}\n'
        "\t\t```\n"
        "\t\tcontentType: json\n"
    )
    cm = parse_culture(existing)
    assert "Customers" in cm.by_table
    assert cm.linguistic_block is not None
    assert "linguisticMetadata" in cm.linguistic_block

    merge_items(
        cm,
        [
            ApplyItem("Table", "Sales", "Umsatz"),
            ApplyItem("Column", "Customers[Email]", "E-Mail"),
        ],
    )
    out = serialize_culture(cm)
    # Pre-existing entry kept
    assert "caption: Kunden" in out
    assert "caption: Name" in out
    # New entries added
    assert "table Sales" in out
    assert "column Email" in out
    assert 'caption: "E-Mail"' in out  # contains '-' but no whitespace; should NOT need quoting actually
    # linguisticMetadata block survives
    assert "linguisticMetadata" in out


def test_quoting_for_special_characters() -> None:
    cm = empty_culture("fr-FR")
    merge_items(
        cm,
        [
            ApplyItem("Table", "Sales Orders", "Commandes"),  # space → quote table
            ApplyItem("Measure", "Sales Orders[Total $ Amount]", "Montant total"),
        ],
    )
    out = serialize_culture(cm)
    assert "table 'Sales Orders'" in out
    assert "measure 'Total $ Amount'" in out


def test_overwrite_existing_caption() -> None:
    cm = empty_culture("de-DE")
    merge_items(cm, [ApplyItem("Table", "Sales", "Umsatz")])
    merge_items(cm, [ApplyItem("Table", "Sales", "Verkäufe")])
    out = serialize_culture(cm)
    assert "caption: Verkäufe" in out
    assert "caption: Umsatz" not in out
