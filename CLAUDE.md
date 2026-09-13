@AGENTS.md

## Maintainer workflow

The maintainer stays close to this project and sets its direction and pace. The rules below exist so
they can ask questions **before** work lands.

### Planning

Plan each phase with `/plan-phase <n>`. Implementation starts only after the maintainer approves the plan.

### Pre-PR summary — before every PR

Fill in [`.github/pull_request_template.md`](.github/pull_request_template.md) and post it in chat,
then **wait for the maintainer's explicit approval**. Answer their questions and revise until
approved; then open the PR with the approved summary as its body. The summary is what gets
approved, not the diff. Keep it readable in five minutes and link to detail.

### Merging

Squash-merge a PR (`gh pr merge --squash --delete-branch`) once **both** hold: the maintainer has
approved its summary in chat, and the `gates` check is green. Until then the PR waits. (Standing
authorisation from the maintainer, 12 Sep 2026.)

### Phase close

In the PR that closes a phase:

1. Every _completed_ claim in the phase plan and `CHANGELOG.md` is re-verified against the code and
   the tests that prove it.
2. A design change gets a new versioned design document in `docs/`; prior versions stay readable.
3. The maintainer's status briefings are regenerated from the code. They live outside this
   repository; public progress is the phase's GitHub milestone, its issues, and `CHANGELOG.md`.

### Maintainer-only actions

Creating release tags, publishing packages, creating cloud accounts or credentials, spending money,
changing repository settings, and moving the contribution stage in [`GOVERNANCE.md`](GOVERNANCE.md).
