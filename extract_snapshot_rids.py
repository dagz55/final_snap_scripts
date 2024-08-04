import re
import os
import glob

def extract_snapshot_rids(log_file_path, output_file_paths):
    with open(log_file_path, 'r') as log_file:
        log_content = log_file.read()

    # Regular expression to find snapshot Resource IDs
    pattern = r'"id": "(\/subscriptions\/[^"]+\/providers\/Microsoft\.Compute\/snapshots\/[^"]+)"'
    snapshot_rids = re.findall(pattern, log_content)

    # Ensure the directories exist and write the Resource IDs to all output files
    for output_file_path in output_file_paths:
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        with open(output_file_path, 'a') as output_file:
            for rid in snapshot_rids:
                output_file.write(f"{rid}\n")

    print(f"Extracted {len(snapshot_rids)} snapshot Resource IDs and appended to {', '.join(output_file_paths)}")

def get_latest_log_file(directory):
    log_files = glob.glob(os.path.join(directory, "snapshot_log_*.txt"))
    if not log_files:
        raise FileNotFoundError(f"No log files found in {directory}")
    return max(log_files, key=os.path.getctime)

if __name__ == "__main__":
    create_directory = "validate"
    log_file_path = get_latest_log_file(create_directory)
    output_file_paths = ["validate/snap_rid_list.txt", "delete/snap_rid_list.txt"]
    extract_snapshot_rids(log_file_path, output_file_paths)
