import json
import subprocess
import csv
from time import time
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TimeElapsedColumn

def get_subscriptions(console):
    console.print("[bold cyan]Fetching subscriptions...[/bold cyan]")
    command = ['az', 'account', 'list', '-o', 'json']
    result = subprocess.run(command, capture_output=True, text=True)
    
    if result.returncode != 0:
        console.print("[bold red]Error executing az account list command[/bold red]")
        console.print(result.stderr)
        return []

    subscriptions = json.loads(result.stdout)
    return subscriptions

def get_linux_vms(console):
    command = [
        'az', 'vm', 'list',
        '--query', "[?storageProfile.osDisk.osType=='Linux'].{SubscriptionId:id, Name:name}",
        '-o', 'json'
    ]
    
    result = subprocess.run(command, capture_output=True, text=True)
    
    if result.returncode != 0:
        console.print("[bold red]Error executing az vm list command[/bold red]")
        console.print(result.stderr)
        return []

    vms = json.loads(result.stdout)
    return vms

def write_to_csv(vms, console, filename='linux_vm-inventory.csv'):
    with open(filename, mode='w', newline='') as file:
        writer = csv.writer(file, delimiter='\t')  # Use tab as delimiter
        writer.writerow(['Subscription ID,VM Name/Hostname'])  # Header row
        for vm in vms:
            writer.writerow([f"{vm['SubscriptionId']} {vm['Name']}"])  # Use space between ID and Name
    console.print(f"[bold green]VM inventory has been written to {filename}[/bold green]")

def main():
    console = Console()
    start_time = time()
    
    subscriptions = get_subscriptions(console)
    all_vms = []

    with Progress(SpinnerColumn(), "[progress.description]{task.description}", BarColumn(), TimeElapsedColumn(), console=console) as progress:
        task = progress.add_task("Processing subscriptions...", total=len(subscriptions))

        for subscription in subscriptions:
            subscription_id = subscription['id']
            subscription_name = subscription['name']
            
            console.print(f"Setting subscription: [bold]{subscription_name}[/bold] ([cyan]{subscription_id}[/cyan])")
            command = ['az', 'account', 'set', '--subscription', subscription_id]
            result = subprocess.run(command, capture_output=True, text=True)
            
            if result.returncode != 0:
                console.print(f"[bold red]Error setting subscription {subscription_name}[/bold red]")
                console.print(result.stderr)
                continue

            vms = get_linux_vms(console)
            if vms:
                all_vms.extend(vms)
            
            progress.update(task, advance=1)

    if all_vms:
        write_to_csv(all_vms, console)

    end_time = time()
    duration = end_time - start_time
    
    console.print("\n[bold magenta]Summary[/bold magenta]")
    summary_table = Table(show_header=True, header_style="bold blue")
    summary_table.add_column("Total VMs")
    summary_table.add_column("Runtime (seconds)")
    summary_table.add_row(str(len(all_vms)), f"{duration:.2f}")

    console.print(summary_table)

if __name__ == "__main__":
    main()