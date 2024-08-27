# Wordpress Automated CVE Hunting

Scripts to download every Wordpress plugin (updated in the last 2 years) and run Semgrep over the lot of it while storing output in a database.

Full write-up: https://projectblack.io/blog/cve-hunting-at-scale/

Want to skip straight to looking at the dataset? 

Download the latest mysqldump here: https://github.com/prjblk/wordpress-audit-automation/releases

## Getting Started

### Prerequisites

* Ubuntu or other nix system
* At least 30GB of disk space
* MySQL database server
* Python
* Patience

## Steps
1. Setup a MySQL database server
2. Clone this repo
    ```
    git clone https://github.com/prjblk/wordpress-audit-automation
    ```
3. Configure the config file with database credentials/details
    ```
    cp config.ini.sample config.ini
    nano config.ini
    ```
4. Install Python dependencies + Semgrep
    ```
    pip install -r requirements.txt
    ```
6. You may have to login again to ensure Semgrep is available via path
7. Setup the database schema manually (skip this step if providing privileged database credentials to the script)
    * Create a database and run the SQL in create_plugin_data_table and create_plugin_results_table in dbutils.py
8. Run the script with the --download --audit and --create-schema options
    * You might want to run this in a tmux/screen session as it takes ages (15 hours?)
    * By default all the rules in p/php are run against the plugins (minus the PRO rules unless you're logged in). https://semgrep.dev/p/php
    * Would highly suggest looking at some of the other rules available as well
9. Triage output
10. ???
11. CVEs

### Example Usage

```
$ python3 wordpress-plugin-audit.py -h
usage: wordpress-plugin-audit.py [-h] [--download] [--download-dir DOWNLOAD_DIR] [--audit] [--config CONFIG] [--create-schema] [--clear-results] [--verbose]

Downloads or audits all Wordpress plugins.

options:
  -h, --help            show this help message and exit
  --download            Download and extract plugins, if plugin directory already exists, it will delete it and redownload
  --download-dir DOWNLOAD_DIR
                        The directory to save/audit downloaded plugins (default: current directory)
  --audit               Audits downloaded plugins sequentially
  --config CONFIG       Semgrep config/rules to run - https://semgrep.dev/docs/running-rules#running-semgrep-registry-rules-locally (default: p/php)
  --create-schema       Create the database and schema if this flag is set
  --clear-results       Clear audit table and then run, useful if run as a cron job and we only care about the latest release
  --verbose             Print detailed messages

$ python3 wordpress-plugin-audit.py --download --audit --create-schema
Downloading plugins: 100%|███████████████████████████████████| 2/2 [00:49<00:00, 24.65s/it]
Auditing plugins:  10%|█████                          | 2/20 [00:05<00:47,  2.62s/it]
```
#### Useful SQL Queries

You can focus on a specific vulnerability class by querying for output relating to a specific rule.

```
USE SemgrepResults;
SELECT PluginResults.slug,PluginData.active_installs,PluginResults.file_path,PluginResults.start_line,PluginResults.vuln_lines 
FROM PluginResults INNER JOIN PluginData ON PluginResults.slug = PluginData.slug 
WHERE check_id = "php.lang.security.injection.tainted-sql-string.tainted-sql-string"
ORDER BY active_installs DESC
```

### Troubleshooting

If you have problems with auditing plugins, ensure you can run semgrep at the command line normally first.
