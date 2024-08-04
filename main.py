import os
import time
import subprocess
import json
import asyncio
import logging
import traceback
import csv
import sys
import shutil
import datetime
import venv
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.table import Table
from rich.prompt import Confirm, Prompt

console = Console()

# Set up logging
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(filename=os.path.join(log_dir, 'azure_manager.log'), level=logging.DEBUG,
                    format='%(asctime)s:%(levelname)s:%(message)s')

# Global variables
timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
log_file = os.path.join(log_dir, f"snapshot_log_{timestamp}.txt")
summary_file = os.path.join(log_dir, f"snapshot_summary_{timestamp}.txt")
snap_rid_list_file = os.path.join(log_dir, "snap_rid_list.txt")
error_log_file = os.path.join(log_dir, f"error_log_{timestamp}.txt")

def log_error(message):
    with open(error_log_file, "a") as f:
        f.write(f"{datetime.datetime.now()}: {message}\n")

def write_detailed_log(message):
    with open(log_file, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} - {message}\n")

def write_snapshot_rid(snapshot_id):
    with open(snap_rid_list_file, "a") as f:
        f.write(f"{snapshot_id}\n")

async def run_az_command_async(command, max_retries=3, delay=5):
    for attempt in range(max_retries):
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return stdout.decode().strip(), stderr.decode().strip(), process.returncode
        else:
            write_detailed_log(f"Command failed (attempt {attempt + 1}): {command}")
            write_detailed_log(f"Error: {stderr.decode().strip()}")
            if attempt < max_retries - 1:
                write_detailed_log(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
    return "", stderr.decode().strip(), process.returncode

def run_az_command(command):
    try:
        if isinstance(command, list):
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            return result.stdout.strip()
        else:
            with subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                stdout, stderr = process.communicate()
            if process.returncode != 0:
                return f"Error: {stderr.strip()}"
            return stdout.strip()
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {e.cmd}. Error: {e.stderr}")
        raise
    except Exception as e:
        logging.error(f"Error in run_az_command: {str(e)}")
        return f"Error: {str(e)}"

def check_az_login():
    try:
        result = run_az_command("az account show")
        if isinstance(result, str) and "az login" in result.lower():
            console.print("[yellow]You are not logged in to Azure. Please run 'az login' to authenticate.[/yellow]")
            return False
        # Try to parse the JSON output
        try:
            json.loads(result)
            return True
        except json.JSONDecodeError:
            console.print(f"[red]Unexpected response from Azure CLI: {result}[/red]")
            return False
    except Exception as e:
        console.print(f"[red]Error checking Azure login status: {str(e)}[/red]")
        return False

def get_subscription_names():
    command = "az account list --query '[].{id:id, name:name}' -o json"
    result = run_az_command(command)
    if result and not result.startswith("Error:"):
        subscriptions = json.loads(result)
        return {sub['id']: sub['name'] for sub in subscriptions}
    return {}

def switch_subscription(subscription, current_subscription):
    if subscription != current_subscription:
        try:
            run_az_command(['az', 'account', 'set', '--subscription', subscription])
            console.print(f"[green]✔ Switched to subscription: {subscription}[/green]")
            return subscription
        except Exception as e:
            logging.error(f"Failed to switch to subscription {subscription}: {str(e)}")
            raise
    return current_subscription

# Create Snapshot functions
async def process_vm(resource_id, vm_name, chg_number):
    write_detailed_log(f"Processing VM: {vm_name}")
    write_detailed_log(f"Resource ID: {resource_id}")

    # Get the subscription ID
    subscription_id = resource_id.split("/")[2]
    if not subscription_id:
        write_detailed_log(f"Failed to get subscription ID for VM: {vm_name}")
        return vm_name, "Failed to get subscription ID"

    # Set the subscription ID
    _, stderr, returncode = await run_az_command_async(f"az account set --subscription {subscription_id}")
    if returncode != 0:
        write_detailed_log(f"Failed to set subscription ID: {subscription_id}")
        write_detailed_log(f"Error: {stderr}")
        return vm_name, "Failed to set subscription ID"

    write_detailed_log(f"Subscription ID: {subscription_id}")

    # Get the disk ID of the VM's OS disk
    stdout, stderr, returncode = await run_az_command_async(f"az vm show --ids {resource_id} --query 'storageProfile.osDisk.managedDisk.id' -o tsv")
    if returncode != 0 or not stdout:
        write_detailed_log(f"Failed to get disk ID for VM: {vm_name}")
        write_detailed_log(f"Error: {stderr}")
        return vm_name, "Failed to get disk ID"

    disk_id = stdout

    # Get the resource group name
    stdout, stderr, returncode = await run_az_command_async(f"az vm show --ids {resource_id} --query 'resourceGroup' -o tsv")
    if returncode != 0:
        write_detailed_log(f"Failed to get resource group for VM: {vm_name}")
        write_detailed_log(f"Error: {stderr}")
        return vm_name, "Failed to get resource group"

    resource_group = stdout
    write_detailed_log(f"Resource group name: {resource_group}")

    # Create a snapshot
    snapshot_name = f"RH_{chg_number}_{vm_name}_{timestamp}"
    stdout, stderr, returncode = await run_az_command_async(f"az snapshot create --name {snapshot_name} --resource-group {resource_group} --source {disk_id}")
    if returncode != 0:
        write_detailed_log(f"Failed to create snapshot for VM: {vm_name}")
        write_detailed_log(f"Error: {stderr}")
        return vm_name, "Failed to create snapshot"

    # Write snapshot details to log file
    write_detailed_log(f"Snapshot created: {snapshot_name}")
    try:
        snapshot_data = json.loads(stdout)
        write_detailed_log(json.dumps(snapshot_data, indent=2))
        
        # Extract snapshot ID and write to snap_rid_list.txt
        snapshot_id = snapshot_data.get('id')
        if snapshot_id:
            write_snapshot_rid(snapshot_id)
            write_detailed_log(f"Snapshot resource ID added to snap_rid_list.txt: {snapshot_id}")
        else:
            write_detailed_log(f"Warning: Could not extract snapshot resource ID for {snapshot_name}")
    except json.JSONDecodeError:
        write_detailed_log(f"Warning: Could not parse snapshot creation output as JSON. Raw output:")
        write_detailed_log(stdout)

    write_detailed_log(f"Snapshot created successfully for VM: {vm_name}")
    return vm_name, snapshot_name

async def create_snapshots():
    console.print("[cyan]Azure Snapshot Creation[/cyan]")
    console.print("=========================")

    chg_number = Prompt.ask("Enter the CHG number")
    with open(log_file, "a") as f:
        f.write(f"CHG Number: {chg_number}\n\n")

    try:
        with open("snapshot_vmlist.txt") as file:
            vm_list = [line.strip() for line in file if line.strip()]
            total_vms = len(vm_list)
    except FileNotFoundError:
        console.print("[bold red]Error: snapshot_vmlist.txt file not found.[/bold red]")
        return

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})")
    )
    task = progress.add_task("Snapshotting", total=total_vms)

    successful_snapshots = []
    failed_snapshots = []

    with Live(Panel(progress), refresh_per_second=4) as live:
        for line in vm_list:
            try:
                resource_id, vm_name = line.split()
                result = await process_vm(resource_id, vm_name, chg_number)
                if isinstance(result[1], str) and not result[1].startswith("Failed"):
                    successful_snapshots.append(result)
                else:
                    failed_snapshots.append(result)
            except ValueError:
                write_detailed_log(f"Error: Invalid line format in snapshot_vmlist.txt: {line}")
                failed_snapshots.append((line, "Invalid line format"))
            progress.update(task, advance=1)
            live.update(Panel(progress))

    # Create summary table
    table = Table(title="Snapshot Creation Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="magenta")

    table.add_row("Total VMs Processed", str(total_vms))
    table.add_row("Successful Snapshots", str(len(successful_snapshots)))
    table.add_row("Failed Snapshots", str(len(failed_snapshots)))

    console.print(table)

    # Write summary to file
    with open(summary_file, "w") as f:
        f.write("Snapshot Creation Summary\n")
        f.write("========================\n\n")
        f.write(f"Total VMs processed: {total_vms}\n")
        f.write(f"Successful snapshots: {len(successful_snapshots)}\n")
        f.write(f"Failed snapshots: {len(failed_snapshots)}\n\n")

        f.write("Successful snapshots:\n")
        for vm, snapshot in successful_snapshots:
            f.write(f"- {vm}: {snapshot}\n")

        f.write("\nFailed snapshots:\n")
        for vm, error in failed_snapshots:
            f.write(f"- {vm}: {error}\n")

    console.print("\n[bold green]Snapshot creation process completed.[/bold green]")
    console.print(f"Detailed log: {log_file}")
    console.print(f"Summary: {summary_file}")
    console.print(f"Snapshot resource IDs: {snap_rid_list_file}")

# Validate Snapshot functions
def validate_snapshots():
    console.print("[cyan]Azure Snapshot Validation[/cyan]")
    console.print("===========================")

    default_file = os.path.join(log_dir, "snap_rid_list.txt")
    if os.path.exists(default_file):
        snapshot_list_file = default_file
    else:
        snapshot_list_file = Prompt.ask("Enter the path to the snapshot list file", default=default_file)
    
    if not os.path.exists(snapshot_list_file):
        console.print(f"[bold red]Error: File '{snapshot_list_file}' not found.[/bold red]")
        return

    start_time = time.time()
    console.print("[bold cyan]Starting snapshot validation...[/bold cyan]")

    try:
        with open(snapshot_list_file, "r") as file:
            snapshot_ids = file.read().splitlines()
    except FileNotFoundError:
        console.print(f"[bold red]Error: File '{snapshot_list_file}' not found.[/bold red]")
        return
    except IOError as e:
        console.print(f"[bold red]Error reading file '{snapshot_list_file}': {str(e)}[/bold red]")
        return

    if not snapshot_ids:
        console.print(f"[bold yellow]Warning: No snapshot IDs found in '{snapshot_list_file}'.[/bold yellow]")
        return

    total_snapshots = len(snapshot_ids)
    validated_snapshots = []

    with Progress() as progress:
        task = progress.add_task("[cyan]Validating snapshots...", total=total_snapshots)

        for snapshot_id in snapshot_ids:
            snapshot_info = {'id': snapshot_id, 'exists': False}

            details = run_az_command(f"az snapshot show --ids {snapshot_id} --query '{{name:name, resourceGroup:resourceGroup, timeCreated:timeCreated, diskSizeGb:diskSizeGb, provisioningState:provisioningState}}' -o json")

            if details and not details.startswith("Error:"):
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
        log_file = os.path.join(log_dir, f"snapshot_validation_log_{timestamp}.txt")
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
            f.write(f"Missing snapshots: {sum(1 for s in validated_snapshots if not s['exists'])}\n")        
            f.write(f"Runtime: {runtime:.2f} seconds\n")
        console.print(f"[bold green]Log file saved:[/bold green] {log_file}")

    console.print(f"\n[yellow]Note: Errors and details have been logged to: {error_log_file}[/yellow]")

# Delete Snapshot functions
def get_resource_groups_from_snapshots(snapshot_ids):
    resource_groups = set()
    for snapshot_id in snapshot_ids:
        parts = snapshot_id.split('/')
        if len(parts) >= 5:
            resource_groups.add((parts[2], parts[4]))  # (subscription_id, resource_group)
    return resource_groups

def check_and_remove_scope_locks(resource_groups):
    removed_locks = []
    current_subscription = None
    for subscription_id, resource_group in resource_groups:
        current_subscription = switch_subscription(subscription_id, current_subscription)
        command = f"az lock list --resource-group {resource_group} --query '[].{{name:name, level:level}}' -o json"
        locks = json.loads(run_az_command(command))
        for lock in locks:
            if lock['level'] == 'CanNotDelete':
                remove_command = f"az lock delete --name {lock['name']} --resource-group {resource_group}"  
                result = run_az_command(remove_command)
                if not result.startswith("Error:"):
                    removed_locks.append((subscription_id, resource_group, lock['name']))
                    console.print(f"[green]✔ Removed lock '{lock['name']}' from resource group '{resource_group}'[/green]")
                else:
                    console.print(f"[red]Failed to remove lock '{lock['name']}' from resource group '{resource_group}': {result}[/red]")
    return removed_locks

def restore_scope_locks(removed_locks):
    current_subscription = None
    restored_locks = 0
    for subscription_id, resource_group, lock_name in removed_locks:
        current_subscription = switch_subscription(subscription_id, current_subscription)
        command = f"az lock create --name {lock_name} --resource-group {resource_group} --lock-type CanNotDelete"
        result = run_az_command(command)
        if not result.startswith("Error:"):
            console.print(f"[green]✔ Restored lock '{lock_name}' to resource group '{resource_group}'[/green]")
            restored_locks += 1
        else:
            console.print(f"[red]Failed to restore lock '{lock_name}' to resource group '{resource_group}': {result}[/red]")
    return restored_locks

def check_snapshot_exists(snapshot_id):
    command = f"az snapshot show --ids {snapshot_id}"
    result = run_az_command(command)
    return not result.startswith("Error:")

def process_snapshot(snapshot_id, subscription_names):
    try:
        parts = snapshot_id.split('/')
        if len(parts) < 9:
            logging.error(f"Invalid snapshot ID format: {snapshot_id}")
            return None, "invalid", (snapshot_id, "Invalid snapshot ID format")

        subscription_id = parts[2]
        subscription_name = subscription_names.get(subscription_id, subscription_id)
        snapshot_name = parts[-1]

        # Check if snapshot exists
        if not check_snapshot_exists(snapshot_id):
            return subscription_name, "non-existent", snapshot_name

        return subscription_name, "valid", snapshot_name
    except Exception as e:
        logging.error(f"Error processing snapshot {snapshot_id}: {str(e)}")
        return None, "error", (snapshot_id, str(e))

def delete_snapshot(snapshot_id):
    command = f"az snapshot delete --ids {snapshot_id}"
    result = run_az_command(command)
    return not result.startswith("Error:")

def pre_validate_snapshots(snapshot_ids, subscription_names):
    valid_snapshots = []
    results = defaultdict(lambda: defaultdict(list))

    with Progress() as progress:
        task = progress.add_task("[cyan]Pre-validating snapshots...", total=len(snapshot_ids))
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_snapshot = {executor.submit(process_snapshot, snapshot_id, subscription_names): snapshot_id for snapshot_id in snapshot_ids}
            for future in as_completed(future_to_snapshot):
                try:
                    subscription_name, status, data = future.result()
                    if subscription_name:
                        results[subscription_name][status].append(data)
                        if status == "valid":
                            valid_snapshots.append(future_to_snapshot[future])
                    else:
                        results["Unknown"][status].append(data)
                except Exception as e:
                    logging.error(f"Error processing future: {str(e)}")
                progress.update(task, advance=1)

    return valid_snapshots, results

def delete_valid_snapshots(valid_snapshots, subscription_names):
    results = defaultdict(lambda: defaultdict(list))

    with Progress() as progress:
        task = progress.add_task("[cyan]Deleting valid snapshots...", total=len(valid_snapshots))
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_snapshot = {executor.submit(delete_snapshot, snapshot_id): snapshot_id for snapshot_id in valid_snapshots}
            for future in as_completed(future_to_snapshot):
                snapshot_id = future_to_snapshot[future]
                try:
                    success = future.result()
                    parts = snapshot_id.split('/')
                    subscription_id = parts[2]
                    subscription_name = subscription_names.get(subscription_id, subscription_id)
                    snapshot_name = parts[-1]
                    if success:
                        results[subscription_name]["deleted"].append(snapshot_name)
                    else:
                        results[subscription_name]["failed"].append((snapshot_name, "Deletion failed"))     
                except Exception as e:
                    logging.error(f"Error deleting snapshot {snapshot_id}: {str(e)}")
                    results["Unknown"]["error"].append((snapshot_id, str(e)))
                progress.update(task, advance=1)

    return results

def print_summary(results):
    table = Table(title="Summary")
    table.add_column("Subscription", style="cyan")
    table.add_column("Valid Snapshots", style="green")
    table.add_column("Non-existent Snapshots", style="yellow")
    table.add_column("Deleted Snapshots", style="blue")
    table.add_column("Failed Deletions", style="red")

    total_valid = 0
    total_non_existent = 0
    total_deleted = 0
    total_failed = 0

    for subscription_name, data in results.items():
        valid_count = len(data['valid'])
        non_existent_count = len(data['non-existent'])
        deleted_count = len(data['deleted'])
        failed_count = len(data['failed'])
        table.add_row(subscription_name, str(valid_count), str(non_existent_count), str(deleted_count), str(failed_count))

        total_valid += valid_count
        total_non_existent += non_existent_count
        total_deleted += deleted_count
        total_failed += failed_count

    table.add_row("Total", str(total_valid), str(total_non_existent), str(total_deleted), str(total_failed), style="bold")

    console.print(table)

def print_detailed_errors(results):
    console.print("\n[bold red]Detailed Error Information:[/bold red]")

    for subscription_name, data in results.items():
        if data['non-existent'] or data['failed'] or data['error']:
            console.print(f"\n[cyan]Subscription: {subscription_name}[/cyan]")

            if data['non-existent']:
                console.print("\n[bold]Non-existent Snapshots:[/bold]")
                for snapshot in data['non-existent']:
                    console.print(f"  [yellow]• {snapshot}[/yellow]")

            if data['failed']:
                console.print("\n[bold]Failed Deletions:[/bold]")
                for snapshot, error in data['failed']:
                    console.print(f"  [red]• {snapshot}: {error}[/red]")

            if data['error']:
                console.print("\n[bold]Errors:[/bold]")
                for snapshot, error in data['error']:
                    console.print(f"  [red]• {snapshot}: {error}[/red]")

def export_to_csv(results, filename):
    with open(filename, 'w', newline='') as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(['Subscription', 'Status', 'Snapshot', 'Error'])
        for subscription, data in results.items():
            for status, snapshots in data.items():
                if status in ['deleted', 'non-existent', 'valid']:
                    for snapshot in snapshots:
                        csvwriter.writerow([subscription, status, snapshot, ''])
                else:
                    for snapshot, error in snapshots:
                        csvwriter.writerow([subscription, status, snapshot, error])
    console.print(f"[green]✔ Results exported to {filename}[/green]")

def delete_snapshots():
    console.print("[cyan]Azure Snapshot Deletion[/cyan]")
    console.print("=========================")

    if not check_az_login():
        console.print("[red]Please run 'az login' and try again.[/red]")
        return

    filename = console.input("Enter the filename with snapshot IDs: ")
    if not os.path.isfile(filename):
        console.print(f"[bold red]File {filename} does not exist.[/bold red]")
        return

    start_time = time.time()

    subscription_names = get_subscription_names()
    if not subscription_names:
        console.print("[bold red]Failed to fetch subscription names. Using IDs instead.[/bold red]")    

    try:
        with open(filename, 'r') as f:
            snapshot_ids = f.read().splitlines()
    except Exception as e:
        console.print(f"[bold red]Error reading file {filename}: {e}[/bold red]")
        return

    if len(snapshot_ids) > 100:
        confirm = console.input(f"[yellow]You are about to process {len(snapshot_ids)} snapshots. Are you sure you want to proceed? (y/n): [/yellow]")
        if confirm.lower() != 'y':
            console.print("[red]Operation cancelled.[/red]")
            return

    valid_snapshots, pre_validation_results = pre_validate_snapshots(snapshot_ids, subscription_names)  

    if not valid_snapshots:
        console.print("[yellow]No valid snapshots found. Skipping scope lock removal and deletion process.[/yellow]")
        results = pre_validation_results
    else:
        resource_groups = get_resource_groups_from_snapshots(valid_snapshots)
        console.print(f"[green]✔ Found {len(resource_groups)} resource groups from valid snapshot list.[/green]")

        removed_locks = check_and_remove_scope_locks(resource_groups)
        console.print(f"[green]✔ Removed {len(removed_locks)} scope locks.[/green]")

        deletion_results = delete_valid_snapshots(valid_snapshots, subscription_names)

        console.print("[yellow]Restoring removed scope locks...[/yellow]")
        restored_locks = restore_scope_locks(removed_locks)
        console.print(f"[green]✔ Restored {restored_locks} scope locks.[/green]")

        # Merge pre-validation results with deletion results
        results = pre_validation_results
        for subscription, data in deletion_results.items():
            results[subscription].update(data)

    print_summary(results)
    print_detailed_errors(results)

    end_time = time.time()
    total_runtime = end_time - start_time

    console.print(f"\n[bold green]✔ Total runtime: {total_runtime:.2f} seconds[/bold green]")

    export_csv = console.input("Do you want to export the results to a CSV file? (y/n): ")
    if export_csv.lower() == 'y':
        csv_filename = console.input("Enter the CSV filename to export results: ")
        export_to_csv(results, csv_filename)

def main_menu():
    while True:
        console.print("\n[cyan]Azure Snapshot Manager[/cyan]")
        console.print("=========================")
        console.print("1. Create Snapshots")
        console.print("2. Validate Snapshots")
        console.print("3. Delete Snapshots")
        console.print("4. Exit")

        choice = Prompt.ask("Enter your choice", choices=["1", "2", "3", "4"])

        if choice == "1":
            asyncio.run(create_snapshots())
        elif choice == "2":
            validate_snapshots()
        elif choice == "3":
            delete_snapshots()
        elif choice == "4":
            console.print("[green]Exiting Azure Snapshot Manager. Goodbye![/green]")
            break
        
        input("\nPress Enter to continue...")

import subprocess
import sys
import shutil

def check_az_cli():
    try:
        # Check if 'az' command is available
        result = shutil.which("az")
        if not result:
            console.print("[yellow]Warning: 'az' command not found in PATH. Attempting to proceed anyway.[/yellow]")
        
        # Check Azure CLI version
        try:
            version_result = subprocess.run(["az", "version", "--output", "json"], capture_output=True, text=True, timeout=10)
            if version_result.returncode == 0:
                version_info = json.loads(version_result.stdout)
                azure_cli_version = version_info.get('azure-cli', 'Unknown')
                azure_cli_core_version = version_info.get('azure-cli-core', 'Unknown')
                azure_cli_telemetry_version = version_info.get('azure-cli-telemetry', 'Unknown')
                
                console.print(f"[green]Azure CLI is installed. Version: {azure_cli_version}[/green]")
                console.print(f"[green]Azure CLI Core Version: {azure_cli_core_version}[/green]")
                console.print(f"[green]Azure CLI Telemetry Version: {azure_cli_telemetry_version}[/green]")
            else:
                console.print("[yellow]Warning: Unable to get Azure CLI version. Attempting to proceed anyway.[/yellow]")
        except subprocess.TimeoutExpired:
            console.print("[yellow]Warning: Azure CLI version check timed out. Attempting to proceed anyway.[/yellow]")
        except json.JSONDecodeError:
            console.print("[yellow]Warning: Failed to parse Azure CLI version information. Attempting to proceed anyway.[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Warning: Unexpected error while checking Azure CLI version: {str(e)}. Attempting to proceed anyway.[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Warning: An unexpected error occurred while checking for Azure CLI: {str(e)}. Attempting to proceed anyway.[/yellow]")
    
    # Instead of exiting, we'll ask the user if they want to continue
    if Confirm.ask("Do you want to continue with the script?"):
        return
    else:
        console.print("[red]Script execution cancelled by user.[/red]")
        sys.exit(1)

def setup_venv():
    venv_dir = "snapvenv"
    if not os.path.isdir(venv_dir):
        venv.create(venv_dir, with_pip=True)
    
    # Get the path to the activated virtual environment's Python
    if sys.platform == "win32":
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        venv_python = os.path.join(venv_dir, "bin", "python")
    
    # Install or upgrade pip and packages
    subprocess.run([venv_python, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.run([venv_python, "-m", "pip", "install", "-r", "requirements.txt"])

def install_packages():
    console.print("[cyan]Checking and installing required packages...[/cyan]")
    required = {'rich', 'azure-cli'}
    missing = set()

    for package in required:
        try:
            __import__(package)
        except ImportError:
            missing.add(package)

    if missing:
        console.print(f"[yellow]Installing missing packages: {', '.join(missing)}[/yellow]")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
            console.print("[green]All required packages installed successfully.[/green]")
            
            # Apply patch to Azure CLI
            subprocess.check_call([sys.executable, "patch_azure_cli.py"])
            console.print("[green]Azure CLI patched successfully.[/green]")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error during package installation or patching: {str(e)}[/red]")
            sys.exit(1)
    else:
        console.print("[green]All required packages are already installed.[/green]")

if __name__ == "__main__":
    try:
        check_az_cli()
        setup_venv()
        install_packages()

        if not check_az_login():
            console.print("[red]Please run 'az login' and try again.[/red]")
            sys.exit(1)

        main_menu()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user. Exiting...[/yellow]")
    except Exception as e:
        console.print(f"[red]An unexpected error occurred: {str(e)}[/red]")
        console.print("[yellow]Please check the azure_manager.log file for more details.[/yellow]")
        logging.error(f"An unexpected error occurred: {str(e)}\n{traceback.format_exc()}")
