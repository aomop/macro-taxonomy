# TAXONOMY

A Python pipeline that builds the macroinvertebrate taxonomy dataset used by the [MacroIBI](https://github.com/aomop/MacroIBI) Shiny application. It queries the [ITIS](https://www.itis.gov/) (Integrated Taxonomic Information System) and [iNaturalist](https://www.inaturalist.org/) APIs to assemble a flat taxonomy table with hierarchical ranks, regional occurrence flags, and English common names.

## What it produces

A dated CSV (`data/output/taxonomy_YYYYMMDD.csv`) with one row per taxon and these key columns:

| Column | Description |
|---|---|
| `taxon` | Lowest-level scientific name for this entry |
| `level` | Taxonomic rank of `taxon` (e.g. Genus, Family) |
| `Group` | MacroIBI display group (e.g. "Beetles - Order Coleoptera") |
| `tsn` | ITIS Taxonomic Serial Number |
| `parentTsn` | TSN of the parent taxon |
| `Kingdom` … `Species` | Full rank hierarchy columns |
| `in_region` | `True` if the taxon is known to occur in the target region |
| `common_names` | Semicolon-separated English common names |

This CSV is loaded into MacroIBI via `refresh_taxonomy()` (see [Connecting to MacroIBI](#connecting-to-macroibi)).

---

## Requirements

Python 3.10+

```
pip install -r requirements.txt
```

Dependencies: `aiohttp`, `pandas`, `requests`, `tqdm`.

---

## Directory structure

```
TAXONOMY/
├── taxa_pipeline.py          # Main entry point — runs the full pipeline
├── requirements.txt
├── scripts/
│   ├── add_tsns.py           # Expand a high-level TSN into all downstream genera
│   ├── build_taxonomy.py     # Fetch ITIS hierarchies and build the flat taxonomy table
│   ├── flag_regions.py       # Add in_region flag via ITIS jurisdiction data
│   ├── scrape_common.py      # Add English common names via ITIS + iNaturalist
│   └── inspect_terms.py      # Diagnostic: audit cached region strings
└── data/
    ├── add_tsns_data/         # Input: tsn_list_YYYYMMDD.csv files (seed TSN lists)
    ├── add_tsns_cache/        # Cache: ITIS hierarchy traversal results
    ├── build_output/          # Intermediate: raw built_taxonomy_*.csv files
    ├── build_cache/           # Cache: parsed hierarchy data
    ├── flag_output/           # Intermediate: taxonomy with in_region flag
    ├── flag_cache/            # Cache: per-TSN ITIS jurisdiction/geo data
    ├── common_names_cache/    # Cache: ITIS common name responses
    ├── inat_cache/            # Cache: iNaturalist responses
    ├── output/                # Final output: taxonomy_YYYYMMDD.csv
    └── region_term_lookup.csv # Maps ITIS region strings to True/False
```

---

## Workflow

### Step 0 — Verify your TSN seed list

The pipeline starts from a file `data/add_tsns_data/tsn_list_YYYYMMDD.csv` that lists the genera (by ITIS TSN) you want included in the taxonomy. A default list covering wetland macroinvertebrates of the continental United States is already included.

The file must have two columns: `TSN` and `genus`.

### Step 1 (optional) — Add a new taxonomic group

To add all genera from a higher-level taxon (e.g. an Order or Family), run `add_tsns.py` with the parent TSN. It performs a breadth-first search down the ITIS hierarchy and merges any new genera into a new dated snapshot of the TSN list.

```bash
# Find the ITIS TSN for the group you want to add, then:
python scripts/add_tsns.py --tsn <PARENT_TSN>

# To start from a specific file instead of the most recent snapshot:
python scripts/add_tsns.py --tsn <PARENT_TSN> --csv data/add_tsns_data/tsn_list_20251205.csv
```

This writes a new `data/add_tsns_data/tsn_list_YYYYMMDD.csv` without modifying the input file.

### Step 2 — Run the pipeline

```bash
python taxa_pipeline.py
```

This runs three stages in sequence:

1. **Build taxonomy** — fetches full ITIS hierarchies for every TSN in the latest `tsn_list_*.csv`, flattens them into a table with one column per rank, and assigns each taxon to a MacroIBI display group.
2. **Flag regions** — queries ITIS jurisdiction and geographic division data per leaf taxon to determine `in_region`, then propagates the flag upward through the hierarchy.
3. **Add common names** — fetches English common names from ITIS for all taxa, with iNaturalist used as a fallback for genera not covered by ITIS.

Output is written to `data/output/taxonomy_YYYYMMDD.csv`.

#### Optional flags

```bash
# Add a single TSN to the seed list before rebuilding (no hierarchy traversal):
python taxa_pipeline.py --tsn 12345 67890

# Re-run only the common names step on the most recent output file:
python taxa_pipeline.py --common-names-only
```

> **Note on `--tsn` in the pipeline vs `add_tsns.py`:** `taxa_pipeline.py --tsn` appends specific TSNs directly to the seed list. `add_tsns.py --tsn` traverses the full ITIS hierarchy below that TSN and collects all downstream genera. Use `add_tsns.py` when adding a new family or order; use `taxa_pipeline.py --tsn` when you already know the exact genus TSNs to add.

---

## Runtime and caching

All ITIS and iNaturalist responses are cached to disk under `data/`. On a **cold run** (empty cache) with ~5,000 TSNs, expect:

- Build taxonomy: 15–30 minutes (async, ~10 concurrent requests)
- Flag regions: 30–60 minutes (async, ~10 concurrent requests, leaf taxa only)
- Common names: 30–60 minutes (ITIS fast, iNaturalist rate-limited to ~1.5 req/s)

On **warm runs** (cache populated), all three stages complete in under a minute. Caches persist indefinitely — delete the relevant `data/*_cache/` directory to force a refresh from the API.

---

## Connecting to MacroIBI

Once the pipeline has produced `data/output/taxonomy_YYYYMMDD.csv`, load it into MacroIBI using `refresh_taxonomy()` from the R console (with the MacroIBI project open):

```r
macroibi::refresh_taxonomy(
  input_dir  = "C:/path/to/TAXONOMY/data/output",
  output_path = "inst/extdata/"
)
```

This reads the most recently dated CSV from `input_dir`, converts it to the RDS format MacroIBI expects, and writes it to `inst/extdata/taxonomy_YYYY-MM-DD.rds`. The app picks it up automatically on next launch.

---

## Customising the region filter

`in_region` is determined by matching ITIS jurisdiction and geographic division strings against `data/region_term_lookup.csv`. The default lookup targets the continental United States and Canada. Each row maps a region string to `TRUE` (taxon is in-region) or `FALSE` (out-of-region).

To adapt this for a different geography:

1. Run the pipeline at least once to populate `data/flag_cache/` with ITIS data.
2. Run the diagnostic script to see every region string that appears in the cache:
   ```bash
   python scripts/inspect_terms.py
   ```
   This writes `data/inspect_output/region_term_summary.csv` with all unique strings and their frequencies.
3. Edit `data/region_term_lookup.csv` to set `in_region_term` to `TRUE` or `FALSE` for each string relevant to your region. Strings not present in the lookup default to `TRUE` (conservative/inclusive).
4. Re-run only the flag step:
   ```bash
   # Re-run the full pipeline (flag_regions re-reads the lookup each run)
   python taxa_pipeline.py
   ```

---

## MacroIBI display groups

The `Group` column maps each taxon to one of the nine sections displayed in the MacroIBI data-entry UI. The mapping is defined in `scripts/build_taxonomy.py` (`apply_group_mapping`) and is keyed on the ITIS `Order` column. To add a new group or reassign an order, edit that dictionary and re-run the pipeline.
