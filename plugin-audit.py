import argparse
import json
import os
import mysql.connector
import subprocess
from tqdm import tqdm
from dbutils import connect_to_db


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


def run_semgrep_and_store_results(
    download_dir, config, create_schema=False, verbose=False
):

    # Connect to the database
    db_conn, cursor = connect_to_db(create_schema)

    plugins = os.listdir(os.path.join(download_dir, "plugins"))

    for plugin in tqdm(plugins, desc="Auditing plugins"):
        plugin_path = os.path.join(download_dir, "plugins", plugin)
        output_file = os.path.join(plugin_path, "semgrep_output.json")

        command = [
            "semgrep",
            "--config", "{}".format(config),
            "--json",
            "--no-git-ignore",
            "--output", output_file,
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

    cursor.close()
    db_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Runs semgrep over the downloaded plugins and inserts output into the database."
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=".",
        help="The directory where the downloaded plugins folder is (default: current directory)",
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
        "--verbose", action="store_true", help="Print detailed messages"
    )

    # Parse arguments
    args = parser.parse_args()
    # Run semgrep across plugins, insert output into database
    run_semgrep_and_store_results(
        args.download_dir, args.config, args.create_schema, args.verbose
    )
