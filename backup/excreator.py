import os
import asyncio
import json
import datetime
import getpass
import csv
from collections import defaultdict
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
import aiofiles
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()

# Global variables
user_id = getpass.getuser()
timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
log_dir = "logs"
log_file = os.path.join(log_dir, f"snapshot_creation_log_{user_id}_{timestamp}.txt")
summary_file = os.path.join(log_dir, f"snapshot_summary_{user_id}_{timestamp}.txt")
snap_rid_list_file = "snap_rid_list.txt"
chg_number = ""
expire_days = 3
semaphore = asyncio.Semaphore(10)
successful_snapshots = []
failed_snapshots = []
inventory_file = 'linux_vm-inventory.csv'

@lru_cache(maxsize=None)
def get_vm_info(hostname, inventory_file):
    with open(inventory_file, 'r', newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            if hostname in row:
                return ','.join(row)
    return None

async def extract_vm_info(host_file):
    snapshot_vmlist_file = 'snapshot_vmlist.txt'
    if not os.path.exists(snapshot_vmlist_file):
        console.print(f"[bold red]Error: Snapshot VM list file '{snapshot_vmlist_file}' not found.[/bold red]")
        return None

    async with aiofiles.open(snapshot_vmlist_file, 'r') as f:
        vm_list = await f.read()
        vm_list = vm_list.splitlines()

    if not vm_list:
        console.print(f"[bold red]Error: No VM information found in '{snapshot_vmlist_file}'.[/bold red]")
        return None

    return vm_list

async def run_az_command(command, max_retries=3, delay=5):
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
            write_log(f"Command failed (attempt {attempt + 1}): {command}")
            write_log(f"Error: {stderr.decode().strip()}")
            if attempt < max_retries - 1:
                write_log(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
    return "", stderr.decode().strip(), process.returncode

async def write_log(message):
    async with aiofiles.open(log_file, "a") as f:
        await f.write(f"{datetime.datetime.now()}: {message}\n")

def write_snapshot_rid(snapshot_id):
    with open(snap_rid_list_file, "a") as f:
        f.write(f"{snapshot_id}\n")

async def process_vm(resource_id, vm_name, resource_group, disk_id, progress, task):
    async with semaphore:
        await write_log(f"Processing VM: {vm_name}")
        await write_log(f"Resource ID: {resource_id}")
        await write_log(f"Resource group: {resource_group}")

        snapshot_name = f"RH_{chg_number}_{vm_name}_{timestamp}"
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
            snapshot_id = snapshot_data.get('id')
            if snapshot_id:
                write_snapshot_rid(snapshot_id)
                await write_log(f"Snapshot resource ID added to snap_rid_list.txt: {snapshot_id}")
                successful_snapshots.append((vm_name, snapshot_name))
            else:
                await write_log(f"Warning: Could not extract snapshot resource ID for {snapshot_name}")
                failed_snapshots.append((vm_name, "Failed to extract snapshot ID"))

        progress.update(task, completed=100)

def group_vms_by_subscription(vm_list):
    grouped_vms = defaultdict(list)
    for line in vm_list:
        resource_id, vm_name = line.rsplit(None, 1)
        subscription_id = resource_id.split("/")[2]
        grouped_vms[subscription_id].append((resource_id, vm_name))
    return grouped_vms

async def main():
    global chg_number

    console.print("[cyan]Azure Snapshot Creator[/cyan]")
    console.print("=========================")

    # Create log directory
    os.makedirs(log_dir, exist_ok=True)

    # Get input from user
    host_file = console.input("Please enter your host file (default: host): ") or "host"
    chg_number = console.input("Enter the CHG number: ")
    
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
        expand=True
    )
    
    vm_tasks = {}
    for subscription_id, vms in grouped_vms.items():
        for resource_id, vm_name in vms:
            vm_tasks[vm_name] = progress.add_task(f"[cyan]{vm_name}", total=100)

    overall_task = progress.add_task("[bold green]Overall Progress", total=total_vms)

    with Live(Panel(progress), refresh_per_second=4) as live:
        for subscription_id, vms in grouped_vms.items():
            # Switch to the current subscription
            stdout, stderr, returncode = await run_az_command(f"az account set --subscription {subscription_id}")
            if returncode != 0:
                await write_log(f"Failed to set subscription ID: {subscription_id}")
                await write_log(f"Error: {stderr}")
                for _, vm_name in vms:
                    failed_snapshots.append((vm_name, "Failed to set subscription"))
                    progress.update(vm_tasks[vm_name], completed=100)
                    progress.update(overall_task, advance=1)
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
                    progress.update(overall_task, advance=1)
                    continue

                vm_details = json.loads(stdout)
                resource_group = vm_details['resourceGroup']
                disk_id = vm_details['diskId']

                task = asyncio.create_task(process_vm(resource_id, vm_name, resource_group, disk_id, progress, vm_tasks[vm_name]))
                tasks.append(task)
            
            await asyncio.gather(*tasks)
            progress.update(overall_task, advance=len(vms))

    # Display summary table
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
        f.write("=========================\n\n")
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

if __name__ == "__main__":
    asyncio.run(main())
