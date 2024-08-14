import asyncio
import datetime
import getpass
import json
import os
import sys
import time
from azure.identity.aio import DefaultAzureCredential
from azure.mgmt.compute.aio import ComputeManagementClient
from azure.core.exceptions import ResourceNotFoundError
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Confirm

console = Console()

# Extract SUBSCRIPTION_ID and RESOURCE_GROUP_NAME from snap_rid_list.txt
SUBSCRIPTION_ID = None
RESOURCE_GROUP_NAME = None

with open('snap_rid_list.txt', 'r') as f:
    for line in f:
        line = line.strip()
        if line:
            parts = line.split('/')
            if len(parts) >= 5:
                SUBSCRIPTION_ID = parts[2]
                RESOURCE_GROUP_NAME = parts[4]
                break

if SUBSCRIPTION_ID and RESOURCE_GROUP_NAME:
    console.print(f"[green]Using Subscription ID: {SUBSCRIPTION_ID}[/green]")
    console.print(f"[green]Using Resource Group: {RESOURCE_GROUP_NAME}[/green]")
    os.environ['AZURE_SUBSCRIPTION_ID'] = SUBSCRIPTION_ID
    os.environ['AZURE_RESOURCE_GROUP'] = RESOURCE_GROUP_NAME
else:
    console.print("[bold red]Error: Could not extract Subscription ID and Resource Group from snap_rid_list.txt[/bold red]")
    sys.exit(1)

user_uid = getpass.getuser()

log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

error_log_file = os.path.join(
    log_dir,
    f"error_log_{user_uid}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.txt",
)

def log_error(message):
    with open(error_log_file, "a") as f:
        f.write(f"{datetime.datetime.now()}: {message}\n")

def extract_snapshot_name(snapshot_id):
    parts = snapshot_id.split("/")
    full_name = parts[-1]
    name_parts = full_name.rsplit("_", 1)
    return name_parts[0]

async def read_snapshots(snapshot_list_file):
    with open(snapshot_list_file, "r") as file:
        for line in file:
            yield extract_snapshot_name(line.strip())

async def validate_snapshots(snapshot_list_file):
    console.print(
        Panel.fit(
            "[bold cyan]Starting snapshot validation...[/bold cyan]",
            border_style="cyan",
        )
    )

    credential = DefaultAzureCredential()
    compute_client = ComputeManagementClient(credential, SUBSCRIPTION_ID)

    start_time = time.time()
    
    snapshot_names = []
    with open(snapshot_list_file, "r") as file:
        for line in file:
            snapshot_names.append(extract_snapshot_name(line.strip()))
    total_snapshots = len(snapshot_names)

    validated_snapshots = []
    errors = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        overall_task = progress.add_task("[cyan]Processing snapshots...", total=total_snapshots)
        snapshot_tasks = []

        for snapshot_name in snapshot_names:
            snapshot_task = progress.add_task(f"Validating: {snapshot_name}", total=100)
            snapshot_tasks.append(validate_snapshot(snapshot_name, compute_client, progress, snapshot_task))

        validated_snapshots = await asyncio.gather(*snapshot_tasks)
        progress.update(overall_task, completed=total_snapshots)

    end_time = time.time()
    runtime = end_time - start_time

    # Display summary table
    console.print("\n")
    summary_table = Table(title="Snapshot Validation Summary", box=box.ROUNDED)
    summary_table.add_column("Category", style="cyan")
    summary_table.add_column("Count", style="magenta")
    summary_table.add_row("Total snapshots processed", str(total_snapshots))
    summary_table.add_row("Valid snapshots", str(sum(1 for s in validated_snapshots if s["exists"])))
    summary_table.add_row("Invalid snapshots", str(sum(1 for s in validated_snapshots if not s["exists"])))
    summary_table.add_row("Runtime", f"{runtime:.2f} seconds")
    
    console.print(Panel(summary_table, expand=False, border_style="green"))

    if Confirm.ask("Do you want to save the validation results to a log file?"):
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        log_file = os.path.join(
            log_dir, f"snapshot_validation_log_{user_uid}_{timestamp}.txt"
        )
        with open(log_file, "w") as f:
            f.write("Snapshot Validation Results\n")
            f.write("===========================\n\n")
            for snapshot in validated_snapshots:
                f.write(f"Snapshot Name: {snapshot['name']}\n")
                f.write(f"Exists: {'Yes' if snapshot['exists'] else 'No'}\n")
                if snapshot["exists"]:
                    f.write(f"Resource Group: {snapshot.get('resource_group', 'N/A')}\n")
                    f.write(f"Time Created: {snapshot.get('time_created', 'N/A')}\n")
                    f.write(f"Size (GB): {snapshot.get('size_gb', 'N/A')}\n")
                    f.write(f"State: {snapshot.get('state', 'N/A')}\n")
                f.write("\n")
            f.write(f"\nTotal snapshots processed: {total_snapshots}\n")
            f.write(f"Valid snapshots: {sum(1 for s in validated_snapshots if s['exists'])}\n")
            f.write(f"Invalid snapshots: {sum(1 for s in validated_snapshots if not s['exists'])}\n")
            f.write(f"Runtime: {runtime:.2f} seconds\n")
        console.print(
            Panel(
                f"[bold green]Log file saved:[/bold green] {log_file}",
                border_style="green",
            )
        )

    console.print(
        Panel(
            f"[yellow]Note: Errors and details have been logged to: {error_log_file}[/yellow]",
            border_style="yellow",
        )
    )

async def validate_snapshot(snapshot_name, compute_client, progress, task):
    try:
        # Try to get the snapshot from all resource groups
        async for snapshot in compute_client.snapshots.list():
            if snapshot.name == snapshot_name:
                progress.update(task, advance=100)
                return {
                    "name": snapshot_name,
                    "exists": True,
                    "resource_group": snapshot.id.split('/')[4],  # Extract resource group from the ID
                    "time_created": str(snapshot.time_created),
                    "size_gb": snapshot.disk_size_gb,
                    "state": snapshot.provisioning_state,
                }
        
        # If the loop completes without finding the snapshot, it doesn't exist
        progress.update(task, advance=100)
        return {"name": snapshot_name, "exists": False}
    except Exception as e:
        progress.update(task, advance=100)
        error_message = f"Error validating snapshot {snapshot_name}: {str(e)}"
        log_error(error_message)
        return {"name": snapshot_name, "exists": False, "error": error_message}

if __name__ == "__main__":
    snapshot_list_file = sys.argv[1] if len(sys.argv) > 1 else "snap_rid_list.txt"
    asyncio.run(validate_snapshots(snapshot_list_file))
