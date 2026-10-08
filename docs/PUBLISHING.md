# Publish the initial repository

The maintainer must have authenticated GitHub write access. Repository ownership
or chat approval alone does not give a local Git process credentials. Never paste
a GitHub token, HA token or household journal into an issue or chat.

## From a prepared Git bundle

Transfer the reviewed `dwell-mind-ha.bundle` to your own authenticated computer:

```sh
git clone dwell-mind-ha.bundle dwell-mind-ha
cd dwell-mind-ha
git remote set-url origin https://github.com/spikked27/dwell-mind-ha.git
git push -u origin main
```

Authenticate locally through Git Credential Manager, GitHub Desktop, an existing
SSH setup, or GitHub CLI's browser login. If using GitHub CLI:

```sh
gh auth login --hostname github.com --web
gh auth setup-git
```

The credential must permit pushing `.github/workflows/` as well as source. For
fine-grained credentials, limit access to this repository and grant the necessary
contents/workflow permissions. Do not store credentials in the remote URL.

The source ZIP is an alternative for inspection/local builds. It contains the
same tracked source but no `.git` history; the bundle preserves the reviewed commits.
Neither export includes household reports, credentials, private journals or the
development virtual environment.

## After the first push

1. Open **Actions**. Confirm both test and publish workflows succeeded. Their
   Docker jobs are the first container build/smoke verification if no local Docker
   host was available during preparation.
2. Open the GHCR package linked to the repository and set its visibility to
   **Public** if necessary. Workflow publication alone may leave it private.
3. Verify an anonymous `docker pull ghcr.io/spikked27/dwell-mind-ha:edge`.
4. Install the reviewed Unraid XML user template from the `main` branch.
5. Pair the HA companion integration locally, select Areas/entities in its UI, and run the five-minute test.
6. Record the actual HA/Unraid versions and result before creating an alpha tag.

Optional repository settings for the owner:
- Enable private vulnerability reporting under Security.
- Set the description to “Local-first room observation and experimental machine learning for Home Assistant.”
- Add topics: `home-assistant`, `unraid`, `docker`, `machine-learning`, `privacy`, `occupancy`.
- Protect `main` with required test status checks once the initial workflows run.

This document provides a handoff, not evidence that any push, image build,
package publication or real-host installation has already happened.
