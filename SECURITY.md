# Security policy

VIGO for Python is experimental pre-release software. Do not use it as the sole
basis for safety-critical, operational, or passenger-information decisions.

## Supported version

Security fixes currently target the latest tagged `0.3.x` source. Older
snapshots may not receive fixes.

## Reporting a vulnerability

Use GitHub's private vulnerability-reporting feature for this repository. Do
not publish credentials, private transit data, exploit details, or sensitive
filesystem paths in a public issue.

Include the package version, Python version, operating system, installation
method, and the smallest non-sensitive reproduction you can provide.

## Runtime downloads

The bundled installer accepts only HTTPS download URLs and verifies the entire
runtime archive against a SHA-256 digest before extraction. It rejects path
traversal and symbolic-link entries and will not overwrite an unrelated
populated directory.
