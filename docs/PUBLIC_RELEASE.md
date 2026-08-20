# Public Repository Readiness

UMP is intended to become a publicly inspectable interoperability protocol.
Public visibility is appropriate once repository history, governance, and
GitHub controls have been reviewed. Visibility is not a prerequisite for local
development and should not be changed merely to make a workflow pass.

## Automated audit

Run the repository audit from a clean checkout:

```sh
ump-public-readiness .
```

The command inspects every object reachable from all local Git references. It
reports high-confidence credential patterns in patch history, sensitive file
names, blobs larger than 5 MiB, author email privacy, required governance files,
worktree cleanliness, and top-level paths that exist only in history. Output is
JSON using profile `ump.public-readiness/v1`; a ready result exits with status
zero.

This repository contains an older, deleted Rust implementation in reachable
history. Publishing the existing repository will publish that implementation as
well. A maintainer must choose one of these approaches before changing
visibility:

1. Review and preserve the history, then record that decision by running
   `ump-public-readiness . --accept-historical-tree` from a clean checkout.
2. Create an intentionally fresh public history or perform a coordinated history
   rewrite. Rewriting published history is destructive and requires an explicit
   maintainer decision, backups, collaborator coordination, and force-push
   controls.

The acceptance flag records only that the operator made the review decision for
that invocation. It does not alter history, certify the old implementation, or
bypass any other check.

## Independent checks

The built-in patterns are deliberately narrow and cannot prove that arbitrary
secrets are absent. Before publication:

1. Run a maintained secret scanner against the complete Git history and review
   every finding.
2. Review commit messages, author identities, issues, pull requests, workflow
   logs, artifacts, releases, and tags for confidential information.
3. Confirm that public documentation does not disclose deployment credentials,
   private network locations, customer data, or unsafe robot-control details.
4. Confirm that every distributed dependency and asset has an acceptable
   license.

## GitHub controls

Before changing visibility, configure repository rules for the default branch,
required reviews, required CI checks, and blocked force pushes. Limit Actions
permissions to the minimum required and review environments, repository secrets,
deploy keys, webhooks, installed apps, and collaborator access.

After publication, enable GitHub secret scanning and push protection where the
account plan supports them. Verify the security advisory and private
vulnerability-reporting paths described in `SECURITY.md`. Keep release and ROS 2
workflow artifacts only for their documented retention periods and verify public
build-provenance attestations before treating them as qualification evidence.

Only change repository visibility after the automated audit passes, the
historical-tree decision is recorded, independent checks are complete, and the
GitHub controls have been verified by a repository owner.
