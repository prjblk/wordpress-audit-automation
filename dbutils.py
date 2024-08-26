import mysql.connector
import configparser


def connect_to_db(create_schema=False):
    # Read the configuration file
    config = configparser.ConfigParser()
    config.read("config.ini")

    # Extract database connection details
    db_config = config["database"]

    # Connect to the database server (initially without specifying the database)
    db_conn = mysql.connector.connect(
        host=db_config["host"], user=db_config["user"], password=db_config["password"]
    )
    cursor = db_conn.cursor()
    try:
        # If schema creation is requested, create the database and table if they don't exist
        if create_schema:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_config['database']}")
            db_conn.database = db_config["database"]
            create_plugin_data_table(cursor)
            create_plugin_results_table(cursor)
        else:
            db_conn.database = db_config["database"]

    except mysql.connector.errors.ProgrammingError as e:
        if "1049" in str(e):
            raise SystemExit(
                "Database {} does not exist. Please run with the '--create-schema' flag to create the database.".format(
                    db_config["database"]
                )
            )

    return db_conn, cursor

def delete_results_table(cursor):
    cursor.execute("DROP TABLE IF EXISTS PluginResults")
    create_plugin_results_table(cursor)

def create_plugin_data_table(cursor):
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS PluginData (
        slug VARCHAR(255) PRIMARY KEY,
        version VARCHAR(255),
        active_installs INT,
        downloaded INT,
        last_updated DATETIME,
        added_date DATE,
        download_link TEXT
    )
    """
    )


def create_plugin_results_table(cursor):
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS PluginResults (
        id INT AUTO_INCREMENT PRIMARY KEY,
        slug VARCHAR(255),
        file_path VARCHAR(255),
        check_id VARCHAR(255),
        start_line INT,
        end_line INT,
        vuln_lines TEXT,
        FOREIGN KEY (slug) REFERENCES PluginData(slug)
    )
    """
    )
