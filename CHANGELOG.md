# Changelog

## [1.0.1] - 2024-08-14
### Fixed
- Fixed the `move_invalid_snapshots` function in `validate_snapy.py` to correctly handle snapshot IDs and names.
- The function now reads the original snapshot list file to get the full snapshot IDs.
- Improved the logic for identifying valid and invalid snapshots based on the validation results.

## [1.0.0] - 2024-08-14
### Added
- Initial version of the snapshot validation script.
- Functionality to validate Azure snapshots.
- Option to save validation results to a log file.
- Option to move invalid snapshots to a separate file.
