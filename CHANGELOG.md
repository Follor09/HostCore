# Changelog

Releases are tags `vX.Y.Z`; installs on the stable update channel only follow these.

## v1.3.0 – 2026-10-24

First tagged release.

### New
- **Single sign-on (OpenID Connect):** Pocket ID, Authentik, Keycloak …; account linking, group mapping, SSO as second factor, local sign-in limited to administrators as emergency access.
- **GitOps:** compose stacks from a Git repository with diff, deploy as a task, automatic mode, drift detection and deploy webhook.
- **Emergency handbook:** restore steps per service generated from stacks, backups, dumps and GitOps; print view and Markdown.
- **Change requests** with four-eyes approval, maintenance windows and journal entries.
- **Access review** with sign-off and fingerprinted evidence.
- **Image policies** (allowed registries, fixed versions, block list) that warn or block deployments.
- **Secret scan** for keys, tokens and passwords in stack folders, scripts and documentation (masked).
- **Patch deadlines** per severity with exceptions and time-to-fix statistics.
- **Problem management** with known errors and solution via change request.
- **Compliance page:** baseline check modelled on BSI IT-Grundschutz / CIS, GDPR records of processing and TOM, evidence package for auditors.
- **Update channels:** *stable* (releases only, default for new installs) or *main* (every commit).

Everything before this release: see the [commit history](https://github.com/Follor09/RaspberryPi-Dash/commits/main) and [README.de.md](README.de.md).
