import subprocess
import datetime
import json
import os
import getpass
import time
from rich.console import Console, Group
from rich.table import Table
from rich.prompt import Confirm
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn
from rich.panel import Panel
from rich import box
from rich.live import Live

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

def extract_snapshot_name(snapshot_id):
    parts = snapshot_id.split('/')
    full_name = parts[-1]
    name_parts = full_name.rsplit('_', 1)
    return name_parts[0]

def validate_snapshots(snapshot_list_file):
    start_time = time.time()
    console.print(Panel.fit("[bold cyan]Starting snapshot validation...[/bold cyan]", border_style="cyan"))

    with open(snapshot_list_file, "r") as file:
        snapshot_ids = file.read().splitlines()

    total_snapshots = len(snapshot_ids)
    validated_snapshots = []

    overall_progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        expand=True
    )

    snapshot_progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        SpinnerColumn(style="aesthetic"),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        expand=True
    )

    overall_task = overall_progress.add_task("[green]Overall progress", total=total_snapshots)
    current_task = snapshot_progress.add_task("Validating snapshot", total=1, visible=True)

    progress_group = Group(
        Panel(overall_progress, title="Overall Progress", border_style="green"),
        Panel(snapshot_progress, title="Current Snapshot", border_style="blue")
    )

    with Live(progress_group, refresh_per_second=10) as live:
        for snapshot_id in snapshot_ids:
            snapshot_name = extract_snapshot_name(snapshot_id)
            snapshot_info = {'id': snapshot_id, 'exists': False, 'name': snapshot_name}

            start_time = time.time()
            snapshot_progress.update(current_task, description=f"Validating: {snapshot_name}", completed=0)

            details = run_az_command(f"az snapshot show --ids {snapshot_id} --query '{{name:name, resourceGroup:resourceGroup, timeCreated:timeCreated, diskSizeGb:diskSizeGb, provisioningState:provisioningState}}' -o json")

            if details:
                try:
                    details = json.loads(details)
                    snapshot_info.update({
                        'exists': True,
                        'resource_group': details['resourceGroup'],
                        'time_created': details['timeCreated'],
                        'size_gb': details['diskSizeGb'],
                        'state': details['provisioningState']
                    })
                except json.JSONDecodeError:
                    log_error(f"Failed to parse JSON for snapshot: {snapshot_id}")
            else:
                snapshot_info['name'] = f"Not found: {snapshot_name}"

            validated_snapshots.append(snapshot_info)
            overall_progress.update(overall_task, advance=1)
            
            end_time = time.time()
            validation_time = end_time - start_time
            snapshot_progress.update(current_task, description=f"Validated: {snapshot_name} in {validation_time:.2f}s", completed=1)

    end_time = time.time()
    runtime = end_time - start_time

    console.print("\n")  # Add a newline for separation

    # Create and display the table with snapshot names
    table = Table(title="Snapshot Validation Results", box=box.ROUNDED)
    table.add_column("Snapshot Name", style="cyan")
    table.add_column("Status", style="magenta", justify="center")

    for snapshot in validated_snapshots:
        status = "[green]✓[/green]" if snapshot['exists'] else "[red]✗[/red]"
        table.add_row(snapshot['name'], status)

    console.print(Panel(table, expand=False, border_style="green"))

    # Create summary table
    summary_table = Table(title="Snapshot Validation Summary", box=box.ROUNDED)
    summary_table.add_column("Category", style="cyan")
    summary_table.add_column("Count", style="magenta")

    summary_table.add_row("Total snapshots processed", str(total_snapshots))
    summary_table.add_row("Existing snapshots", str(sum(1 for s in validated_snapshots if s['exists'])))
    summary_table.add_row("Missing snapshots", str(sum(1 for s in validated_snapshots if not s['exists'])))

    console.print(Panel(summary_table, expand=False, border_style="green"))

    console.print(Panel(f"[bold green]Validation complete![/bold green]\nRuntime: {runtime:.2f} seconds", border_style="green"))

    if Confirm.ask("Do you want to save the validation results to a log file?"):
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        log_file = os.path.join(log_dir, f"snapshot_validation_log_{user_uid}_{timestamp}.txt")
        with open(log_file, "w") as f:
            f.write("Snapshot Validation Results\n")
            f.write("===========================\n\n")
            for snapshot in validated_snapshots:
                f.write(f"Snapshot Name: {snapshot['name']}\n")
                f.write(f"Exists: {'Yes' if snapshot['exists'] else 'No'}\n")
                if snapshot['exists']:
                    f.write(f"Resource Group: {snapshot.get('resource_group', 'N/A')}\n")
                    f.write(f"Time Created: {snapshot.get('time_created', 'N/A')}\n")
                    f.write(f"Size (GB): {snapshot.get('size_gb', 'N/A')}\n")
                    f.write(f"State: {snapshot.get('state', 'N/A')}\n")
                f.write("\n")
            f.write(f"\nTotal snapshots processed: {total_snapshots}\n")
            f.write(f"Existing snapshots: {sum(1 for s in validated_snapshots if s['exists'])}\n")
            f.write(f"Missing snapshots: {sum(1 for s in validated_snapshots if not s['exists'])}\n")        
            f.write(f"Runtime: {runtime:.2f} seconds\n")
        console.print(Panel(f"[bold green]Log file saved:[/bold green] {log_file}", border_style="green"))

    console.print(Panel(f"[yellow]Note: Errors and details have been logged to: {error_log_file}[/yellow]", border_style="yellow"))

if __name__ == "__main__":
    snapshot_list_file = input("Enter the path to the snapshot list file (default: snap_rid_list.txt): ") or "snap_rid_list.txt"
    validate_snapshots(snapshot_list_file)
