import csv

input_file = 'linux_vm-inventory.csv'

# Read the file into memory
with open(input_file, 'r') as file:
    reader = csv.reader(file)
    rows = list(reader)

# Make the changes
for row in rows:
    if len(row) >= 2:
        original_vm_name = row[1]
        vm_name = original_vm_name[-8:]  # Get the last 8 characters
        print(f"Original VM name: {original_vm_name}, Modified VM name: {vm_name}")
        row[1] = vm_name

# Write the changes back to the file
with open(input_file, 'w', newline='') as file:
    writer = csv.writer(file)
    writer.writerows(rows)
