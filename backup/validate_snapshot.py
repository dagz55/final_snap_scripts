import subprocess
import datetime
import json
import os
import getpass
import time
import sys
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm
from itertools import cycle
from rich.progress import Progress, BarColumn, TextColumn


console = Console()

# Get the user's UID
user_uid = getpass.getuser()

# Create a log directory if it doesn't exist
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# Create an error log file with the user's UID in the file name
error_log_file = os.path.join(log_dir, f"error_log_{user_uid}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.txt")

def log_error(message):
    with open(error_log_file, "a") as f:
        f.write(f"{datetime.datetime.now()}: {message}\n")

def run_az_command(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        log_error(f"Error running command: {command}\nError: {e.stderr}")
        return None

def spinner():
    return cycle(['|', '/', '-', '\\'])

def validate_snapshots(snapshot_list_file):
    start_time = time.time()
    console.print("[bold cyan]Starting snapshot validation...[/bold cyan]")

    with open(snapshot_list_file, "r") as file:
        snapshot_ids = file.read().splitlines()

    total_snapshots = len(snapshot_ids)
    validated_snapshots = []

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
    ) as progress:
        task = progress.add_task("Validating snapshots...", total=total_snapshots)

        for snapshot_id in snapshot_ids:
            snapshot_info = {'id': snapshot_id, 'exists': False}

            details = run_az_command(f"az snapshot show --ids {snapshot_id} --query '{{name:name, resourceGroup:resourceGroup, timeCreated:timeCreated, diskSizeGb:diskSizeGb, provisioningState:provisioningState}}' -o json")

            if details:
                try:
                    details = json.loads(details)
                    snapshot_info.update({
                        'exists': True,
                        'name': details['name'],
                        'resource_group': details['resourceGroup'],
                        'time_created': details['timeCreated'],
                        'size_gb': details['diskSizeGb'],
                        'state': details['provisioningState']
                    })
                except json.JSONDecodeError:
                    log_error(f"Failed to parse JSON for snapshot: {snapshot_id}")

            validated_snapshots.append(snapshot_info)
            progress.update(task, advance=1)

    end_time = time.time()
    runtime = end_time - start_time


    # Create summary table
    table = Table(title="Snapshot Validation Summary")
    table.add_column("Snapshot ID", style="cyan", no_wrap=False)
    table.add_column("Name", style="cyan")
    table.add_column("Exists", style="green")
    table.add_column("Resource Group", style="magenta")
    table.add_column("Time Created", style="yellow")
    table.add_column("Size (GB)", style="blue")
    table.add_column("State", style="red")

    for snapshot in validated_snapshots:
        table.add_row(
            snapshot['id'],
            snapshot.get('name', 'N/A'),
            "✅" if snapshot['exists'] else "❌",
            snapshot.get('resource_group', 'N/A'),
            snapshot.get('time_created', 'N/A'),
            str(snapshot.get('size_gb', 'N/A')),
            snapshot.get('state', 'N/A')
        )

    console.print(table)

    console.print(f"[bold green]Validation complete![/bold green]")
    console.print(f"Total snapshots processed: {total_snapshots}")
    console.print(f"Existing snapshots: {sum(1 for s in validated_snapshots if s['exists'])}")
    console.print(f"Missing snapshots: {sum(1 for s in validated_snapshots if not s['exists'])}")
    console.print(f"Runtime: {runtime:.2f} seconds")

    if Confirm.ask("Do you want to save the validation results to a log file?"):
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        log_file = os.path.join(log_dir, f"snapshot_validation_log_{user_uid}_{timestamp}.txt")
        with open(log_file, "w") as f:
            f.write("Snapshot Validation Results\n")
            f.write("===========================\n\n")
            for snapshot in validated_snapshots:
                f.write(f"Snapshot ID: {snapshot['id']}\n")
                f.write(f"Exists: {'Yes' if snapshot['exists'] else 'No'}\n")
                if snapshot['exists']:
                    f.write(f"Name: {snapshot.get('name', 'N/A')}\n")
                    f.write(f"Resource Group: {snapshot.get('resource_group', 'N/A')}\n")
                    f.write(f"Time Created: {snapshot.get('time_created', 'N/A')}\n")
                    f.write(f"Size (GB): {snapshot.get('size_gb', 'N/A')}\n")
                    f.write(f"State: {snapshot.get('state', 'N/A')}\n")
                f.write("\n")
            f.write(f"\nTotal snapshots processed: {total_snapshots}\n")
            f.write(f"Existing snapshots: {sum(1 for s in validated_snapshots if s['exists'])}\n")
            f.write(f"Non-existing snapshots: {sum(1 for s in validated_snapshots if not s['exists'])}\n")        
            f.write(f"Runtime: {runtime:.2f} seconds\n")
        console.print(f"[bold green]Log file saved:[/bold green] {log_file}")

    console.print(f"\n[yellow]Note: Errors and details have been logged to: {error_log_file}[/yellow]")

if __name__ == "__main__":
    snapshot_list_file = input("Enter the path to the snapshot list file (default: snap_rid_list.txt): ") or "snap_rid_list.txt"
    validate_snapshots(snapshot_list_file)