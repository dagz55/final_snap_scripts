import os

def get_vm_info(hostname, inventory_file):
    with open(inventory_file, 'r') as f:
        for line in f:
            if hostname in line:
                return line.strip()
    return None

def main():
    list_file = input("Enter the path to the list file containing hostnames: ")
    inventory_file = 'linux_vm-inventory.csv'
    output_file = 'snapshot_vmlist.txt'

    if not os.path.exists(inventory_file):
        print(f"Error: Inventory file '{inventory_file}' not found.")
        return

    if not os.path.exists(list_file):
        print(f"Error: List file '{list_file}' not found.")
        return

    with open(list_file, 'r') as f:
        hostnames = f.read().splitlines()

    mode = 'a' if os.path.exists(output_file) else 'w'
    with open(output_file, mode) as f:
        for hostname in hostnames:
            vm_info = get_vm_info(hostname, inventory_file)
            if vm_info:
                f.write(f"{vm_info}\n")
            else:
                print(f"Warning: Information not found for hostname '{hostname}'")

    print(f"VM information has been {'appended to' if mode == 'a' else 'written to'} {output_file}")

if __name__ == "__main__":
    main()
