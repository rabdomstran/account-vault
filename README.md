# account-vault

A local-first CLI for storing and retrieving account credentials on my own machine.

## Goals

- Encrypted at rest with a modern KDF; no plaintext secrets on disk
- Vault file lives outside the repo, under `~/.local/share/account-vault/`
- `add`, `get`, `list`, `rm`, `rotate` subcommands
- Import/export in a documented format
- Tested with synthetic fixtures only

## Status

Nothing built yet.
