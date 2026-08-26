# macro-taxonomy

[![tests](https://github.com/aomop/macro-taxonomy/actions/workflows/tests.yml/badge.svg)](https://github.com/aomop/macro-taxonomy/actions/workflows/tests.yml)

A Python pipeline that builds the macroinvertebrate taxonomy dataset used by the [MacroIBI](https://github.com/aomop/MacroIBI) Shiny application. It queries the [ITIS](https://www.itis.gov/) (Integrated Taxonomic Information System) and [iNaturalist](https://www.inaturalist.org/) APIs to assemble a flat taxonomy table with hierarchical ranks, regional occurrence flags, and English common names.

## Products

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

To run the test suite as well:

```
pip install -r requirements.txt -r requirements-dev.txt
```

---

## Directory structure

```
macro-taxonomy/
├── taxa_pipeline.py          # Main entry point - runs the full pipeline
├── requirements.txt          # Runtime dependencies
├── requirements-dev.txt      # Test dependencies
├── scripts/
│   ├── add_tsns.py           # Expand a high-level TSN into all downstream genera
│   ├── build_taxonomy.py     # Fetch ITIS hierarchies and build the flat taxonomy table
│   ├── flag_regions.py       # Add in_region flag via ITIS jurisdiction data
│   ├── scrape_common.py      # Add English common names via ITIS + iNaturalist
│   ├── inspect_terms.py      # Diagnostic: audit cached region strings
│   └── file_selection.py     # Pick the newest dated file by filename date
├── tests/                    # pytest suite over the pure (non-API) functions
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
    ├── group_mapping.csv      # Maps ITIS Order to MacroIBI display group
    └── region_term_lookup.csv # Maps ITIS region strings to True/False
```

---

## Workflow

### Step 0 - Verify your TSN seed list

The pipeline starts from a file `data/add_tsns_data/tsn_list_YYYYMMDD.csv` that lists the genera (by ITIS TSN) you want included in the taxonomy. A default list covering wetland macroinvertebrates of the continental United States is already included.

The file must have two columns: `TSN` and `genus`.

**Which seed list gets used.** When several are present, the pipeline takes the newest by the **date in the filename** — not by file modification time, which git does not preserve and which would make the choice arbitrary on a fresh clone. Same-day revisions carry a `_N` suffix and sort after the plain date, so `tsn_list_20260701_1.csv` beats `tsn_list_20260701.csv`. Older snapshots are kept as history and are safe to leave in place; pass `--csv` to start from one explicitly.

### Step 1 (optional) - Add a new taxonomic group

To add all genera from a higher-level taxon (e.g. an Order or Family), run `add_tsns.py` with the parent TSN. It performs a breadth-first search down the ITIS hierarchy and merges any new genera into a new dated snapshot of the TSN list.

```bash
# Find the ITIS TSN for the group you want to add, then:
python scripts/add_tsns.py --tsn <PARENT_TSN>

# To start from a specific file instead of the most recent snapshot:
python scripts/add_tsns.py --tsn <PARENT_TSN> --csv data/add_tsns_data/tsn_list_20251205.csv
```

This writes a new `data/add_tsns_data/tsn_list_YYYYMMDD.csv` without modifying the input file.

### Step 2 - Run the pipeline

```bash
python taxa_pipeline.py
```

This runs three stages in sequence:

1. **Build taxonomy** - fetches full ITIS hierarchies for every TSN in the latest `tsn_list_*.csv`, flattens them into a table with one column per rank, and assigns each taxon to a MacroIBI display group.
2. **Flag regions** - queries ITIS jurisdiction and geographic division data per leaf taxon to determine `in_region`, then propagates the flag upward through the hierarchy.
3. **Add common names** - fetches English common names from ITIS for all taxa, with iNaturalist used as a fallback for genera not covered by ITIS.

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

Every ITIS and iNaturalist response is cached to disk as one JSON file per TSN,
under four independent directories in `data/`. Each stage checks its own cache
before touching the network, so the stages warm up separately — it is normal for
some to be instant while others are still slow.

### Scale

The seed list holds roughly 4,900 **genera**, but the pipeline expands those into
the full hierarchy beneath them. A representative build produces **~57,500 taxa**,
of which **~51,750 are leaves**. The per-stage query counts follow from that, not
from the size of the seed list:

| Stage | Cache | Queries | Cold | Warm |
|---|---|---|---|---|
| Build taxonomy | `build_cache/` | ~4,900 (one hierarchy per seed genus) | ~2 min | ~45 s |
| Flag regions | `flag_cache/` | ~51,750 (leaf taxa only) | ~13 min | seconds |
| Common names — ITIS | `common_names_cache/` | ~57,500 (every taxon) | ~14 min | seconds |
| Common names — iNaturalist | `inat_cache/` | ~6,600 (only taxa ITIS could not name) | ~75 min | ~1 min |

**Fully cold: 1.5–2 hours. Fully warm: 1–2 minutes.** Anything in between means
some caches are populated and others are not — a run of ~30 minutes, for example,
is the normal cost of warm build and iNaturalist caches with cold region and ITIS
common-name caches.

The network-bound stages run at roughly **65–70 TSN/s**, which is what a
concurrency of 10 yields against ITIS. iNaturalist is the outlier: it is
deliberately rate-limited to 1.5 requests/second, so its cold cost dominates a
first run even though it queries the fewest taxa. Warm cache reads run four
orders of magnitude faster — all ~51,750 files in well under a second — so if a
stage is crawling at ~67/s, it is calling the API, not reading the cache.

### Checking which caches are warm

Compare the file count in each cache against the query counts above:

```bash
# PowerShell
Get-ChildItem data\*_cache -Directory | ForEach-Object {
    "{0}: {1}" -f $_.Name, (Get-ChildItem $_ -File).Count
}

# bash
for d in data/*_cache/; do echo "$d $(ls -1 "$d" | wc -l)"; done
```

A directory holding far fewer files than its stage queries will go to the network
on the next run.

Caches persist indefinitely and are never invalidated automatically — delete a
`data/*_cache/` directory to force that stage to refetch from the API. Note that
ITIS jurisdiction data and common names change rarely, so there is little reason
to clear those caches routinely.

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

The `Group` column maps each taxon to one of the sections displayed in the MacroIBI data-entry UI. The mapping lives in **`data/group_mapping.csv`** and is keyed on the ITIS `Order` column. To add a group or reassign an order, edit that file and re-run the pipeline — no code change needed.

The file has exactly two columns:

```csv
order,group
Coleoptera,Beetles - Order Coleoptera
Ephemeroptera,"Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders"
```

> **Quote any group name containing a comma.** Several MacroIBI group names do. An unquoted row parses as extra columns, and pandas absorbs those into an index rather than raising — so the file loads "successfully" as nonsense, every order fails to match, and every taxon ends up with `Group = NA`. `apply_group_mapping()` now validates the parsed mapping and raises if this happens, but the quoting is still yours to get right.

Orders absent from the mapping are assigned `Group = NA`, which is expected for taxa outside the nine display sections.

---

## Testing

The suite covers the pure, non-API functions — region classification and
propagation, common-name deduplication, TSN appending, group mapping validation,
and dated-file selection. No network access required.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/
```

---

## License

Released under the [MIT License](LICENSE).
