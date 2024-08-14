# Changelog for excreator.py

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2024-08-11
### Added
- Azure login verification feature
- New custom exception `AzureLoginError` for handling login-related errors
- `verify_azure_login()` function to check and initiate Azure login if necessary

### Changed
- Updated `main()` function to verify Azure login before proceeding with snapshot creation

## [2.0.0] - 2024-08-10
### Added
- Asynchronous operations using `asyncio` and `aiohttp`
- Caching for `get_vm_info` function using `@lru_cache`
- CSV module for reading the inventory file
- Retry logic with exponential backoff for `run_az_command` using the `backoff` library
- Logging using the `logging` module
- Better CLI interface and argument parsing using `Typer`
- Configuration file (config.ini) for default values and settings
- Custom exception hierarchy for better error handling
- Rich progress bars and console output

### Changed
- Refactored code to use asynchronous file I/O operations with `aiofiles`
- Replaced subprocess calls to az CLI with `aiohttp` requests
- Implemented structured data using `NamedTuple` (VMInfo) instead of regular tuples
- Improved error handling and input validation

### Removed
- Custom `write_log` function in favor of the `logging` module

## [1.2.0] - 2024-08-09
### Added
- Parallel processing for VM operations
- Progress bar for overall snapshot creation process
- Summary table at the end of the script execution

### Changed
- Improved error handling and logging
- Refactored code for better readability and maintainability

## [1.1.0] - 2024-08-08
### Added
- Support for processing multiple subscriptions
- Automatic creation of log directory if it doesn't exist

### Changed
- Improved VM information extraction process
- Enhanced error messages and logging

## [1.0.0] - 2024-08-07
### Added
- Initial release of the Azure Snapshot Creator script
- Basic functionality to create snapshots for Azure VMs
- Logging of snapshot creation process
- Error handling for common issues
- Support for reading VM information from a file
- Ability to specify CHG number for snapshot naming

