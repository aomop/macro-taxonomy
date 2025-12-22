import pandas as pd
import asyncio
import csv
import datetime
import aiohttp
from tqdm.asyncio import tqdm
import xml.etree.ElementTree as ET
from tqdm.auto import tqdm as auto_tqdm
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "input"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = DATA_DIR / "build_output"

# Import tqdm for pandas integration
auto_tqdm.pandas()

def get_latest_tsn_list() -> Path:
    """Return the most recently modified tsn_list_*.csv in data/input/."""
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
    url = f"http://www.itis.gov/ITISWebService/services/ITISService/getFullHierarchyFromTSN?tsn={tsn}"
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

async def main_async_fetch():
    tsn_list_path = get_latest_tsn_list()
    tsn_df = pd.read_csv(tsn_list_path, dtype={"TSN": str})
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_full_hierarchy(session, tsn) for tsn in tsn_df['TSN']]
        results = []
        for result in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Fetching Hierarchies"):
            data = await result
            results.append(data)
    
    # Write results to CSV
    with open(r'data\cache\hierarchy_full.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='|', quoting=csv.QUOTE_ALL)
        writer.writerow(['Hierarchy'])  # Write the header
        for result in results:
            writer.writerow([result])  # Write each result as a row
            
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

def process_xml_from_csv(file_path):
    df = pd.read_csv(file_path)
    # Ensure that all entries in 'Hierarchy' are strings; replace NaN with empty strings
    df['Hierarchy'] = df['Hierarchy'].fillna('')
    all_data = []

    for xml_string in df['Hierarchy']:
        if isinstance(xml_string, str) and xml_string.strip():
            # Only parse non-empty strings
            hierarchy_data = parse_hierarchy(xml_string)
            all_data.append(hierarchy_data)

    if all_data:
        combined_data = pd.concat(all_data, ignore_index=True)
        return combined_data
    else:
        return pd.DataFrame()  # Return an empty DataFrame if no valid XML data was processed

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

def apply_group_mapping(taxonomy):
    # Dictionary for mapping orders to groups
    order_to_group = {
        "Ephemeroptera": "Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders",
        "Odonata": "Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders",
        "Trichoptera": "Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders",
        "Zygoptera": "Dragonflies, Mayflies, Damselflies, and Caddisflies - EOT Orders",
        "Coleoptera": "Beetles - Order Coleoptera",
        "Diptera": "Flies and Midges - Order Diptera",
        "Hemiptera": "True Bugs - Order Hemiptera",
        "Amphipoda": "Crustaceans - Subclass Eumalacostraca",
        "Decapoda": "Crustaceans - Subclass Eumalacostraca",
        "Isopoda": "Crustaceans - Subclass Eumalacostraca",  # Assuming Isopoda also fits here
        "Diplostraca": "Crustaceans - Subclass Eumalacostraca",
        "Anostraca": "Crustaceans - Subclass Eumalacostraca",
        "Hirudinida": "Leeches - Order Hirudinida",
        "Sphaeriida": "Other Non-Insect Invertebrates",
        "Gastropoda": "Snails - Gastropoda",
        "Lepidoptera": "Other Aquatic Insects",  # Lepidoptera generally aren't aquatic, grouped generally
        "Megaloptera": "Other Aquatic Insects",  # Close to aquatic insects
        "Neuroptera": "Other Aquatic Insects",  # More terrestrial, grouped generally
        "Littorinida": "Snails - Class Gastropoda",  # Snails
        "Viviparida": "Snails - Class Gastropoda",  # Snails
        "Ectobranchia": "Other Non-Insect Invertebrates",  # Aquatic non-insects
        "Poduromorpha": "Other Non-Insect Invertebrates", # Spring tails
        "Lymnaeida": "Snails - Class Gastropoda"  # Freshwater snails
    }

    # Apply the group mapping based on 'Order' column which should exist in taxonomy DataFrame
    taxonomy['Group'] = taxonomy['Order'].map(order_to_group).fillna('NA')

    return taxonomy

current_date = datetime.datetime.now()

# Format the date as YYYYMMDD or any other format you prefer
date_str = current_date.strftime('%Y%m%d')

desired_order = ['taxon', 'level', 'Group', 'tsn', 'parentTsn', 'Kingdom', 'Subkingdom', 'Infrakingdom', 'Superphylum', 'Phylum', 'Subphylum',
                 'Superclass', 'Class', 'Subclass', 'Infraclass', 'Superorder', 'Order',
                 'Suborder', 'Infraorder', 'Superfamily', 'Family', 'Subfamily', 'Tribe',
                 'Subtribe', 'Genus', 'Subgenus', 'Species']

def run_pipeline():
    # Asynchronously fetch XML data
    asyncio.run(main_async_fetch())

    # Process the fetched XML data
    parsed_data = process_xml_from_csv(r'data\cache\hierarchy_full.csv')
    
    # Drop duplicates in parsed_data, focusing on the 'taxon' column
    parsed_data = parsed_data.drop_duplicates(subset=['taxon'], keep='first')

    # Save the cleaned parsed_data (optional step depending on need)
    parsed_data.to_csv(r'data\cache\parsed_data_full.csv', index=False)

    # Load and process parsed data
    itis_data = pd.read_csv(r'data\cache\parsed_data_full.csv')
    itis_data.drop_duplicates(subset='tsn', inplace=True)
    itis_data.set_index('tsn', inplace=True)
    taxonomy = itis_data.index.to_series().progress_apply(lambda x: trace_hierarchy(x, itis_data))
    taxonomy.fillna(value=pd.NA, inplace=True)
    taxonomy.to_csv(r'data\cache\expanded_data_full.csv', index=False)

    # Load expanded data and determine lowest taxon
    taxonomy = pd.read_csv(r'data\cache\expanded_data_full.csv')
    taxonomy[['taxon', 'level']] = taxonomy.apply(get_taxon_and_level, axis=1)

    # Load cleaned parsed_data to retrieve tsn and parentTsn
    taxonomy = taxonomy.merge(parsed_data[['tsn', 'parentTsn', 'taxon']], on='taxon', how='left')

    # Apply group mapping
    taxonomy = apply_group_mapping(taxonomy)

    taxonomy = taxonomy[desired_order]

    # Save the final DataFrame with groups and tsn, parentTsn
    output_file_path = f'data\\output\\built_taxonomy_{date_str}.csv'
    taxonomy.to_csv(output_file_path, index=False, na_rep= 'NA')
    print(f"Final taxonomy data with groups saved to {output_file_path}")

if __name__ == '__main__':
    run_pipeline()