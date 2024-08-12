import json
import time
import sys
import asyncio
from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from rich.prompt import Confirm
from rich.table import Table

# Import shared components from common.py
from common import (
    console, USER_ID, LOG_DIR, LOG_FILE, SUMMARY_FILE, SNAP_RID_LIST_FILE,
    INVENTORY_FILE, error_log_file, run_az_command, log_error, extract_snapshot_name,
    write_log
)

async def validate_snapshots(snapshot_list_file):
    console.print(
        Panel.fit(
            "[bold cyan]Starting snapshot validation...[/bold cyan]",
            border_style="cyan",
        )
    )

    with open(snapshot_list_file, "r") as file:
        snapshot_ids = file.read().splitlines()

    total_snapshots = len(snapshot_ids)
    validated_snapshots = []

    overall_progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        expand=True,
    )

    snapshot_progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("{task.completed:.0f}/{task.total:.0f}"),
        TimeRemainingColumn(),
        expand=True,
    )

    overall_task = overall_progress.add_task(
        "[green]Overall progress", total=total_snapshots
    )
    current_task = snapshot_progress.add_task(
        "Validating snapshot", total=1, visible=True
    )

    progress_group = Group(
        Panel(overall_progress, title="Overall Progress", border_style="green"),
        Panel(snapshot_progress, title="Current Snapshot", border_style="blue"),
    )

    snapshot_start_time = time.time()

    with Live(progress_group, refresh_per_second=10) as live:
        for snapshot_id in snapshot_ids:
            snapshot_name = extract_snapshot_name(snapshot_id)
            snapshot_info = {"id": snapshot_id, "exists": False, "name": snapshot_name}

            snapshot_progress.update(
                current_task,
                description=f"Validating: {snapshot_name:<50}",
                completed=0,
            )

            stdout, stderr, returncode = await run_az_command(
                f"az snapshot show --ids {snapshot_id} --query '{{name:name, resourceGroup:resourceGroup, timeCreated:timeCreated, diskSizeGb:diskSizeGb, provisioningState:provisioningState}}' -o json"
            )

            if returncode == 0:
                try:
                    details = json.loads(stdout)
                    snapshot_info.update(
                        {
                            "exists": True,
                            "resource_group": details["resourceGroup"],
                            "time_created": details["timeCreated"],
                            "size_gb": details["diskSizeGb"],
                            "state": details["provisioningState"],
                        }
                    )
                except json.JSONDecodeError:
                    await write_log(f"Failed to parse JSON for snapshot: {snapshot_id}")
                    log_error(f"Failed to parse JSON for snapshot: {snapshot_id}")
            else:
                snapshot_info["name"] = f"Not found: {snapshot_name}"
                await write_log(f"Failed to get details for snapshot: {snapshot_id}")
                await write_log(f"Error: {stderr}")

            validated_snapshots.append(snapshot_info)
            overall_progress.update(overall_task, advance=1)

            snapshot_end_time = time.time()
            validation_time = snapshot_end_time - snapshot_start_time
            snapshot_progress.update(
                current_task,
                description=f"Validated: {snapshot_name:<50} in {validation_time:.2f}s",
                completed=1,
            )

            time.sleep(0.5)

    end_time = time.time()
    runtime = end_time - snapshot_start_time

    console.print("\n")  # Add a newline for separation

    valid_table = Table(title="Valid Snapshots", box=box.ROUNDED)
    valid_table.add_column("Snapshot Name", style="cyan")
    valid_table.add_column("Status", style="green", justify="center")

    invalid_table = Table(title="Invalid Snapshots", box=box.ROUNDED)
    invalid_table.add_column("Snapshot Name", style="cyan")
    invalid_table.add_column("Status", style="red", justify="center")

    for snapshot in validated_snapshots:
        if snapshot["exists"]:
            valid_table.add_row(snapshot["name"], "✓")
        else:
            invalid_table.add_row(snapshot["name"], "✗")

    console.print(
        Panel(Group(valid_table, invalid_table), expand=False, border_style="green")
    )

    # Create summary table
    summary_table = Table(title="Snapshot Validation Summary", box=box.ROUNDED)
    summary_table.add_column("Category", style="cyan")
    summary_table.add_column("Count", style="magenta")

    summary_table.add_row("Total snapshots processed", str(total_snapshots))
    summary_table.add_row(
        "Valid snapshots", str(sum(1 for s in validated_snapshots if s["exists"]))
    )
    summary_table.add_row(
        "Invalid snapshots", str(sum(1 for s in validated_snapshots if not s["exists"]))
    )

    console.print(Panel(summary_table, expand=False, border_style="green"))

    console.print(
        Panel(
            f"[bold green]Validation complete![/bold green]\nRuntime: {runtime:.2f} seconds",
            border_style="green",
        )
    )

    if Confirm.ask("Do you want to save the validation results to a log file?"):
        from datetime import datetime
        import os
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        log_file = os.path.join(
            LOG_DIR, f"snapshot_validation_log_{USER_ID}_{timestamp}.txt"
        )
        with open(log_file, "w") as f:
            f.write("Snapshot Validation Results\n")
            f.write("===========================\n\n")
            for snapshot in validated_snapshots:
                f.write(f"Snapshot Name: {snapshot['name']}\n")
                f.write(f"Exists: {'Yes' if snapshot['exists'] else 'No'}\n")
                if snapshot["exists"]:
                    f.write(
                        f"Resource Group: {snapshot.get('resource_group', 'N/A')}\n"
                    )
                    f.write(f"Time Created: {snapshot.get('time_created', 'N/A')}\n")
                    f.write(f"Size (GB): {snapshot.get('size_gb', 'N/A')}\n")
                    f.write(f"State: {snapshot.get('state', 'N/A')}\n")
                f.write("\n")
            f.write(f"\nTotal snapshots processed: {total_snapshots}\n")
            f.write(
                f"Valid snapshots: {sum(1 for s in validated_snapshots if s['exists'])}\n"
            )
            f.write(
                f"Invalid snapshots: {sum(1 for s in validated_snapshots if not s['exists'])}\n"
            )
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

async def main(snapshot_list_file=None):
    if snapshot_list_file is None:
        snapshot_list_file = (
            console.input("Enter the path to the snapshot list file (default: snap_rid_list.txt): ")
            or "snap_rid_list.txt"
        )
    await validate_snapshots(snapshot_list_file)

if __name__ == "__main__":
    snapshot_list_file = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(snapshot_list_file))
