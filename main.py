import os
import time
import json
import logging
import ipaddress
from collections import defaultdict
from git import Repo
import requests
import schedule
from typing import List, Dict, Tuple

CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))


def load_config() -> Dict[str, str]:
    """
    Loads the configuration settings from the JSON config file.

    Returns:
        Dict[str, str]: Configuration data in a dictionary format.
    """
    config_path = os.path.join(CURRENT_DIR, "cfg/config.json")
    try:
        with open(config_path) as file:
            return json.load(file)
    except Exception as e:
        logging.error(f"Error loading config file: {e}")
        exit(1)


def setup_logging(config: Dict[str, str]) -> None:
    """
    Sets up logging configuration based on the provided config.

    Args:
        config (Dict[str, str]): Configuration settings for logging.
    """
    logging_level = logging.DEBUG if config["debug"] else logging.INFO
    log_file_path = os.path.join(CURRENT_DIR, "log/script.log")

    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file_path),
            logging.StreamHandler(),
        ],
    )


def initialize_repo(repo_path: str) -> Repo:
    """
    Initializes the Git repository.

    Args:
        repo_path (str): Path to the Git repository.

    Returns:
        Repo: Git repository object.
    """
    try:
        return Repo(repo_path)
    except Exception as e:
        logging.error(f"Error initializing Git repository: {e}")
        exit(1)


def check_for_remote_changes(repo: Repo) -> bool:
    """
    Checks if there are any changes in the remote Git repository compared to the local repository.

    Args:
        repo (Repo): The local Git repository object.

    Returns:
        bool: True if changes are detected in the remote repository, False otherwise.
    """
    try:
        origin = repo.remotes.origin
        origin.fetch()
        local_commit = repo.commit("refs/heads/main")
        remote_commit = repo.commit("refs/remotes/origin/main")
        return local_commit != remote_commit
    except Exception as e:
        logging.error(f"Error checking for remote changes: {e}")
        return False


def pull_latest_changes(repo: Repo) -> None:
    """
    Pulls the latest changes from the remote Git repository.

    Args:
        repo (Repo): The local Git repository object.
    """
    try:
        repo.remotes.origin.pull()
        logging.info("Pulled the latest changes from the remote repository.")
    except Exception as e:
        logging.error(f"Error pulling latest changes: {e}")


def commit_and_push_changes(repo: Repo) -> None:
    """
    Commits and pushes changes to the remote Git repository.

    Args:
        repo (Repo): The local Git repository object.
    """
    try:
        repo.git.add(update=True)
        if repo.is_dirty(untracked_files=True):
            repo.index.commit("Update validated blocklists")
            repo.remotes.origin.push()
            logging.info("Changes committed and pushed to the remote repository.")
        else:
            logging.info("No changes to commit.")
    except Exception as e:
        logging.error(f"Error committing and pushing changes: {e}")


def load_input_file_entries(input_file_path: str) -> List[str]:
    """
    Loads entries from an input file.

    Args:
        input_file_path (str): Path to the input file.

    Returns:
        List[str]: List of entries (IP addresses) from the file.
    """
    try:
        with open(input_file_path, "r") as file:
            lines = [line.strip() for line in file]
            logging.info(f"Loaded {len(lines)} lines from {input_file_path}")
            return lines
    except FileNotFoundError:
        logging.error(f"File {input_file_path} not found.")
        exit(1)


def load_existing_output_file_entries(output_file_path: str) -> Dict[str, str]:
    """
    Loads existing entries from an output file.

    Args:
        output_file_path (str): Path to the output file.

    Returns:
        Dict[str, str]: Dictionary of existing entries and their comments.
    """
    entries = {}
    if os.path.exists(output_file_path):
        with open(output_file_path, "r", encoding="utf-8") as file:
            for line in file:
                if line.strip() and not line.startswith("#"):
                    parts = line.split(" ", 1)
                    entry = parts[0].strip()
                    comment = parts[1].strip("# ").strip() if len(parts) > 1 else ""
                    entries[entry] = comment
        logging.info(f"Loaded {len(entries)} entries from {output_file_path}")
    else:
        logging.info(f"No existing output file found at {output_file_path}")
    return entries


def remove_duplicates(addresses: List[str]) -> List[str]:
    """
    Removes duplicate IP addresses from the list.

    Args:
        addresses (List[str]): List of IP addresses.

    Returns:
        List[str]: List of unique IP addresses.
    """
    unique_addresses = sorted(set(addresses))
    logging.info(
        f"Removed duplicates. {len(addresses)} -> {len(unique_addresses)} unique addresses."
    )
    return unique_addresses


def validate_ip_addresses(addresses: List[str]) -> List[str]:
    """
    Validates the given list of IP addresses.

    Args:
        addresses (List[str]): List of IP addresses.

    Returns:
        List[str]: List of valid IP addresses.
    """
    valid_ips = []
    for ip in addresses:
        try:
            ipaddress.ip_address(ip.split("/")[0])
            valid_ips.append(ip)
        except ValueError:
            logging.warning(f"{ip} is not a valid IP address!")
    logging.info(
        f"Validated addresses. {len(valid_ips)} valid, {len(addresses) - len(valid_ips)} invalid."
    )
    return valid_ips


def group_ips_into_subnets(addresses: List[str], threshold: int) -> List[str]:
    """
    Groups individual IP addresses into subnets based on a threshold.

    Args:
        addresses (List[str]): List of IP addresses or subnets.
        threshold (int): Minimum number of IPs required to group into a /24 subnet.

    Returns:
        List[str]: List of grouped subnets.
    """
    ip_dict = defaultdict(list)
    existing_subnets, individual_ips = set(), set()

    for entry in addresses:
        if "/" not in entry:
            entry += "/32"  # Treat plain IPs as /32 subnets
        network = ipaddress.ip_network(entry, strict=False)
        if network.prefixlen == 32:
            first_three_octets = str(network.network_address).rsplit(".", 1)[0]
            ip_dict[first_three_octets].append(network)
            individual_ips.add(network)
        else:
            existing_subnets.add(network)

    for subnet in existing_subnets:
        first_three_octets = str(subnet.network_address).rsplit(".", 1)[0]
        if first_three_octets in ip_dict:
            del ip_dict[first_three_octets]

    result = set()
    for octets, ips in ip_dict.items():
        if len(ips) >= threshold:
            result.add(f"{octets}.0/24")
            individual_ips.difference_update(ips)

    result.update(str(subnet) for subnet in existing_subnets)
    result.update(str(ip) for ip in individual_ips)

    logging.info(f"Grouped into subnets. {len(result)} entries after grouping.")
    return list(result)


def sort_entries_by_cidr(entries: Dict[str, str]) -> Dict[str, str]:
    """
    Sorts entries by CIDR prefix length and IP address.

    Args:
        entries (Dict[str, str]): Dictionary of IP addresses or subnets and their comments.

    Returns:
        Dict[str, str]: Sorted dictionary of entries.
    """

    def sort_key(entry: str) -> Tuple[int, ipaddress.IPv4Address]:
        network = ipaddress.ip_network(entry, strict=False)
        return (network.prefixlen, network.network_address)

    sorted_entries = dict(sorted(entries.items(), key=lambda item: sort_key(item[0])))
    logging.info(f"Sorted subnets. {len(sorted_entries)} entries after sorting.")
    return sorted_entries


def fetch_whois_info(
    entries: List[str], existing_entries: Dict[str, str]
) -> Dict[str, str]:
    """
    Fetches WhoIs information for a list of IP addresses or subnets.

    Args:
        entries (List[str]): List of IP addresses or subnets.
        existing_entries (Dict[str, str]): Dictionary of already fetched WhoIs data.

    Returns:
        Dict[str, str]: Dictionary of entries with their associated WhoIs comments.
    """
    whois_info = {}
    logging.info(
        "Start fetching WhoIs information for new or changed entries. This could take a while..."
    )
    for entry in entries:
        if entry not in existing_entries:
            try:
                response = requests.get(
                    f"http://ipwho.is/{entry.split('/')[0]}?fields=country,region,connection.isp"
                )
                response_json = response.json()
                comment = f"{response_json.get('country')} | {response_json.get('region')} | {response_json.get('connection', {}).get('isp')}"
                whois_info[entry] = comment
            except Exception as e:
                logging.error(f"Error processing {entry}: {e}")
        else:
            whois_info[entry] = existing_entries[entry]
    logging.info(f"WhoIs information processed.")
    return whois_info


def write_to_output_file(
    entries: Dict[str, str], file_path: str, config: Dict[str, str]
) -> bool:
    """
    Writes processed entries to the output file.

    Args:
        entries (Dict[str, str]): Dictionary of IP addresses or subnets and their comments.
        file_path (str): Path to the output file.
        config (Dict[str, str]): Configuration settings for output.

    Returns:
        bool: True if the output file was successfully written, False otherwise.
    """
    max_entries = config["fg_max_entries"]
    max_comment_length = int(config["fg_max_comment_length"])
    max_file_size = int(config["fg_max_size_bytes"])

    if len(entries) > max_entries:
        logging.error(f"The number of entries exceeds the limit of {max_entries}")
        return False

    try:
        with open(file_path, "w", encoding="utf-8") as file:
            for entry, comment in entries.items():
                if comment:
                    comment = (
                        f"# {comment[:max_comment_length-5]}...\n"
                        if len(comment) > max_comment_length - 2
                        else f"# {comment}\n"
                    )
                file.write(f"{entry} {comment}" if comment else f"{entry}\n")
        file_size = os.path.getsize(file_path)
        if file_size > max_file_size:
            logging.error(
                f"The file size exceeds the limit of {max_file_size / 1024 / 1024}MB"
            )
            return False
        logging.info(f"Output written to {file_path} ({file_size} bytes)")
        return True
    except IOError as e:
        logging.error(f"Error writing to output file: {e}")
        return False


def process_lists(
    input_file_path: str, output_file_path: str, config: Dict[str, str]
) -> Tuple[bool, int, int]:
    """
    Processes input lists, validates IP addresses, groups them into subnets,
    fetches WhoIs information, and writes the final output.

    Args:
        input_file_path (str): Path to the input file.
        output_file_path (str): Path to the output file.
        config (Dict[str, str]): Configuration settings for processing.

    Returns:
        Tuple[bool, int, int]: A tuple indicating success status, number of added entries,
                               and number of removed entries.
    """
    raw_addresses = load_input_file_entries(input_file_path)
    unique_addresses = remove_duplicates(raw_addresses)
    valid_addresses = validate_ip_addresses(unique_addresses)
    grouped_addresses = group_ips_into_subnets(
        valid_addresses, config["threshold_group_ips_into_subnets"]
    )

    existing_entries = load_existing_output_file_entries(output_file_path)
    looked_up_addresses = fetch_whois_info(grouped_addresses, existing_entries)

    current_entries = set(grouped_addresses)
    added_entries = current_entries - set(existing_entries)
    removed_entries = set(existing_entries) - current_entries

    for entry in removed_entries:
        del existing_entries[entry]

    sorted_entries = sort_entries_by_cidr({**existing_entries, **looked_up_addresses})
    success = write_to_output_file(sorted_entries, output_file_path, config)

    return success, len(added_entries), len(removed_entries)


def main() -> None:
    """
    Main function to load configuration, check for remote changes, process files,
    and commit changes if applicable.
    """
    config = load_config()
    setup_logging(config)

    repo = initialize_repo(config["repo_path"])

    if check_for_remote_changes(repo) or config["debug"]:
        logging.info("Changes detected or in debug mode. Start processing.")
        pull_latest_changes(repo)

        total_additions, total_deletions = 0, 0

        for file_name in config["input_files_to_process"]:
            input_file_path = os.path.join(config["repo_path"], file_name)
            output_file_path = os.path.join(
                config["repo_path"],
                "output",
                f"blocklist-industrial{'-manual' if 'manual' in file_name else ''}.txt",
            )
            processed, additions, deletions = process_lists(
                input_file_path, output_file_path, config
            )
            total_additions += additions
            total_deletions += deletions

        if processed and not config["debug"]:
            commit_and_push_changes(repo)
        elif processed and config["debug"]:
            logging.warning("Currently in debug mode, not pushing to git.")
        else:
            logging.error("Error during output file validation, not pushing to git.")

        logging.info(
            f"Stats: {total_additions} Additions | {total_deletions} Deletions"
        )
    else:
        logging.info(
            "No remote changes detected and not in debug mode. Skipping processing."
        )


def schedule_task() -> None:
    """
    Schedules the script to run at regular intervals based on the configuration.

    The task runs periodically using the schedule module.
    """
    config = load_config()
    main()
    schedule.every(config["run_script_interval_hours"]).hours.do(main)
    logging.info(
        f"Scheduled task to run every {config['run_script_interval_hours']} hours."
    )

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    schedule_task()
