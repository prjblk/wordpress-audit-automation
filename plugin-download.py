import requests
import csv
import argparse
import os
import mysql.connector
import zipfile
from datetime import datetime
from io import BytesIO
from tqdm import tqdm
from dbutils import connect_to_db

# Let's only retrieve 10 plugins per page so people feel like the status bar actually moving
def get_plugins(page=1, per_page=10):
    url = f"https://api.wordpress.org/plugins/info/1.2/?action=query_plugins&request[page]={page}&request[per_page]={per_page}"
    response = requests.get(url)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to retrieve page {page}: {response.status_code}")
        return None

def write_plugins_to_csv_db_and_download(csv_filename, download_dir, download_plugins=False, create_schema=False, verbose=False):
    # Open CSV file for writing
    with open(csv_filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        # Write the header
        writer.writerow(['Slug', 'Version', 'Active Installs', 'Downloaded', 'Last Updated', 'Added Date', 'Download Link'])
        
        # Get the first page to find out the total number of pages
        data = get_plugins(page=1)
        
        if not data or 'info' not in data:
            print("Failed to retrieve the plugin information.")
            return
        
        total_pages = data['info']['pages']
        
        # Connect to the database
        db_conn, cursor = connect_to_db(create_schema)

        # Ensure the directory for plugins exists if downloading
        if download_plugins:
            os.makedirs('plugins', exist_ok=True)

        # Iterate through the pages
        for page in tqdm(range(1, total_pages + 1), desc="Downloading plugins"):
            data = get_plugins(page=page)
            
            if not data or 'plugins' not in data:
                break
            
            for plugin in data['plugins']:
                # Write to CSV
                writer.writerow([
                    plugin['slug'],
                    plugin.get('version', 'N/A'),
                    plugin.get('active_installs', 'N/A'),
                    plugin.get('downloaded', 'N/A'),
                    plugin.get('last_updated', 'N/A'),
                    plugin.get('added', 'N/A'),
                    plugin.get('download_link', 'N/A')
                ])

                # Prepare data for database insertion
                last_updated = plugin.get('last_updated', None)
                added_date = plugin.get('added', None)
                
                # Convert date formats if available
                if last_updated:
                    last_updated = datetime.strptime(last_updated, '%Y-%m-%d %I:%M%p %Z').strftime('%Y-%m-%d %H:%M:%S')
                if added_date:
                    added_date = datetime.strptime(added_date, '%Y-%m-%d').strftime('%Y-%m-%d')

                # Prepare SQL upsert statement
                sql = """
                INSERT INTO PluginData (slug, version, active_installs, downloaded, last_updated, added_date, download_link)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    version = VALUES(version),
                    active_installs = VALUES(active_installs),
                    downloaded = VALUES(downloaded),
                    last_updated = VALUES(last_updated),
                    added_date = VALUES(added_date),
                    download_link = VALUES(download_link)
                """
                data = (
                    plugin['slug'],
                    plugin.get('version', 'N/A'),
                    int(plugin.get('active_installs', 0)),
                    int(plugin.get('downloaded', 0)),
                    last_updated,
                    added_date,
                    plugin.get('download_link', 'N/A')
                )

                try:
                    cursor.execute(sql, data)
                    db_conn.commit()
                    if verbose:
                        print(f"Inserted data for plugin {plugin['slug']}.")
                except mysql.connector.errors.ProgrammingError as e:
                    if '1146' in str(e):
                        raise SystemExit("Table does not exist. Please run with the '--create-schema' flag to create the table.")

                # Download and extract the plugin if the download_plugins flag is set
                if download_plugins:
                    download_and_extract_plugin(plugin, download_dir, verbose)

        # Close database connection
        cursor.close()
        db_conn.close()

def download_and_extract_plugin(plugin, download_dir, verbose):
    slug = plugin['slug']
    download_link = plugin.get('download_link')
    last_updated = plugin.get('last_updated')

    # Check if the plugin was last updated in the last 2 years, we'll only download the ones that actively maintained
    try:
        # Parse the date format 'YYYY-MM-DD HH:MMpm GMT'
        last_updated_datetime = datetime.strptime(last_updated, '%Y-%m-%d %I:%M%p %Z')
        last_updated_year = last_updated_datetime.year
        if last_updated_year < (datetime.now().year - 2):
            return
    except ValueError:
        print(f"Invalid date format for plugin {slug}: {last_updated}")
        return
    
    # Download and extract the plugin
    try:
        if verbose:
            print(f"Downloading and extracting plugin: {slug}")
        response = requests.get(download_link)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        with zipfile.ZipFile(BytesIO(response.content)) as z:
            z.extractall(os.path.join(download_dir,'plugins'))
    except requests.RequestException as e:
        print(f"Failed to download {slug}: {e}")
    except zipfile.BadZipFile:
        print(f"Failed to unzip {slug}: Not a zip file or corrupt zip file")

if __name__ == "__main__":
    # Set up argument parser for CSV filename and download flag
    parser = argparse.ArgumentParser(description="Download WordPress plugins information, save to a CSV file, and insert into a database.")
    parser.add_argument('--csv', type=str, default='output.csv', help='The name of the output CSV file (default: output.csv)')
    parser.add_argument('--download-dir', type=str, default='.', help='The directory to save downloaded files (default: current directory)')
    parser.add_argument('--download', action='store_true', help='Download and extract plugins based on the CSV file')
    parser.add_argument('--create-schema', action='store_true', help='Create the database and schema if this flag is set')
    parser.add_argument('--verbose', action='store_true', help='Print detailed messages')
    

    # Parse arguments
    args = parser.parse_args()

    # Write plugins to CSV, Database, and possibly download them
    write_plugins_to_csv_db_and_download(args.csv, args.download_dir, args.download, args.create_schema, args.verbose)
