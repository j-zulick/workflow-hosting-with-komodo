# Changelog

## 0.1.0

- Add loopback-only Compose examples for n8n and Windmill, Komodo Stack configuration, and backup and recovery commands.
- Verify an isolated n8n upgrade from 2.49.6 to 2.49.7 using the same saved workflow, named volume and recorded image identities.

- Added synthetic upgrade fixtures, strict offline Compose checks, and a loopback runtime gate that compares image, preserved volume and workflow receipts.
- Updated the n8n pin to 2.49.7. Retained the known Windmill digest and PostgreSQL 17 layout.
