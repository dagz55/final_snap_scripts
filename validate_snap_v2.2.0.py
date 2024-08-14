import asyncio
import datetime
import getpass
import json
import os
import sys
import time
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Confirm, Prompt

console = Console()

async def run_az_command(command):
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0:
        return stdout.decode().strip()
    else:
        error_message = stderr.decode().strip()
        if "AADSTS50173" in error_message:
            return None  # Token expired, but we don't want to display this error
        else:
            log_error(f"Error running command: {command}\nError message: {error_message}")
        return None

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

async def validate_snapshots(snapshot_list_file):
    console.print(
        Panel.fit(
            "[bold cyan]Starting snapshot validation...[/bold cyan]",
            border_style="cyan",
        )
    )

    start_time = time.time()
    
    with open(snapshot_list_file, "r") as file:
        snapshot_ids = file.read().splitlines()
    total_snapshots = len(snapshot_ids)

    validated_snapshots = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        overall_task = progress.add_task("[cyan]Processing snapshots...", total=total_snapshots)
        snapshot_tasks = []

        for snapshot_id in snapshot_ids:
            snapshot_name = extract_snapshot_name(snapshot_id)
            snapshot_task = progress.add_task(f"Validating: {snapshot_name}", total=100)
            snapshot_tasks.append(validate_snapshot(snapshot_id, progress, snapshot_task))

        validated_snapshots = await asyncio.gather(*snapshot_tasks)
        progress.update(overall_task, completed=total_snapshots)

    end_time = time.time()
    runtime = end_time - start_time

    valid_snapshots = [s for s in validated_snapshots if s["exists"]]
    invalid_snapshots = [s for s in validated_snapshots if not s["exists"]]

    # Display summary table
    console.print("\n")
    summary_table = Table(title="Snapshot Validation Summary", box=box.ROUNDED)
    summary_table.add_column("Category", style="cyan")
    summary_table.add_column("Count", style="magenta")
    summary_table.add_row("Total snapshots processed", str(total_snapshots))
    summary_table.add_row("Valid snapshots", str(len(valid_snapshots)))
    summary_table.add_row("Invalid snapshots", str(len(invalid_snapshots)))
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
            f.write(f"Valid snapshots: {len(valid_snapshots)}\n")
            f.write(f"Invalid snapshots: {len(invalid_snapshots)}\n")
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


    # Move invalid snapshots to a separate file and remove them from the original file
    if invalid_snapshots:
        if Confirm.ask(f"Do you want to move {len(invalid_snapshots)} invalid snapshots to a separate file?"):
            move_invalid_snapshots(invalid_snapshots, snapshot_ids, snapshot_list_file)


async def validate_snapshot(snapshot_id, progress, task):
    try:
        command = f"az snapshot show --ids '{snapshot_id}' --query '{{name:name, resourceGroup:resourceGroup, timeCreated:timeCreated, diskSizeGb:diskSizeGb, provisioningState:provisioningState}}' -o json"
        result = await run_az_command(command)
        
        if result:
            snapshot_info = json.loads(result)
            progress.update(task, advance=100)
            return {
                "name": snapshot_info['name'],
                "exists": True,
                "resource_group": snapshot_info['resourceGroup'],
                "time_created": snapshot_info['timeCreated'],
                "size_gb": snapshot_info['diskSizeGb'],
                "state": snapshot_info['provisioningState'],
            }
        else:
            progress.update(task, advance=100)
            return {"name": extract_snapshot_name(snapshot_id), "exists": False}
    except json.JSONDecodeError:
        progress.update(task, advance=100)
        log_error(f"Error decoding JSON for snapshot {snapshot_id}")
        return {"name": extract_snapshot_name(snapshot_id), "exists": False}
    except Exception as e:
        progress.update(task, advance=100)
        log_error(f"Error validating snapshot {snapshot_id}: {str(e)}")
        return {"name": extract_snapshot_name(snapshot_id), "exists": False}

def move_invalid_snapshots(invalid_snapshots, all_snapshot_ids, snapshot_list_file):
    invalid_names = [s["name"] for s in invalid_snapshots]
    invalid_ids = [id for id in all_snapshot_ids if extract_snapshot_name(id) in invalid_names]

    # Write invalid snapshots to new file
    invalid_file = "invalid_snap_rid.txt"
    with open(invalid_file, "w") as f:
        f.write("\n".join(invalid_ids))

    # Remove invalid snapshots from the original file
    valid_ids = [id for id in all_snapshot_ids if id not in invalid_ids]
    with open(snapshot_list_file, "w") as f:
        f.write("\n".join(valid_ids))

    console.print(f"[green]Invalid snapshots ({len(invalid_ids)}) moved to {invalid_file}[/green]")
    console.print(f"[green]Valid snapshots ({len(valid_ids)}) remain in {snapshot_list_file}[/green]")


if __name__ == "__main__":
    snapshot_list_file = sys.argv[1] if len(sys.argv) > 1 else "snap_rid_list.txt"
    asyncio.run(validate_snapshots(snapshot_list_file))