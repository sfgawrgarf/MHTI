# Security Policy

## Supported versions

Security fixes are applied to the latest release and the current `main` branch.
Older releases should be upgraded before requesting a security backport.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or a private Security
Advisory for this repository. Do not open a public issue for an undisclosed
vulnerability and do not include credentials, access tokens, cookies, database
files, 115 login data, or user media in a report.

Include the affected version, impact, reproduction steps, and any suggested
mitigation. A maintainer will acknowledge the report and coordinate disclosure
after a fix is available.

## Operational safety

Use test data when reproducing an issue. Never ask users to delete or upload
their `data` directory, database, encryption key, or 115 client state.
