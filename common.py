import os
import datetime
import getpass
import json
import aiofiles
import asyncio
from rich.console import Console

console = Console()

# Common constants
USER_ID = getpass.getuser()
TIMESTAMP = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, f"snapshot_log_{USER_ID}_{TIMESTAMP}.txt")
SUMMARY_FILE = os.path.join(LOG_DIR, f"snapshot_summary_{USER_ID}_{TIMESTAMP}.txt")
SNAP_RID_LIST_FILE = "snap_rid_list.txt"
INVENTORY_FILE = 'linux_vm-inventory.csv'
error_log_file = os.path.join(LOG_DIR, f"error_log_{USER_ID}_{TIMESTAMP}.txt")

# Common functions
async def run_az_command(command):
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return stdout.decode().strip(), stderr.decode().strip(), process.returncode
        else:
            console.print(f"[red]Error running command: {command}[/red]")
            console.print(f"[red]Error message: {stderr.decode().strip()}[/red]")
            return None, stderr.decode().strip(), process.returncode
    except Exception as e:
        console.print(f"[red]Exception occurred: {str(e)}[/red]")
        return None, str(e), 1

def log_error(message):
    with open(error_log_file, "a") as f:
        f.write(f"{datetime.datetime.now()}: {message}\n")

def extract_snapshot_name(snapshot_id):
    return snapshot_id.split("/")[-1]

async def write_log(message):
    async with aiofiles.open(LOG_FILE, "a") as f:
        await f.write(f"{datetime.datetime.now()}: {message}\n")

async def check_az_login():
    result = await run_az_command("az account show")
    return result is not None

async def get_subscription_names():
    result = await run_az_command("az account list --query '[].{id:id, name:name}' -o json")
    if result:
        subscriptions = json.loads(result)
        return {sub['id']: sub['name'] for sub in subscriptions}
    return {}

async def extract_vm_info(host_file):
    snapshot_vmlist_file = 'snapshot_vmlist.txt'
    if not os.path.exists(snapshot_vmlist_file):
        await write_log(f"Error: Snapshot VM list file '{snapshot_vmlist_file}' not found.")
        return None

    async with aiofiles.open(snapshot_vmlist_file, 'r') as f:
        vm_list = await f.read()
        vm_list = vm_list.splitlines()

    if not vm_list:
        await write_log(f"Error: No VM information found in '{snapshot_vmlist_file}'.")
        return None

    return vm_list

# Add any other common functions or classes here