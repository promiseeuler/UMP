# Security Policy

## Supported versions

UMP is pre-1.0. Security fixes are applied to the latest revision of `main`.
No released version is currently certified for unsupervised physical operation.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
security advisory reporting for this repository. Include the affected revision,
deployment assumptions, reproduction steps, impact, and any proposed mitigation.

Maintainers should acknowledge a report within five business days. Disclosure
timing is coordinated after triage and a remediation is available. Never include
production certificates, private keys, robot telemetry, or owner data in a report.

## Safety boundary

UMP communicates semantic state and high-level assignments. It is not an
emergency stop, safety PLC, motion controller, or substitute for manufacturer
safety systems. Security findings that could cause unauthorized assignment,
identity confusion, state disclosure, or loss of audit integrity are treated as
high priority even when native robot controllers retain final authority.
