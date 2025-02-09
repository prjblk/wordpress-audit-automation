import requests
import argparse
import os
import json
import subprocess
import zipfile
import shutil
from datetime import datetime, UTC
from urllib.parse import urlparse, parse_qs
from io import BytesIO
from bs4 import BeautifulSoup
from tqdm import tqdm
from dbutils import (
    connect_to_db,
    delete_results_table,
    insert_result_into_db,
    insert_plugin_into_db,
)

def drupal_download_modules(db_conn, cursor, download_dir, verbose=False):
    os.makedirs(os.path.join(download_dir, "plugins"), exist_ok=True)

    url = "https://www.drupal.org/api-d7/node.json?type=project_module&field_project_type=full&page=0"
    response = requests.get(url)
    # Parse the JSON response
    data = response.json()
    last = data.get('last', 0)
    
    # Parse the URL
    parsed_url = urlparse(last)
    # Extract query parameters
    query_params = parse_qs(parsed_url.query)
    # Get the value of the 'page' parameter
    page_value = query_params.get('page', [None])[0]  # Default to None if 'page' is not in the query
    for page in tqdm(range(0, int(page_value)), desc="Downloading plugins"):
        url = f"https://www.drupal.org/api-d7/node.json?type=project_module&field_project_type=full&page={page}"
        response = requests.get(url)
        data = response.json()

        modules = data.get('list', [])
        for module in modules:
            version,download_link = drupal_get_latest_release(module['field_project_machine_name'])
            if download_link:
                module_name = module['field_project_machine_name']
                
                last_updated = datetime.fromtimestamp(int(module['changed']), UTC)
                added_dated = datetime.fromtimestamp(int(module['created']), UTC).strftime('%Y-%m-%d')

                total_usage = sum(int(value) for value in module.get('project_usage', {}).values())

                if last_updated.year > (datetime.now().year - 2):
                    plugin = {
                        "slug": module_name,
                        "version": version,
                        "active_installs": total_usage,
                        "downloaded": "0",
                        "last_updated_z": last_updated,
                        "added_date": added_dated,
                        "download_link": download_link
                    }

                    insert_plugin_into_db(cursor, plugin, "drupal")
                    db_conn.commit()
                    download_zip(download_link, download_dir, module_name, verbose)


def drupal_get_latest_release(module_name):
    url = f"https://www.drupal.org/project/{module_name}/releases"
    response = requests.get(url)
    
    if response.status_code == 200:
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Locate the release row
        release_row_container  = soup.find('div', class_='view-content')
        if release_row_container:
            release_row = release_row_container.find('div', class_='views-row')
            # Extract version and download link
            version_tag = release_row.find('h2')
            version = version_tag.get_text(strip=True) if version_tag else "Unknown version"
            
            link_tag = release_row.find('a', href=True)
            relative_link = link_tag['href'] if link_tag else None
            
            if relative_link:
                # Extract version from the link
                version_part = relative_link.split('/')[-1]  # Extract "7.x-2.0-beta4"
                download_link = f"https://ftp.drupal.org/files/projects/{module_name}-{version_part}.zip"
                return version, download_link
            else:
                print("No download link found.")
        else:
            print(f"No releases found for module: {module_name}")
    else:
        print(f"Failed to fetch release page for: {module_name} (HTTP {response.status_code})")
    
    return None, None


# Let's only retrieve 10 plugins per page so people feel like the status bar is actually moving
def wp_get_plugins(page=1, per_page=10):
    url = f"https://api.wordpress.org/plugins/info/1.2/?action=query_plugins&request[page]={page}&request[per_page]={per_page}"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to retrieve page {page}: {response.status_code}")
        return None


def wp_download_plugins(db_conn, cursor, download_dir, verbose=False):

    # Get the first page to find out the total number of pages
    data = wp_get_plugins(page=1)

    if not data or "info" not in data:
        print("Failed to retrieve the plugin information.")
        return

    total_pages = data["info"]["pages"]

    # Ensure the directory for plugins exists
    os.makedirs(os.path.join(download_dir, "plugins"), exist_ok=True)

    # Iterate through the pages
    for page in tqdm(range(1, total_pages + 1), desc="Downloading plugins"):
        data = wp_get_plugins(page=page)

        if not data or "plugins" not in data:
            break

        for plugin in data["plugins"]:
            # Prepare data for database insertion
            last_updated = plugin.get("last_updated", None)
            added_date = plugin.get("added", None)

            # Convert date formats if available
            if last_updated:
                last_updated = datetime.strptime(last_updated, "%Y-%m-%d %I:%M%p %Z").strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                plugin['last_updated_z'] = last_updated
            if added_date:
                added_date = datetime.strptime(added_date, "%Y-%m-%d").strftime("%Y-%m-%d")
                plugin['added_date'] = added_date

            insert_plugin_into_db(cursor, plugin, "wordpress")
            db_conn.commit()

            if verbose:
                print(f"Inserted data for plugin {plugin['slug']}.")
            # Download and extract the plugin
            slug = plugin["slug"]
            download_link = plugin.get("download_link")
            last_updated = plugin.get("last_updated")
            # Check if the plugin was last updated in the last 2 years, we'll only download the ones that actively maintained
            try:
                # Parse the date format 'YYYY-MM-DD HH:MMpm GMT'
                last_updated_datetime = datetime.strptime(last_updated, "%Y-%m-%d %I:%M%p %Z")
                last_updated_year = last_updated_datetime.year
                if last_updated_year > (datetime.now().year - 2):
                    pass
            except ValueError:
                print(f"Invalid date format for plugin {slug}: {last_updated}")
                return
            
            download_zip(download_link, download_dir, slug, verbose)


def download_zip(download_link, download_dir, slug, verbose=False):
    plugin_path = os.path.join(download_dir, "plugins", slug)
    # Clear the directory if it exists
    if os.path.exists(plugin_path):
        if verbose:
            print(f"Plugin folder already exists, deleting folder: {plugin_path}")
        shutil.rmtree(plugin_path)
    try:
        if verbose:
            print(f"Downloading and extracting plugin: {slug}")
        response = requests.get(download_link)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        with zipfile.ZipFile(BytesIO(response.content)) as z:
            z.extractall(os.path.join(download_dir, "plugins"))
    except requests.RequestException as e:
        print(f"Failed to download {slug}: {e}")
    except zipfile.BadZipFile:
        print(f"Failed to unzip {slug}: Not a zip file or corrupt zip file")

def run_semgrep_and_store_results(db_conn, cursor, download_dir, config, verbose=False):

    plugins = os.listdir(os.path.join(download_dir, "plugins"))

    for plugin in tqdm(plugins, desc="Auditing plugins"):
        plugin_path = os.path.join(download_dir, "plugins", plugin)
        output_file = os.path.join(plugin_path, "semgrep_output.json")

        command = [
            "semgrep",
            "--config",
            "{}".format(config),
            "--json",
            "--no-git-ignore",
            "--output",
            output_file,
            "--quiet",  # Suppress non-essential output
            plugin_path,
        ]

        try:
            # Run the semgrep command
            subprocess.run(command, check=True)
            if verbose:
                print(f"Semgrep analysis completed for {plugin}.")

        except subprocess.CalledProcessError as e:
            print(f"Semgrep failed for {plugin}: {e}")
        except json.JSONDecodeError as e:
            print(f"Failed to decode JSON for {plugin}: {e}")
        except Exception as e:
            print(f"Unexpected error for {plugin}: {e}")

        # Read the output file and process results
        with open(output_file, "r") as file:
            data = json.load(file)
            for item in data["results"]:
                insert_result_into_db(cursor, plugin, item)
                db_conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Downloads or audits all Wordpress/Drupal plugins."
    )
    parser.add_argument(
        "--download-wordpress",
        action="store_true",
        help="Download and extract Wordpress plugins, if plugin directory already exists, it will delete it and redownload",
    )
    parser.add_argument(
        "--download-drupal",
        action="store_true",
        help="Download and extract Drupal plugins, if plugin directory already exists, it will delete it and redownload",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=".",
        help="The directory to save/audit downloaded plugins (default: current directory)",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Audits downloaded plugins sequentially",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="p/php",
        help="Semgrep config/rules to run - https://semgrep.dev/docs/running-rules#running-semgrep-registry-rules-locally (default: p/php)",
    )
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Create the database and schema if this flag is set",
    )
    parser.add_argument(
        "--clear-results",
        action="store_true",
        help="Clear audit table and then run, useful if run as a cron job and we only care about the latest release",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print detailed messages"
    )

    # Parse arguments
    args = parser.parse_args()

    if not args.download_wordpress and not args.download_drupal and not args.audit:
        print("Please set either the --download-wordpress or --audit option.\n")
        parser.print_help()

    else:
        # Create schema
        db_conn, cursor = connect_to_db(args.create_schema)
        if args.clear_results:
            delete_results_table(cursor)

        # Write plugins to CSV, Database, and possibly download them
        if args.download_wordpress:
            wp_download_plugins(
                db_conn, cursor, args.download_dir, args.verbose
            )
        if args.download_drupal:
            drupal_download_modules(
                db_conn, cursor, args.download_dir, args.verbose
            )
        if args.audit:
            run_semgrep_and_store_results(
                db_conn, cursor, args.download_dir, args.config, args.verbose
            )

        cursor.close()
        db_conn.close()
