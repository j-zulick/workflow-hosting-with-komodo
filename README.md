How do you update a self-hosted workflow service and know it still works afterward?

# Workflow hosting with Komodo

Deploy a loopback-only Docker Compose lab, record the running image, and repeat the same workflow after an update.

- [Start with n8n](docs/n8n.md) to run a two-node workflow and inspect its execution.
- [Start with Windmill](docs/windmill.md) to run a Bash script and check the worker and result.
- [Run the upgrade verification gate](docs/verification.md) to compare the pre-update and post-update receipts.
- [Back up, upgrade, and recover](docs/upgrades-and-recovery.md) to preserve data and rehearse a restore.

Both examples passed real Compose configuration validation. The upgrade gate passed its regression suite with simulated runtime responses, and both workflow snippets executed with the expected output. Native container, Komodo, and restore rehearsals remain pending.

## Where I use this setup

PageVet has an [n8n workflow](https://github.com/j-zulick/pagevet/blob/main/integrations/n8n/pagevet-guard.workflow.json) and a [Windmill rescan script](https://github.com/j-zulick/pagevet/blob/main/integrations/windmill/pagevet_rescan.py). The n8n guide draws on operating n8n through Komodo. The Windmill guide is a reference deployment.

[Komodo](https://github.com/moghtech/komodo), [n8n](https://github.com/n8n-io/n8n), and [Windmill](https://github.com/windmill-labs/windmill) are upstream projects. Joseph Zulick contributed the deployment examples, operating guidance, and workflow verification procedures. See the [license](LICENSE).
