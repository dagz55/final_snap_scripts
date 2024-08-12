import os
import sys
import asyncio
import json
import time
from collections import defaultdict
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.console import Group
from rich import box
from common import console, LOG_DIR, write_log, extract_vm_info, run_az_command

# Import shared components from common.py
from common import (
    SUMMARY_FILE,
    SNAP_RID_LIST_FILE,
    TIMESTAMP,
    LOG_FILE,
)

# Global variables
chg_number = ""
expire_days = 3
semaphore = asyncio.Semaphore(10)
successful_snapshots = []
failed_snapshots = []


def write_snapshot_rid(snapshot_id):
    with open(SNAP_RID_LIST_FILE, "a") as f:
        f.write(f"{snapshot_id}\n")


async def process_vm(resource_id, vm_name, resource_group, disk_id, progress, task):
    async with semaphore:
        await write_log(f"Processing VM: {vm_name}")
        await write_log(f"Resource ID: {resource_id}")
        await write_log(f"Resource group: {resource_group}")

        snapshot_name = f"RH_{chg_number}_{vm_name}_{TIMESTAMP}"
        progress.update(task, description=f"Creating snapshot: {snapshot_name}")
        stdout, stderr, returncode = await run_az_command(
            f"az snapshot create --name {snapshot_name} --resource-group {resource_group} --source {disk_id}"
        )

        if returncode != 0:
            await write_log(f"Failed to create snapshot for VM: {vm_name}")
            await write_log(f"Error: {stderr}")
            failed_snapshots.append((vm_name, "Failed to create snapshot"))
            progress.update(task, description=f"[red]Failed: {vm_name}[/red]")
        else:
            await write_log(f"Snapshot created: {snapshot_name}")
            await write_log(json.dumps(json.loads(stdout), indent=2))

            snapshot_data = json.loads(stdout)
            snapshot_id = snapshot_data.get("id")
            if snapshot_id:
                write_snapshot_rid(snapshot_id)
                await write_log(
                    f"Snapshot resource ID added to snap_rid_list.txt: {snapshot_id}"
                )
                successful_snapshots.append((vm_name, snapshot_name))
                progress.update(task, description=f"[green]Success: {vm_name}[/green]")
            else:
                await write_log(
                    f"Warning: Could not extract snapshot resource ID for {snapshot_name}"
                )
                failed_snapshots.append((vm_name, "Failed to extract snapshot ID"))
                progress.update(task, description=f"[yellow]Warning: {vm_name}[/yellow]")

        progress.update(task, completed=100)


def group_vms_by_subscription(vm_list):
    grouped_vms = defaultdict(list)
    for line in vm_list:
        resource_id, vm_name = line.rsplit(None, 1)
        subscription_id = resource_id.split("/")[2]
        grouped_vms[subscription_id].append((resource_id, vm_name))
    return grouped_vms


async def main(host_file=None, input_chg_number=None):
    global chg_number
    console.print("[cyan]Azure Snapshot Creator[/cyan]")
    console.print("=========================")

    start_time = time.time()

    # Create log directory
    os.makedirs(LOG_DIR, exist_ok=True)

    # Get input from user if not provided as arguments
    host_file = (
        host_file
        or console.input("Please enter your host file (default: host): ")
        or "host"
    )
    chg_number = input_chg_number or console.input("Enter the CHG number: ")

    await write_log(f"CHG Number: {chg_number}")

    vm_list = await extract_vm_info(host_file)
    if vm_list is None:
        return

    total_vms = len(vm_list)
    if total_vms == 0:
        console.print("[bold red]Error: No valid VM information found.[/bold red]")
        return

    grouped_vms = group_vms_by_subscription(vm_list)

    overall_progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        expand=True,
    )

    vm_progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("{task.completed:.0f}/{task.total:.0f}"),
        TimeRemainingColumn(),
        expand=True,
    )

    overall_task = overall_progress.add_task("[green]Overall progress", total=total_vms)

    progress_group = Group(
        Panel(overall_progress, title="Overall Progress", border_style="green"),
        Panel(vm_progress, title="VM Progress", border_style="blue"),
    )

    with Live(progress_group, refresh_per_second=10) as live:
        for subscription_id, vms in grouped_vms.items():
            # Switch to the current subscription
            stdout, stderr, returncode = await run_az_command(
                f"az account set --subscription {subscription_id}"
            )
            if returncode != 0:
                await write_log(f"Failed to set subscription ID: {subscription_id}")
                await write_log(f"Error: {stderr}")
                for _, vm_name in vms:
                    failed_snapshots.append((vm_name, "Failed to set subscription"))
                    vm_progress.add_task(f"[red]Failed: {vm_name}[/red]", total=1, completed=1)
                    overall_progress.update(overall_task, advance=1)
                continue

            await write_log(f"Switched to subscription: {subscription_id}")

            for resource_id, vm_name in vms:
                # Get resource group and disk ID for each VM
                stdout, stderr, returncode = await run_az_command(
                    f"az vm show --ids {resource_id} --query '{{resourceGroup:resourceGroup, diskId:storageProfile.osDisk.managedDisk.id}}' -o json"
                )
                if returncode != 0:
                    await write_log(f"Failed to get VM details for {vm_name}")
                    await write_log(f"Error: {stderr}")
                    failed_snapshots.append((vm_name, "Failed to get VM details"))
                    vm_progress.add_task(f"[red]Failed: {vm_name}[/red]", total=1, completed=1)
                    overall_progress.update(overall_task, advance=1)
                    continue

                vm_details = json.loads(stdout)
                resource_group = vm_details["resourceGroup"]
                disk_id = vm_details["diskId"]

                vm_task = vm_progress.add_task(f"Processing: {vm_name}", total=100)
                await process_vm(
                    resource_id,
                    vm_name,
                    resource_group,
                    disk_id,
                    vm_progress,
                    vm_task,
                )
                overall_progress.update(overall_task, advance=1)

        # Ensure the progress bars are fully updated
        overall_progress.update(overall_task, completed=total_vms)
        for task_id in vm_progress.task_ids:
            vm_progress.update(task_id, completed=100)

    end_time = time.time()
    runtime = end_time - start_time

    # Display summary table
    console.print("\n")
    table = Table(title="Snapshot Creation Summary", box=box.ROUNDED)
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="magenta")
    table.add_row("Total VMs Processed", str(total_vms))
    table.add_row("Successful Snapshots", str(len(successful_snapshots)))
    table.add_row("Failed Snapshots", str(len(failed_snapshots)))
    table.add_row("Runtime", f"{runtime:.2f} seconds")
    console.print(table)

    # Calculate total_vms
    total_vms = len(successful_snapshots) + len(failed_snapshots)

    with open(SUMMARY_FILE, "w") as f:
        f.write("Snapshot Creation Summary\n")
        f.write("=========================\n\n")
        f.write(f"Total VMs processed: {total_vms}\n")
        f.write(f"Successful snapshots: {len(successful_snapshots)}\n")
        f.write(f"Failed snapshots: {len(failed_snapshots)}\n")
        f.write(f"Runtime: {runtime:.2f} seconds\n\n")

        f.write("Failed Snapshots:\n")
        for vm, error in failed_snapshots:
            f.write(f"- {vm}: {error}\n")

    console.print("\n[bold green]Snapshot creation process completed.[/bold green]")
    console.print(f"Detailed log: {LOG_FILE}")
    console.print(f"Summary: {SUMMARY_FILE}")

if __name__ == "__main__":
    host_file = sys.argv[1] if len(sys.argv) > 1 else None
    chg_number = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(main(host_file, chg_number))
