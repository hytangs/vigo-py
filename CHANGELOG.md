# Changelog

## Unreleased

- Added bounded resident-query, analytical-query, and raw-build deadlines with
  terminate/grace/kill recovery and a stable `VigoCliTimeoutError`.
- Required every CLI discovery path to pass one cached exact-version and
  required-command probe.
- Removed obsolete SQL-router claims from missing-plan failure receipts.
- Added Apple-silicon clean-wheel integration coverage against the published
  checksum-pinned VIGO runtime.

## 0.3.0 - 2026-08-31

- Established VIGO for Python as a standalone repository.
- Added a checksum-pinned installer for the verified VIGO 0.3.0 runtime.
- Preserved one canonical routing implementation through the VIGO CLI and
  native Rust kernel.
- Added clean-install, runtime-security, API-contract, and documentation checks.
