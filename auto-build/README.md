# auto-build

Runs [`../docs/deployment-guide.md`](../docs/deployment-guide.md) by executing
the guide itself: every runnable block, verbatim, in document order, in one bash session. A block
that fails is a guide problem, and the fix goes in `docs/`, not here.

| File | What it does |
|---|---|
| `extract.py` | Writes the guide's runnable blocks out as one bash script, or lists their IDs |
| `run.sh` | Applies your credentials, runs that script against the current cluster, logs each block |
| `check-paste.py` | Pastes every block into an interactive zsh and bash, with and without `shell-setup`, and reports the blocks that break |

## Credentials

One, the same one the guide's environment setup block sets to a placeholder: `ANTHROPIC_API_KEY`.
Attachment D also needs `OPENAI_API_KEY`, which its own environment block sets to a placeholder.

Export it, or put it in `auto-build/.env.local` (gitignored):

```bash
cat > auto-build/.env.local <<'X'
ANTHROPIC_API_KEY='...'
X
```

`run.sh` refuses to start if it is unset or still the guide's `<...>` placeholder. It is applied
immediately after the environment block, so versions and settings come from the guide and only the
secret comes from you. No block prints it.

## Usage

```bash
auto-build/run.sh --dry-run          # print the script that would run, run nothing
auto-build/run.sh                    # run every block
auto-build/run.sh --from <block-id>  # resume at a block
auto-build/run.sh --to <block-id>    # stop after a block
auto-build/run.sh --shell zsh         # run it under zsh (default: bash)
auto-build/run.sh --in-order          # every block in document order, when=manual ones included
auto-build/extract.py docs/deployment-guide.md --list   # block IDs, in order

auto-build/run.sh --attachment docs/attachments.md --part b   # Attachment B's path
auto-build/run.sh --attachment docs/attachments.md --part d --to d-step-14-codex-turn   # OpenAI, Attachment D
```

It runs against whatever `KUBECONFIG` points at, from a new, empty directory outside the repository
(deleted afterwards), so a block that depends on the repository or the current directory fails
here as it would for someone working from the guide alone. A certification passes under both
`--shell bash` and `--shell zsh`; zsh runs with `-f`, so no startup file is read. Step 1 creates the cluster, so a full run expects
no kind cluster named `KIND_CLUSTER_NAME` (default `agents`) to exist yet. To keep the build out of
your own kubeconfig and home directory, set both before running:

```bash
export KUBECONFIG=$PWD/kubeconfig ATE_BIN_DIR=$PWD/.bin
```

Each run writes `auto-build/logs/run-<UTC time>.log` (and the exact script it ran beside it, as `.sh`), with a `>>>BLOCK <id>` line before each
block's output and `>>>DONE` at the end. `run.sh` prints the exit status and the last marker, so a
failure names the block it stopped in.

## How blocks are found

A runnable block's fence carries a `run` tag and a stable ID:

````markdown
```bash run id=install-kagent
````

Blocks without the tag are output or illustration and are ignored. `when=manual` marks a block a
reader runs only when something has failed, and `run.sh` skips it.

Derive an ID from what the block creates, not from its step number, so renumbering does not break
`--from`.

## Resuming

`--from <id>` skips the blocks before `<id>` except two kinds, because later blocks depend on the
shell state they leave:

- the environment setup block (`environment-setup-env`), followed by your credentials;
- any block whose ID ends in `-helper` or `-helpers`, which only define shell functions.

Values a reader's shell would still hold from an earlier block, such as a session ID, are read
back from the cluster. A new block that exports a value later blocks need should either be
cheap to re-run or be added to `RESTORE` in `extract.py`.

## Exit status

`run.sh` exits non-zero if a block fails (with that command's status), and also if the script ends
without reaching `>>>DONE`. The second check exists because a block that reads stdin can swallow the
rest of a piped script and end the run with status 0; the script now runs from a file with stdin
closed, and the check catches anything that ends a run early some other way.

## Attachments

`docs/attachments.md` holds two kinds of runnable block.

**Replacements.** A block tagged `replaces=<id>` runs in place of the guide's block `<id>`, and one
tagged `after=<id>` runs right after it. Attachments B and D are written this way: B's blocks change
Up and Running for a cluster without the beta certificates API, and D's run Up and Running and
Operate on OpenAI, without copying the rest of the guide. `--part <letter>` applies only the blocks
of that attachment, whose ids start with `<letter>-`; without it, every attachment's replacements
apply. With `--attachment`, `--from` and `--to` work on the merged sequence, and `--list` shows each
block's tag.

A block tagged `after=environment-setup-env` belongs to the environment setup: it always runs, and
the credentials are applied again after it. `OPENAI_API_KEY`, which only Attachment D uses, is
optional: `run.sh` passes it on when it is set.

**Standalone blocks.** Attachment A's check and Attachment C's fixes are untagged and `when=manual`,
because each applies only in a particular situation. They run only when named with `--only`.

## Running chosen blocks

`--only <id>[,<id>...]` runs the environment block, the restore described under Resuming, every
helper block, and then the named blocks in the order given, including `when=manual` ones. It is how
Attachment C's fixes are run, and it works for any guide block too.

## Checking that blocks survive a paste

```bash
auto-build/check-paste.py
```

A reader pastes blocks into an interactive shell, which reads them differently from a script: zsh
does not take `#` as a comment unless `interactive_comments` is set, and both shells expand `!` in
double quotes as history. `zsh -n` checks none of that, and an interactive zsh refuses `noexec`, so
the check runs each block for real in `zsh -f -i` and `bash -i` with `PATH` pointing at stubs that
do nothing, and collects the errors the shell reports while reading it. Each block is pasted with
default options and after the guide's `shell-setup` block. It also flags variables named after
zsh's special parameters (`status`, `path` and the rest). It exits 1 if a block fails after
`shell-setup`; blocks that fail only without it are listed for information.

## Running the guide as a reader pastes it

`--in-order` runs every block in document order, `when=manual` ones included, because a reader
working from the PDF cannot see that tag and pastes each block as it comes. A manual block must be
harmless there: a read-back block acts only when the value it recovers is missing, and anything
that changes the cluster sits after Validate. A certification passes in this
mode as well as the normal one, Validate included.
