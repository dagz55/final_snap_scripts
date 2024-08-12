import asyncio
import subprocess
import inquirer
from common import console

async def run_script(script_name):
    console.print(f"Running script: {script_name}")
    
    cmd = ['python', script_name]
    
    if script_name == 'create_snapy.py':
        host_file = input("Please enter your host file (default: host): ") or "host"
        chg_number = input("Enter the CHG number: ")
        cmd.extend([host_file, chg_number])
    elif script_name == 'validate_snapy.py':
        snapshot_list_file = input("Enter the path to the snapshot list file (default: snap_rid_list.txt): ") or "snap_rid_list.txt"
        cmd.append(snapshot_list_file)
    elif script_name == 'delete_snapy.py':
        snapshot_list_file = input("Enter the filename with snapshot IDs (default: snap_rid_list.txt): ") or "snap_rid_list.txt"
        cmd.append(snapshot_list_file)
    else:
        console.print(f"Unknown script: {script_name}")
        return

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    async def print_output(stream, prefix):
        while True:
            line = await stream.readline()
            if not line:
                break
            console.print(f"{prefix}{line.decode().strip()}")

    await asyncio.gather(
        print_output(process.stdout, ""),
        print_output(process.stderr, "ERROR: ")
    )

    await process.wait()

def display_menu():
    questions = [
        inquirer.List('action',
            message="Choose an action",
            choices=[
                'Create Snapshot',
                'Validate Snapshot',
                'Delete Snapshot',
                'Quit'
            ],
        ),
    ]
    return inquirer.prompt(questions)['action']

async def main():
    while True:
        choice = display_menu()
        if choice == 'Create Snapshot':
            await run_script('create_snapy.py')
        elif choice == 'Validate Snapshot':
            await run_script('validate_snapy.py')
        elif choice == 'Delete Snapshot':
            await run_script('delete_snapy.py')
        elif choice == 'Quit':
            console.print("Exiting the program...")
            break
        
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    asyncio.run(main())