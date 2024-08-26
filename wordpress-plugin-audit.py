import requests
import csv
import argparse
import os
import json
import subprocess
import mysql.connector
import zipfile
import shutil
from datetime import datetime
from io import BytesIO
from tqdm import tqdm
from dbutils import connect_to_db, delete_results_table


# Let's only retrieve 10 plugins per page so people feel like the status bar is actually moving
def get_plugins(page=1, per_page=10):
    url = f"https://api.wordpress.org/plugins/info/1.2/?action=query_plugins&request[page]={page}&request[per_page]={per_page}"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to retrieve page {page}: {response.status_code}")
        return None


def write_plugins_to_csv_db_and_download(db_conn, cursor, download_dir, verbose=False):

    # Get the first page to find out the total number of pages
    data = get_plugins(page=1)

    if not data or "info" not in data:
        print("Failed to retrieve the plugin information.")
        return

    total_pages = data["info"]["pages"]
    total_pages = 2

    # Ensure the directory for plugins exists
    os.makedirs(os.path.join(download_dir, "plugins"), exist_ok=True)

    # Iterate through the pages
    for page in tqdm(range(1, total_pages + 1), desc="Downloading plugins"):
        data = get_plugins(page=page)

        if not data or "plugins" not in data:
            break

        for plugin in data["plugins"]:

            # Prepare data for database insertion
            last_updated = plugin.get("last_updated", None)
            added_date = plugin.get("added", None)

            # Convert date formats if available
            if last_updated:
                last_updated = datetime.strptime(
                    last_updated, "%Y-%m-%d %I:%M%p %Z"
                ).strftime("%Y-%m-%d %H:%M:%S")
            if added_date:
                added_date = datetime.strptime(added_date, "%Y-%m-%d").strftime(
                    "%Y-%m-%d"
                )

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
                plugin["slug"],
                plugin.get("version", "N/A"),
                int(plugin.get("active_installs", 0)),
                int(plugin.get("downloaded", 0)),
                last_updated,
                added_date,
                plugin.get("download_link", "N/A"),
            )

            try:
                cursor.execute(sql, data)
                db_conn.commit()
                if verbose:
                    print(f"Inserted data for plugin {plugin['slug']}.")
            except mysql.connector.errors.ProgrammingError as e:
                if "1146" in str(e):
                    raise SystemExit(
                        "Table does not exist. Please run with the '--create-schema' flag to create the table."
                    )

            # Download and extract the plugin
            download_and_extract_plugin(plugin, download_dir, verbose)


def download_and_extract_plugin(plugin, download_dir, verbose):
    slug = plugin["slug"]
    download_link = plugin.get("download_link")
    last_updated = plugin.get("last_updated")

    # Check if the plugin was last updated in the last 2 years, we'll only download the ones that actively maintained
    try:
        # Parse the date format 'YYYY-MM-DD HH:MMpm GMT'
        last_updated_datetime = datetime.strptime(last_updated, "%Y-%m-%d %I:%M%p %Z")
        last_updated_year = last_updated_datetime.year
        if last_updated_year < (datetime.now().year - 2):
            return
    except ValueError:
        print(f"Invalid date format for plugin {slug}: {last_updated}")
        return

    # Download and extract the plugin
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


def insert_result_into_db(cursor, slug, result):
    sql = (
        "INSERT INTO PluginResults (slug, file_path, check_id, start_line, end_line, vuln_lines)"
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    data = (
        slug,
        result["path"],
        result["check_id"],
        result["start"]["line"],
        result["end"]["line"],
        result["extra"]["lines"],
    )
    try:
        cursor.execute(sql, data)

    except mysql.connector.errors.ProgrammingError as e:
        if "1146" in str(e):
            raise SystemExit(
                "Table does not exist. Please run with the '--create-schema' flag to create the table."
            )


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
        description="Downloads or audits all Wordpress plugins."
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download and extract plugins, if plugin directory already exists, it will delete it and redownload",
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

    if not args.download and not args.audit:
        print ("Please set either the --download or --audit option.\n")
        parser.print_help()

    else:
        # Create schema
        db_conn, cursor = connect_to_db(args.create_schema)
        if args.clear_results:
            delete_results_table(cursor)

        # Write plugins to CSV, Database, and possibly download them
        if args.download:
            write_plugins_to_csv_db_and_download(
                db_conn, cursor, args.download_dir, args.verbose
            )
        if args.audit:
            run_semgrep_and_store_results(
                db_conn, cursor, args.download_dir, args.config, args.verbose
            )
            
        cursor.close()
        db_conn.close()