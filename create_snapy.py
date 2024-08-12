import os
import sys
import asyncio
import json
from collections import defaultdict
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from common import console, LOG_DIR, write_log, extract_vm_info, run_az_command

# Import shared components from common.py
from common import (
    SUMMARY_FILE,
    SNAP_RID_LIST_FILE,
    TIMESTAMP,
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
        stdout, stderr, returncode = await run_az_command(
            f"az snapshot create --name {snapshot_name} --resource-group {resource_group} --source {disk_id}"
        )

        if returncode != 0:
            await write_log(f"Failed to create snapshot for VM: {vm_name}")
            await write_log(f"Error: {stderr}")
            failed_snapshots.append((vm_name, "Failed to create snapshot"))
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
            else:
                await write_log(
                    f"Warning: Could not extract snapshot resource ID for {snapshot_name}"
                )
                failed_snapshots.append((vm_name, "Failed to extract snapshot ID"))

        progress.update(task, completed=100)
        sys.stdout.flush()


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

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        expand=True,
    )
    sys.stdout.flush()

    vm_tasks = {}
    for subscription_id, vms in grouped_vms.items():
        for resource_id, vm_name in vms:
            vm_tasks[vm_name] = progress.add_task(f"[cyan]{vm_name}", total=100)

    overall_task = progress.add_task("[bold green]Overall Progress", total=total_vms)

    with Live(Panel(progress), refresh_per_second=4) as live:
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
                    progress.update(vm_tasks[vm_name], completed=100)
                    sys.stdout.flush()
                    progress.update(overall_task, advance=1)
                    sys.stdout.flush()
                continue

            await write_log(f"Switched to subscription: {subscription_id}")

            tasks = []
            for resource_id, vm_name in vms:
                # Get resource group and disk ID for each VM
                stdout, stderr, returncode = await run_az_command(
                    f"az vm show --ids {resource_id} --query '{{resourceGroup:resourceGroup, diskId:storageProfile.osDisk.managedDisk.id}}' -o json"
                )
                if returncode != 0:
                    await write_log(f"Failed to get VM details for {vm_name}")
                    await write_log(f"Error: {stderr}")
                    failed_snapshots.append((vm_name, "Failed to get VM details"))
                    progress.update(vm_tasks[vm_name], completed=100)
                    sys.stdout.flush()
                    progress.update(overall_task, advance=1)
                    sys.stdout.flush()
                    continue

                vm_details = json.loads(stdout)
                resource_group = vm_details["resourceGroup"]
                disk_id = vm_details["diskId"]

                task = asyncio.create_task(
                    process_vm(
                        resource_id,
                        vm_name,
                        resource_group,
                        disk_id,
                        progress,
                        vm_tasks[vm_name],
                    )
                )
                tasks.append(task)

            await asyncio.gather(*tasks)
            progress.update(overall_task, advance=len(vms))
            sys.stdout.flush()

    # Display summary table
    table = Table(title="Snapshot Creation Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="magenta")
    table.add_row("Total VMs Processed", str(total_vms))
    table.add_row("Successful Snapshots", str(len(successful_snapshots)))
    table.add_row("Failed Snapshots", str(len(failed_snapshots)))
    console.print(table)

    # Calculate total_vms
    total_vms = len(successful_snapshots) + len(failed_snapshots)

    with open(SUMMARY_FILE, "w") as f:
        f.write("Snapshot Creation Summary\n")
        f.write("=========================\n\n")
        f.write(f"Total VMs processed: {total_vms}\n")
        f.write(f"Successful snapshots: {len(successful_snapshots)}\n")
        f.write(f"Failed snapshots: {len(failed_snapshots)}\n\n")

        f.write("Failed Snapshots:\n")
        for vm, error in failed_snapshots:
            f.write(f"- {vm}: {error}\n")

    console.print("\n[bold green]Snapshot creation process completed.[/bold green]")
    console.print(f"Detailed log: {write_log}")
    console.print(f"Summary: {SUMMARY_FILE}")

if __name__ == "__main__":
    host_file = sys.argv[1] if len(sys.argv) > 1 else None
    chg_number = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(main(host_file, chg_number))