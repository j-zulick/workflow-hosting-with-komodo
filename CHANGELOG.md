# Changelog

## Unreleased

- Added loopback-only Compose examples for n8n and Windmill, Komodo Stack configuration, and backup and recovery commands.
- Both examples passed Compose configuration validation. The first release requires an isolated end-to-end workflow run on at least one example.

- Added synthetic upgrade fixtures, strict offline Compose checks, and a loopback runtime gate that compares image, preserved volume and workflow receipts.
- Updated the n8n pin to 2.49.7. Retained the known Windmill digest and PostgreSQL 17 layout.
