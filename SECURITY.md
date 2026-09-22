# Security policy

## Report privately

Please use [GitHub private vulnerability reporting](https://github.com/zzccchen/token-pulse/security/advisories/new) for vulnerabilities involving private data, unsafe file access, or code execution. Do not open a public issue containing credentials, real conversation logs, or exploit details that expose other users.

Include the affected revision/version, environment, impact, and a minimal synthetic reproduction. Share only the information needed to investigate. If the private form is unavailable, open a public issue asking for a private contact method without disclosing the vulnerability.

## Supported scope

Security fixes currently target the latest code on `main`. No older release line has a separate maintenance commitment. This is an early community project without a guaranteed response deadline or a bug bounty.

TokenPulse reads local files and stores local statistics. It does not need Codex credentials or transmit telemetry. Preferences and exports may contain sensitive paths or usage metadata; consult the [data policy](docs/usage.md#data-and-privacy). Security review should include parser bounds, path containment, CSV escaping, dependency behavior, and accidental persistence of private content.
