# Contributing

## One-time setup

You need git, Docker (Compose v2) and uv on the host. Nothing else. Then, from the repo root:

macOS or Linux:

```sh
uv run fd doctor
uv run fd secrets
uv run fd hooks
uv run fd up
```

Windows (PowerShell):

```powershell
uv run fd doctor
uv run fd secrets
uv run fd hooks
uv run fd up
```

`uv run fd hooks` is required once per clone. It points git at the versioned hooks in `infra/git-hooks/` (`git config core.hooksPath infra/git-hooks`, a repo-local setting that never touches your identity).

## Commits

- Small, conventional commits: `feat(api): ...`, `test(alloc): ...`, `docs: ...`, `chore(repo): ...`.
- Your own git identity is used. Do not change `user.name` or `user.email` for this repo to anything but your own.

## Rule R1: no AI attribution (this applies to everyone and every tool)

Claude, Claude Code, Anthropic and any other AI tool are never a contributor on this repository. No `Co-Authored-By`, `Co-developed-by`, `Assisted-by` or `Reviewed-by` trailer naming an AI tool, no "Generated with ..." line, no Claude Code URL and no robot emoji in commit messages, PR titles or bodies, tags or code comments, and no AI tool as author or committer. The hooks enforce it (patterns in `docs/decisions/D-003-attribution-patterns.md`):

- `commit-msg` rejects the commit being made.
- `pre-push` scans every commit being pushed (message, author, committer).
- `pre-commit` runs `uv run fd lint --staged` (no CR bytes).
- CI scans a pull request's commits with `uv run fd attribution-check range <base>..HEAD`.

**`git commit --no-verify` and `git push --no-verify` are forbidden.** They skip the hooks and cannot be blocked locally, which is why CI repeats the check. If a hook blocks you, fix the message or the identity and try again. If `uv` is missing the hooks block on purpose (they fail closed); install uv and run `uv run fd doctor`.

Before you push, look at what you are about to publish:

```sh
git log --format='%h %an <%ae> | %cn <%ce> | %s%n%b' origin/main..HEAD
```

## Line endings

Files are LF everywhere (`.gitattributes` forces it, `.editorconfig` tells editors). On Windows leave `core.autocrlf` alone; the attribute wins. `uv run fd lint` fails if any tracked text file contains a CR byte. Be careful with Windows PowerShell 5.1: `Set-Content` and `Out-File` write CRLF. Use your editor or `[IO.File]::WriteAllText(...)` for files that Linux containers read (`.env`, scripts, SQL).

## Checks before you push

```sh
uv run fd lint
uv run fd test-api
uv run fd test-web
```

The `fd` task CLI and the hooks have their own tests (they need only git and uv):

```sh
uv run python -m unittest discover -s tools/tests -t tools
```

## Skills and tools

Topical `fullstack-dev-skills` skills may be used as advice. The `project:*` workflow commands and the Atlassian integration are not used here. Plans and `CLAUDE.md` win over any skill.
