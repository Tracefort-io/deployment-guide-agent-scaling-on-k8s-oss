# Contributing

Corrections and improvements are welcome, particularly from anyone who has run the guide and
found that reality disagrees with it.

---

## What belongs here

| Change | Where it goes |
|---|---|
| A command, manifest or output that is wrong | `docs/deployment-guide.md` |
| A troubleshooting entry, or a path the main guide does not take | `docs/attachments.md` |
| A value a resumed run must read back from the cluster | `RESTORE` in `auto-build/extract.py` |
| A way a pasted block breaks that the paste check misses | `auto-build/check-paste.py` |

**Fix the guide, not the automation.** The runner executes the commands the guide documents,
verbatim, so a command that fails is a guide problem. Correcting it there means the fix reaches
readers as well as the build.

---

## Before you open a pull request

Run the guide, or the part of it you changed, against a real cluster. `auto-build/run.sh --from
<block-id>` resumes at the block you changed; a full run needs a fresh cluster. A change that has
not been run is a suggestion rather than a correction, and saying so in the pull request is fine;
it just needs to be said.

Run `auto-build/check-paste.py`. It pastes every block into an interactive zsh and bash and fails on
anything that breaks after the guide's shell setup.

State which product version you ran against. A command that works on one release and not another
is useful information, and it is invisible unless you mention it.

---

## Commit messages

This repository uses [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>: <short imperative summary>

<why the change is needed, if it is not obvious from the summary>
```

| Type | Use for |
|---|---|
| `fix` | A wrong command, manifest, output or explanation |
| `feat` | New steps or capabilities, usually from a product release |
| `docs` | Wording, structure or formatting, with no change to what is run; also recording a certification |
| `chore` | Repository housekeeping and tooling |
| `refactor` | Restructuring that does not change behaviour |

Example:

```
fix: wait for the gateway's spans as well as the agent's before reading a trace

The collector writes the runtime's spans before the gateway's, so a check that
stopped at the first span counted no gateway spans.
```

---

## Pull request checklist

- [ ] Ran the affected steps against a live cluster
- [ ] Ran `auto-build/check-paste.py`
- [ ] Stated the product version it was run against
- [ ] Updated the guide, not only the automation
- [ ] Captured output in the guide matches what the cluster actually returned
- [ ] Uses only the open-source projects and APIs the guide pins
- [ ] No secret, password or local path in any block or output
- [ ] Commit messages follow the convention above

---

## Style

The guide is written to be run, not skimmed, and it is read from a PDF as well as from this
repository. A few conventions hold it together.

**Verified output only.** Every output block was captured from a live cluster. If you add one,
paste what you saw rather than what you expected. If a claim has not been verified, say so rather
than implying it has.

**No filler.** Sentences that announce a point rather than making it ("it is worth understanding
that", "this is the part where") get cut in review. State the fact. No em-dashes.

**Callouts are for behaviour that costs time.** Use `> [!WARNING]` for something that will bite,
`> [!NOTE]` for context that helps. Both should be rare enough to still carry weight.

**Runnable commands need a `run` tag** and a stable ID, so the runner can execute them:

````markdown
```bash run id=backend-anthropic-messages
````

Derive the ID from what the block creates rather than from its step number, so it survives
renumbering. A block whose ID ends in `-helper` or `-helpers` only defines shell functions, and is
replayed whenever a run resumes. Output blocks and illustrative snippets are left untagged.

**A block marked `when=manual` must still be safe to paste in order.** The tag keeps a block out of
the normal run, but a reader working from the PDF cannot see it and pastes every block as it comes.
So a manual block either does nothing unless it is needed (a read-back that acts only when the value
is missing, headed "Only if the guard above stopped you") or sits where running it is harmless (after
Validate). `auto-build/run.sh --in-order` runs manual blocks where they appear; Validate must pass
in that mode too.

**Every block pastes into bash and into zsh.** After the guide's `shell-setup` block:
- brace a variable that is followed by a colon (`"${CONV}:text"`): zsh reads `:u` or `:c` as a
  modifier;
- keep lists in arrays (`TURNS+=($!)`, `"${TURNS[@]}"`): zsh does not split an unquoted string;
- do not name a variable after a zsh special parameter (`status`, `path`, `argv` and the rest);
- chain a `${VAR:?message}` guard to what it protects with `&&`: an interactive bash carries on to
  the next line after a failed guard.

**Every block stands alone.** No block may call `git` or rely on the current directory: a file a
step uses is either in the guide or downloaded at a pinned version with a sha256 check. The runner
executes from an empty directory outside the repository, so such a dependency fails there.

**Blocks are safe to run twice.** A block that creates something replaces or skips what an earlier
attempt left, or the text says how to reset.

**Secrets never appear.** No block prints an API key, password or token. Secrets go into
Kubernetes over stdin with `kubectl create`, not `apply`, which would copy them into an annotation.
Passwords the guide generates are kept in a credentials file readable only by the reader.

**Port-forwards belong to their cluster.** Start each with `--context "kind-${KIND_CLUSTER_NAME}"`
and stop them by that context, so the guide never closes a forward to another cluster.

---

## Reporting a problem without fixing it

Open an issue. Include the command you ran, what you expected, what you got, and the product
version. That is enough to reproduce, and reproducing is most of the work.
