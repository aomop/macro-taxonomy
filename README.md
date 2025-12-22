# Macro Taxonomy Pipeline

This repository contains a small set of scripts for building and annotating a macroinvertebrate taxonomy table from ITIS (Integrated Taxonomic Information System) data. The pipeline reads a TSN list, builds a hierarchical taxonomy table, and adds region flags based on ITIS jurisdiction and geographic division data.

## Repository layout

```
.
├── data/
│   ├── add_tsns_data/        # Source and snapshot TSN lists (tsn_list_*.csv)
│   ├── add_tsns_cache/       # Cached ITIS hierarchy lookups for add_tsns.py
│   ├── build_cache/          # Cached hierarchy XML and parsed data
│   ├── build_output/         # built_taxonomy_*.csv outputs
│   ├── flag_cache/           # Cached ITIS region lookups for flag_regions.py
│   ├── flag_output/          # *_with_regions.csv outputs
│   └── region_term_lookup.csv
├── scripts/
│   ├── add_tsns.py           # Add TSNs and downstream genera to a TSN list
│   ├── build_taxonomy.py     # Build taxonomy tables from TSN lists
│   ├── flag_regions.py       # Add in_region flags to taxonomy tables
│   └── inspect_terms.py      # Summarize ITIS jurisdiction/geographic terms
└── taxa_pipeline.py          # End-to-end pipeline runner
```

## Quick start

1. **(Optional) Add new TSNs** to the latest `tsn_list_*.csv` snapshot.

   ```bash
   python scripts/add_tsns.py --tsn 12345 67890
   ```

2. **Build the taxonomy table** from the latest TSN list.

   ```bash
   python scripts/build_taxonomy.py
   ```

3. **Flag regions** using ITIS jurisdiction/geographic data and the lookup file.

   ```bash
   python scripts/flag_regions.py
   ```

Or run the full pipeline (steps 1–3) in one command:

```bash
python taxa_pipeline.py --tsn 12345 67890
```

## Script details

### `scripts/add_tsns.py`
Adds new TSNs and their downstream genera to a TSN list snapshot.

```bash
# Use the latest tsn_list_*.csv in data/add_tsns_data
python scripts/add_tsns.py --tsn 123456

# Use a specific template CSV
python scripts/add_tsns.py --tsn 123456 --csv data/tsn_list_custom.csv
```

Outputs a new `tsn_list_YYYYMMDD.csv` in `data/add_tsns_data/` without overwriting existing snapshots.

### `scripts/build_taxonomy.py`
Fetches ITIS hierarchy XML for each TSN and builds a flattened taxonomy table.

```bash
python scripts/build_taxonomy.py
```

Outputs `data/build_output/built_taxonomy_YYYYMMDD.csv`.

### `scripts/flag_regions.py`
Adds `in_region` flags using ITIS jurisdiction/geographic data and the lookup file at `data/region_term_lookup.csv`.

```bash
python scripts/flag_regions.py
```

Outputs `data/flag_output/built_taxonomy_YYYYMMDD_with_regions.csv`.

### `scripts/inspect_terms.py`
Summarizes jurisdiction/geographic terms found in the ITIS cache to help refine the lookup file.

```bash
python scripts/inspect_terms.py
```

Outputs `data/inspect_output/region_term_summary.csv`.

## Notes

- Most scripts rely on the **latest** snapshot matching the expected filename pattern, so keep TSN list and taxonomy snapshots in their respective `data/` subdirectories.
- ITIS is accessed over the network, so runs may take some time depending on the number of TSNs.
