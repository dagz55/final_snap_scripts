import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from rich.progress import Progress
from rich.table import Table

# Import shared components from common.py
from common import (
    console,
    USER_ID,
    LOG_DIR,
    run_az_command,
    log_error,
    check_az_login,
    get_subscription_names,
    get_resource_groups_from_snapshots,
    check_and_remove_scope_locks,
    restore_scope_locks

)


def check_snapshot_exists(snapshot_id):
    command = f"az snapshot show --ids {snapshot_id}"
    result = run_az_command(command)
    return not result.startswith("Error:")


def process_snapshot(snapshot_id, subscription_names):
    try:
        parts = snapshot_id.split("/")
        if len(parts) < 9:
            log_error(f"Invalid snapshot ID format: {snapshot_id}")
            return None, "invalid", (snapshot_id, "Invalid snapshot ID format")

        subscription_id = parts[2]
        subscription_name = subscription_names.get(subscription_id, subscription_id)
        snapshot_name = parts[-1]

        # Check if snapshot exists
        if not check_snapshot_exists(snapshot_id):
            return subscription_name, "non-existent", snapshot_name

        return subscription_name, "valid", snapshot_name
    except Exception as e:
        log_error(f"Error processing snapshot {snapshot_id}: {str(e)}")
        return None, "error", (snapshot_id, str(e))


def delete_snapshot(snapshot_id):
    command = f"az snapshot delete --ids {snapshot_id}"
    result = run_az_command(command)
    return not result.startswith("Error:")


def pre_validate_snapshots(snapshot_ids, subscription_names):
    valid_snapshots = []
    results = defaultdict(lambda: defaultdict(list))

    with Progress() as progress:
        task = progress.add_task(
            "[cyan]Pre-validating snapshots...", total=len(snapshot_ids)
        )
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_snapshot = {
                executor.submit(
                    process_snapshot, snapshot_id, subscription_names
                ): snapshot_id
                for snapshot_id in snapshot_ids
            }
            for future in as_completed(future_to_snapshot):
                try:
                    subscription_name, status, data = future.result()
                    if subscription_name:
                        results[subscription_name][status].append(data)
                        if status == "valid":
                            valid_snapshots.append(future_to_snapshot[future])
                    else:
                        results["Unknown"][status].append(data)
                except Exception as e:
                    log_error(f"Error processing future: {str(e)}")
                progress.update(task, advance=1)

    return valid_snapshots, results


def delete_valid_snapshots(valid_snapshots, subscription_names):
    results = defaultdict(lambda: defaultdict(list))

    with Progress() as progress:
        task = progress.add_task(
            "[cyan]Deleting valid snapshots...", total=len(valid_snapshots)
        )
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_snapshot = {
                executor.submit(delete_snapshot, snapshot_id): snapshot_id
                for snapshot_id in valid_snapshots
            }
            for future in as_completed(future_to_snapshot):
                snapshot_id = future_to_snapshot[future]
                try:
                    success = future.result()
                    parts = snapshot_id.split("/")
                    subscription_id = parts[2]
                    subscription_name = subscription_names.get(
                        subscription_id, subscription_id
                    )
                    snapshot_name = parts[-1]
                    if success:
                        results[subscription_name]["deleted"].append(snapshot_name)
                    else:
                        results[subscription_name]["failed"].append(
                            (snapshot_name, "Deletion failed")
                        )
                except Exception as e:
                    log_error(f"Error deleting snapshot {snapshot_id}: {str(e)}")
                    results["Unknown"]["error"].append((snapshot_id, str(e)))
                progress.update(task, advance=1)

    return results


def print_summary(results):
    table = Table(title="Summary")
    table.add_column("Subscription", style="cyan")
    table.add_column("Valid Snapshots", style="green")
    table.add_column("Non-existent Snapshots", style="yellow")
    table.add_column("Deleted Snapshots", style="blue")
    table.add_column("Failed Deletions", style="red")

    total_valid = total_non_existent = total_deleted = total_failed = 0

    for subscription_name, data in results.items():
        valid_count = len(data["valid"])
        non_existent_count = len(data["non-existent"])
        deleted_count = len(data["deleted"])
        failed_count = len(data["failed"])
        table.add_row(
            subscription_name,
            str(valid_count),
            str(non_existent_count),
            str(deleted_count),
            str(failed_count),
        )

        total_valid += valid_count
        total_non_existent += non_existent_count
        total_deleted += deleted_count
        total_failed += failed_count

    table.add_row(
        "Total",
        str(total_valid),
        str(total_non_existent),
        str(total_deleted),
        str(total_failed),
        style="bold",
    )

    console.print(table)


def print_detailed_info(results):
    has_errors = any(data["failed"] or data["error"] for data in results.values())

    if has_errors:
        console.print("\n[bold red]Detailed Error Information:[/bold red]")

        for subscription_name, data in results.items():
            if data["failed"] or data["error"]:
                console.print(f"\n[cyan]Subscription: {subscription_name}[/cyan]")

                if data["failed"]:
                    console.print("\n[bold]Failed Deletions:[/bold]")
                    for snapshot, error in data["failed"]:
                        console.print(f"  [red]• {snapshot}: {error}[/red]")

                if data["error"]:
                    console.print("\n[bold]Errors:[/bold]")
                    for snapshot, error in data["error"]:
                        console.print(f"  [red]• {snapshot}: {error}[/red]")

    console.print("\n[bold green]Deleted Snapshots:[/bold green]")
    for subscription_name, data in results.items():
        if data["deleted"]:
            console.print(f"\n[cyan]Subscription: {subscription_name}[/cyan]")
            for snapshot in data["deleted"]:
                console.print(f"  [green]• {snapshot}[/green]")


def export_to_csv(results, filename):
    import csv

    with open(filename, "w", newline="") as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(["Subscription", "Status", "Snapshot", "Error"])
        for subscription, data in results.items():
            for status, snapshots in data.items():
                if status in ["deleted", "non-existent", "valid"]:
                    for snapshot in snapshots:
                        csvwriter.writerow([subscription, status, snapshot, ""])
                else:
                    for snapshot, error in snapshots:
                        csvwriter.writerow([subscription, status, snapshot, error])
    console.print(f"[green]✔ Results exported to {filename}[/green]")


def main():
    console.print("[cyan]Azure Snapshot Manager[/cyan]")
    console.print("=========================")

    try:
        if not check_az_login():
            console.print("[red]Please run 'az login' and try again.[/red]")
            return

        filename = (
            console.input(
                "Enter the filename with snapshot IDs (default: snap_rid_list.txt): "
            )
            or "snap_rid_list.txt"
        )
        if not os.path.isfile(filename):
            console.print(f"[bold red]File {filename} does not exist.[/bold red]")
            return

        start_time = time.time()

        subscription_names = get_subscription_names()
        if not subscription_names:
            console.print(
                "[bold red]Failed to fetch subscription names. Using IDs instead.[/bold red]"
            )

        try:
            with open(filename, "r") as f:
                snapshot_ids = f.read().splitlines()
        except Exception as e:
            console.print(f"[bold red]Error reading file {filename}: {e}[/bold red]")
            return

        if len(snapshot_ids) > 100:
            confirm = console.input(
                f"[yellow]You are about to process {len(snapshot_ids)} snapshots. Are you sure you want to proceed? (y/n): [/yellow]"
            )
            if confirm.lower() != "y":
                console.print("[red]Operation cancelled.[/red]")
                return

        valid_snapshots, pre_validation_results = pre_validate_snapshots(
            snapshot_ids, subscription_names
        )

        if not valid_snapshots:
            console.print(
                "[yellow]No valid snapshots found. Skipping scope lock removal and deletion process.[/yellow]"
            )
            results = pre_validation_results
        else:
            resource_groups = get_resource_groups_from_snapshots(valid_snapshots)
            console.print(
                f"[green]✔ Found {len(resource_groups)} resource groups from valid snapshot list.[/green]"
            )

            removed_locks = check_and_remove_scope_locks(resource_groups)
            console.print(f"[green]✔ Removed {len(removed_locks)} scope locks.[/green]")

            deletion_results = delete_valid_snapshots(
                valid_snapshots, subscription_names
            )

            console.print("[yellow]Restoring removed scope locks...[/yellow]")
            restored_locks = restore_scope_locks(removed_locks)
            console.print(f"[green]✔ Restored {restored_locks} scope locks.[/green]")

            # Merge pre-validation results with deletion results
            results = pre_validation_results
            for subscription, data in deletion_results.items():
                results[subscription].update(data)

        print_summary(results)
        print_detailed_info(results)

        end_time = time.time()
        total_runtime = end_time - start_time

        console.print(
            f"\n[bold green]✔ Total runtime: {total_runtime:.2f} seconds[/bold green]"
        )

        # Automatically create and write the log file
        from datetime import datetime

        current_time = datetime.now().strftime("%Y%m%d-%H%M%S")
        os.makedirs(LOG_DIR, exist_ok=True)
        log_filename = os.path.join(
            LOG_DIR, f"snapshot_deletion_log_{USER_ID}_{current_time}.txt"
        )

        with open(log_filename, "w") as log_file:
            log_file.write("Snapshot Deletion Log\n")
            log_file.write("=====================\n\n")
            log_file.write(f"User: {USER_ID}\n")
            log_file.write(f"Date and Time: {current_time}\n\n")
            log_file.write("Summary:\n")
            for subscription_name, data in results.items():
                log_file.write(f"\nSubscription: {subscription_name}\n")
                log_file.write(f"  Valid Snapshots: {len(data['valid'])}\n")
                log_file.write(
                    f"  Non-existent Snapshots: {len(data['non-existent'])}\n"
                )
                log_file.write(f"  Deleted Snapshots: {len(data['deleted'])}\n")
                log_file.write(f"  Failed Deletions: {len(data['failed'])}\n")

            log_file.write("\nDeleted Snapshots:\n")
            for subscription_name, data in results.items():
                if data["deleted"]:
                    log_file.write(f"\nSubscription: {subscription_name}\n")
                    for snapshot in data["deleted"]:
                        log_file.write(f"  • {snapshot}\n")

            log_file.write(f"\nTotal Runtime: {total_runtime:.2f} seconds\n")

        console.print(f"[green]✔ Log file created: {log_filename}[/green]")

    except Exception as e:
        log_error(f"An unexpected error occurred: {str(e)}")
        console.print(f"[red]An unexpected error occurred: {str(e)}[/red]")
        console.print(
            "[yellow]Please check the azure_manager.log file for more details.[/yellow]"
        )


if __name__ == "__main__":
    main()
