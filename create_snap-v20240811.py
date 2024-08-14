import os
import asyncio
import json
import datetime
import csv
from collections import defaultdict
from functools import lru_cache
from typing import List, Tuple, NamedTuple
import aiofiles
import aiohttp
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
import typer
import logging
from logging.handlers import RotatingFileHandler
import configparser
import backoff
import subprocess

# Custom exception hierarchy
class SnapshotCreatorError(Exception):
    """Base exception for Snapshot Creator"""

class VMInfoExtractionError(SnapshotCreatorError):
    """Raised when there's an error extracting VM information"""

class AzureCommandError(SnapshotCreatorError):
    """Raised when an Azure CLI command fails"""

class AzureLoginError(SnapshotCreatorError):
    """Raised when Azure login fails"""

# NamedTuple for structured data
class VMInfo(NamedTuple):
    resource_id: str
    vm_name: str
    resource_group: str
    disk_id: str

# Initialize Typer app
app = typer.Typer()

# Initialize Rich console
console = Console()

# Configure logging
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"snapshot_creation_log_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.txt")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(log_file, maxBytes=10000000, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load configuration
config = configparser.ConfigParser()
config.read('config.ini')

# Global variables
EXPIRE_DAYS = config.getint('Snapshot', 'expire_days', fallback=3)
SEMAPHORE_VALUE = config.getint('Azure', 'semaphore_value', fallback=10)
INVENTORY_FILE = config.get('Files', 'inventory_file', fallback='linux_vm-inventory.csv')
SNAPSHOT_VMLIST_FILE = config.get('Files', 'snapshot_vmlist_file', fallback='snapshot_vmlist.txt')
SNAP_RID_LIST_FILE = config.get('Files', 'snap_rid_list_file', fallback='snap_rid_list.txt')

semaphore = asyncio.Semaphore(SEMAPHORE_VALUE)
successful_snapshots = []
failed_snapshots = []

def verify_azure_login():
    try:
        # Check if the user is logged in
        result = subprocess.run(['az', 'account', 'show'], capture_output=True, text=True)
        if result.returncode != 0:
            logger.info("User is not logged in to Azure. Initiating login process...")
            login_result = subprocess.run(['az', 'login'], capture_output=True, text=True)
            if login_result.returncode != 0:
                raise AzureLoginError("Failed to log in to Azure. Please try logging in manually using 'az login'.")
            logger.info("Successfully logged in to Azure.")
        else:
            logger.info("User is already logged in to Azure.")
    except FileNotFoundError:
        raise AzureLoginError("Azure CLI (az) not found. Please install Azure CLI and try again.")

@lru_cache(maxsize=None)
def get_vm_info(hostname: str) -> str:
    with open(INVENTORY_FILE, 'r', newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            if hostname in row:
                return ','.join(row)
    return None

async def extract_vm_info() -> List[str]:
    if not os.path.exists(SNAPSHOT_VMLIST_FILE):
        raise VMInfoExtractionError(f"Snapshot VM list file '{SNAPSHOT_VMLIST_FILE}' not found.")

    async with aiofiles.open(SNAPSHOT_VMLIST_FILE, 'r') as f:
        vm_list = await f.read()
        vm_list = vm_list.splitlines()

    if not vm_list:
        raise VMInfoExtractionError(f"No VM information found in '{SNAPSHOT_VMLIST_FILE}'.")

    return vm_list

@backoff.on_exception(backoff.expo, aiohttp.ClientError, max_tries=3)
async def run_az_command(session: aiohttp.ClientSession, command: str) -> Tuple[str, str, int]:
    async with session.post(
        'https://management.azure.com/api/command',
        json={'command': command},
        headers={'Authorization': f"Bearer {os.environ.get('AZURE_ACCESS_TOKEN')}"}
    ) as response:
        if response.status == 200:
            result = await response.json()
            return result['stdout'], result['stderr'], result['exitCode']
        else:
            raise AzureCommandError(f"Command failed: {command}. Status: {response.status}")

async def write_snapshot_rid(snapshot_id: str):
    async with aiofiles.open(SNAP_RID_LIST_FILE, "a") as f:
        await f.write(f"{snapshot_id}\n")

async def process_vm(session: aiohttp.ClientSession, vm_info: VMInfo, chg_number: str, progress: Progress, task: int):
    async with semaphore:
        logger.info(f"Processing VM: {vm_info.vm_name}")
        logger.info(f"Resource ID: {vm_info.resource_id}")
        logger.info(f"Resource group: {vm_info.resource_group}")

        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        snapshot_name = f"RH_{chg_number}_{vm_info.vm_name}_{timestamp}"
        
        try:
            stdout, stderr, returncode = await run_az_command(
                session,
                f"az snapshot create --name {snapshot_name} --resource-group {vm_info.resource_group} --source {vm_info.disk_id}"
            )
            
            if returncode != 0:
                raise AzureCommandError(f"Failed to create snapshot for VM: {vm_info.vm_name}. Error: {stderr}")

            logger.info(f"Snapshot created: {snapshot_name}")
            logger.info(json.dumps(json.loads(stdout), indent=2))
            
            snapshot_data = json.loads(stdout)
            snapshot_id = snapshot_data.get('id')
            if snapshot_id:
                await write_snapshot_rid(snapshot_id)
                logger.info(f"Snapshot resource ID added to snap_rid_list.txt: {snapshot_id}")
                successful_snapshots.append((vm_info.vm_name, snapshot_name))
            else:
                logger.warning(f"Could not extract snapshot resource ID for {snapshot_name}")
                failed_snapshots.append((vm_info.vm_name, "Failed to extract snapshot ID"))

        except AzureCommandError as e:
            logger.error(str(e))
            failed_snapshots.append((vm_info.vm_name, str(e)))

        finally:
            progress.update(task, completed=100)

def group_vms_by_subscription(vm_list: List[str]) -> defaultdict:
    grouped_vms = defaultdict(list)
    for line in vm_list:
        resource_id, vm_name = line.rsplit(None, 1)
        subscription_id = resource_id.split("/")[2]
        grouped_vms[subscription_id].append((resource_id, vm_name))
    return grouped_vms

@app.command()
def main(
    host_file: str = typer.Option("host", help="Host file name"),
    chg_number: str = typer.Option(..., prompt=True, help="CHG number")
):
    try:
        verify_azure_login()
    except AzureLoginError as e:
        logger.error(str(e))
        return

    async def async_main():
        logger.info(f"CHG Number: {chg_number}")

        try:
            vm_list = await extract_vm_info()
        except VMInfoExtractionError as e:
            logger.error(str(e))
            return

        total_vms = len(vm_list)
        if total_vms == 0:
            logger.error("No valid VM information found.")
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

        async with aiohttp.ClientSession() as session:
            with Live(Panel(progress), refresh_per_second=4) as live:
                for subscription_id, vms in grouped_vms.items():
                    try:
                        # Switch to the current subscription
                        stdout, stderr, returncode = await run_az_command(session, f"az account set --subscription {subscription_id}")
                        if returncode != 0:
                            raise AzureCommandError(f"Failed to set subscription ID: {subscription_id}. Error: {stderr}")

                        logger.info(f"Switched to subscription: {subscription_id}")

                        tasks = []
                        for resource_id, vm_name in vms:
                            # Get resource group and disk ID for each VM
                            stdout, stderr, returncode = await run_az_command(
                                session,
                                f"az vm show --ids {resource_id} --query '{{resourceGroup:resourceGroup, diskId:storageProfile.osDisk.managedDisk.id}}' -o json"
                            )
                            if returncode != 0:
                                raise AzureCommandError(f"Failed to get VM details for {vm_name}. Error: {stderr}")

                            vm_details = json.loads(stdout)
                            vm_info = VMInfo(resource_id, vm_name, vm_details['resourceGroup'], vm_details['diskId'])

                            task = asyncio.create_task(process_vm(session, vm_info, chg_number, progress, vm_tasks[vm_name]))
                            tasks.append(task)
                        
                        await asyncio.gather(*tasks)
                        progress.update(overall_task, advance=len(vms))

                    except AzureCommandError as e:
                        logger.error(str(e))
                        for _, vm_name in vms:
                            failed_snapshots.append((vm_name, str(e)))
                            progress.update(vm_tasks[vm_name], completed=100)
                            progress.update(overall_task, advance=1)

        # Display summary table
        table = Table(title="Snapshot Creation Summary")
        table.add_column("Category", style="cyan")
        table.add_column("Count", style="magenta")
        table.add_row("Total VMs Processed", str(total_vms))
        table.add_row("Successful Snapshots", str(len(successful_snapshots)))
        table.add_row("Failed Snapshots", str(len(failed_snapshots)))
        console.print(table)

        # Write summary to file
        summary_file = os.path.join(log_dir, f"snapshot_summary_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.txt")
        async with aiofiles.open(summary_file, "w") as f:
            await f.write("Snapshot Creation Summary\n")
            await f.write("=========================\n\n")
            await f.write(f"Total VMs processed: {total_vms}\n")
            await f.write(f"Successful snapshots: {len(successful_snapshots)}\n")
            await f.write(f"Failed snapshots: {len(failed_snapshots)}\n\n")
            await f.write("Successful snapshots:\n")
            for vm, snapshot in successful_snapshots:
                await f.write(f"- {vm}: {snapshot}\n")
            await f.write("\nFailed snapshots:\n")
            for vm, error in failed_snapshots:
                await f.write(f"- {vm}: {error}\n")

        logger.info("Snapshot creation process completed.")
        logger.info(f"Detailed log: {log_file}")
        logger.info(f"Summary: {summary_file}")
        logger.info(f"Snapshot resource IDs: {SNAP_RID_LIST_FILE}")

    asyncio.run(async_main())

if __name__ == "__main__":
    app()