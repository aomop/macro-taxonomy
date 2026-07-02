"""
Build the taxonomy table from the latest TSN list.

This script:
  - Loads the most recent tsn_list_*.csv in data/add_tsns_data
  - Fetches full ITIS hierarchy XML for each TSN (async)
  - Parses and flattens hierarchy data into a taxonomy table
  - Writes output to data/build_output/built_taxonomy_YYYYMMDD.csv

Usage examples:
    # Build taxonomy from the latest TSN list
    python scripts/build_taxonomy.py

    # Rebuild after adding TSNs
    python scripts/add_tsns.py --tsn 12345
    python scripts/build_taxonomy.py
"""

import pandas as pd
import asyncio
import datetime
import aiohttp
from tqdm.asyncio import tqdm
import xml.etree.ElementTree as ET
from tqdm.auto import tqdm as auto_tqdm
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "add_tsns_data"
OUTPUT_DIR = DATA_DIR / "build_output"

# Import tqdm for pandas integration
auto_tqdm.pandas()

def get_latest_tsn_list() -> Path:
    """Return the most recently modified tsn_list_*.csv in data/add_tsns_data/."""
    candidates = list(INPUT_DIR.glob("tsn_list_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No tsn_list_*.csv files found in {INPUT_DIR}. "
            "Make sure to create one (e.g. via add_tsns.py)."
        )
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"[build_taxonomy] Using TSN list: {latest}")
    return latest

# Asynchronous fetching of XML hierarchy data
async def fetch_full_hierarchy(session, tsn):
    url = f"https://www.itis.gov/ITISWebService/services/ITISService/getFullHierarchyFromTSN?tsn={tsn}"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                text = await response.text()
                # Strip unwanted whitespace characters
                cleaned_text = text.replace('\t', '').replace('\n', '').replace('\r', '')
                return cleaned_text
            else:
                print(f"Failed to fetch hierarchy for TSN: {tsn}, Status code: {response.status}")
                return None
    except Exception as e:
        print(f"Error fetching hierarchy for TSN {tsn}: {str(e)}")
        return None

async def _fetch_all_hierarchies(tsn_list_path: Path) -> list:
    """Fetch all XML hierarchies and return as a list of XML strings."""
    tsn_df = pd.read_csv(tsn_list_path, dtype={"TSN": str})
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_full_hierarchy(session, tsn) for tsn in tsn_df['TSN']]
        results = []
        for result in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Fetching Hierarchies"):
            data = await result
            results.append(data)
    return results

# XML Parsing
def parse_hierarchy(xml_content):
    ns = {'ax21': 'http://data.itis_service.itis.usgs.gov/xsd'}
    root = ET.fromstring(xml_content)
    data = []
    for record in root.findall('.//ax21:hierarchyList', ns):
        details = {
            'tsn': get_text(record.find('ax21:tsn', ns)),
            'author': get_text(record.find('ax21:author', ns)),
            'parentName': get_text(record.find('ax21:parentName', ns)),
            'parentTsn': get_text(record.find('ax21:parentTsn', ns)),
            'rankName': get_text(record.find('ax21:rankName', ns)),
            'taxon': get_text(record.find('ax21:taxonName', ns))
        }
        data.append(details)
    return pd.DataFrame(data)

def get_text(element):
    return element.text if element is not None else None

def process_xml_results(xml_results: list) -> pd.DataFrame:
    """Parse a list of XML strings into a combined DataFrame."""
    all_data = []
    for xml_string in xml_results:
        if isinstance(xml_string, str) and xml_string.strip():
            hierarchy_data = parse_hierarchy(xml_string)
            all_data.append(hierarchy_data)
    if all_data:
        return pd.concat(all_data, ignore_index=True)
    else:
        return pd.DataFrame()

# Tracing Taxonomy Hierarchy
def trace_hierarchy(tsn, itis_data):
    hierarchy = {}
    current_tsn = tsn
    while current_tsn in itis_data.index:
        row = itis_data.loc[current_tsn]
        rank_name = row['rankName']
        taxon_name = row['taxon']
        hierarchy[rank_name] = taxon_name
        current_tsn = row['parentTsn']
        if pd.isna(current_tsn):
            break
    return pd.Series(hierarchy)

# Lowest Level Taxon Determination
def get_taxon_and_level(row):
    last_valid_taxon, last_valid_level = pd.NA, pd.NA
    for rank in ['Kingdom', 'Subkingdom', 'Infrakingdom', 'Superphylum', 'Phylum', 'Subphylum',
                 'Superclass', 'Class', 'Subclass', 'Infraclass', 'Superorder', 'Order',
                 'Suborder', 'Infraorder', 'Superfamily', 'Family', 'Subfamily', 'Tribe',
                 'Subtribe', 'Genus', 'Subgenus', 'Species']:
        if pd.notna(row[rank]):
            last_valid_taxon = row[rank]
            last_valid_level = rank
    return pd.Series([last_valid_taxon, last_valid_level], index=['taxon', 'level'])

def apply_group_mapping(taxonomy, mapping_path=None):
    if mapping_path is None:
        mapping_path = DATA_DIR / "group_mapping.csv"
    mapping_df = pd.read_csv(mapping_path)
    order_to_group = dict(zip(mapping_df["order"], mapping_df["group"]))
    taxonomy['Group'] = taxonomy['Order'].map(order_to_group).fillna('NA')
    return taxonomy

desired_order = ['taxon', 'level', 'Group', 'tsn', 'parentTsn', 'Kingdom', 'Subkingdom', 'Infrakingdom', 'Superphylum', 'Phylum', 'Subphylum',
                 'Superclass', 'Class', 'Subclass', 'Infraclass', 'Superorder', 'Order',
                 'Suborder', 'Infraorder', 'Superfamily', 'Family', 'Subfamily', 'Tribe',
                 'Subtribe', 'Genus', 'Subgenus', 'Species']


def build_taxonomy() -> pd.DataFrame:
    """Build the full taxonomy table in memory and return it as a DataFrame."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Asynchronously fetch XML data
    tsn_list_path = get_latest_tsn_list()
    xml_results = asyncio.run(_fetch_all_hierarchies(tsn_list_path))

    # Process the fetched XML data in memory
    parsed_data = process_xml_results(xml_results)

    # Drop duplicates in parsed_data, focusing on the 'taxon' column
    before = len(parsed_data)
    parsed_data = parsed_data.drop_duplicates(subset=['taxon'], keep='first')
    dropped = before - len(parsed_data)
    if dropped:
        print(f"[build_taxonomy] Dropped {dropped} duplicate taxon name(s) (homonyms kept: first occurrence).")

    # Process parsed data
    itis_data = parsed_data.copy()
    itis_data.drop_duplicates(subset='tsn', inplace=True)
    itis_data.set_index('tsn', inplace=True)
    taxonomy = itis_data.index.to_series().progress_apply(lambda x: trace_hierarchy(x, itis_data))
    taxonomy.fillna(value=pd.NA, inplace=True)

    # Determine lowest taxon
    taxonomy[['taxon', 'level']] = taxonomy.apply(get_taxon_and_level, axis=1)

    # Retrieve tsn and parentTsn
    taxonomy = taxonomy.merge(parsed_data[['tsn', 'parentTsn', 'taxon']], on='taxon', how='left')

    # Apply group mapping
    taxonomy = apply_group_mapping(taxonomy)

    taxonomy = taxonomy[desired_order]

    print(f"[build_taxonomy] Built taxonomy with {len(taxonomy)} rows.")
    return taxonomy


if __name__ == '__main__':
    tax = build_taxonomy()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.datetime.now().strftime('%Y%m%d')
    output_file_path = OUTPUT_DIR / f"built_taxonomy_{date_str}.csv"
    tax.to_csv(output_file_path, index=False, na_rep="NA")
    print(f"Final taxonomy data with groups saved to {output_file_path}")
