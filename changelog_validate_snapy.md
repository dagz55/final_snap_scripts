# Changelog for validate_snapy.py

## [1.1.0] - 2024-03-17
### Changed
- Improved display during the validation process to reduce eye strain
- Added overall progress bar for processing all snapshots
- Added individual progress bars for each snapshot validation
- Updated `validate_snapshot` function to update progress as it processes each snapshot
- Replaced scrolling lines with a more compact and visually appealing progress display
- Kept the summary table at the end for a clear overview of validation results

## [1.0.0] - Initial Release
### Added
- Initial implementation of snapshot validation script
- Ability to read snapshots from a file
- Validation of snapshots using Azure API calls
- Summary table of validation results
- Option to save validation results to a log file
- Error logging functionality
