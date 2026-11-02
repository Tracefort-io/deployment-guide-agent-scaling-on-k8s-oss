#!/usr/bin/env python3
"""Paste every runnable block into an interactive zsh and an interactive bash, and report the
blocks that break when pasted.

  auto-build/check-paste.py [docs/deployment-guide.md docs/attachments.md]

A pasted block is read by an interactive shell, which treats it differently from a script: zsh
does not take `#` as a comment unless `interactive_comments` is set, and both shells expand `!` in
double quotes as a history reference. `zsh -n` checks none of that, and an interactive zsh refuses
`noexec`, so each block is run for real, with PATH pointing at a directory of stubs that print
nothing: no command reaches a cluster or the network. Errors are read from what the shell
reports while reading and running the block.

Each block is pasted twice per shell: with the shells' default options, and after the guide's
`shell-setup` block. A block that fails after `shell-setup` is a defect in the guide. One that
fails only with default options is listed for information: it is why `shell-setup` exists.
It also reads every block for variables named after zsh's special parameters (`status`, `path`,
`pipestatus`, `argv` and the rest): zsh makes some read-only and ties others to the environment,
so `local status=FAIL` fails and `local path=...` replaces PATH, in a script as much as in a paste.

Exit status 1 if any block fails after `shell-setup` or uses such a name.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = [os.path.join(HERE, "..", "docs", f) for f in ("deployment-guide.md", "attachments.md")]
STUBS = """kubectl helm kind docker curl jq openssl base64 sed awk grep cut tr head tail sort uniq wc sleep
seq mktemp rm cat tee shasum chmod mkdir touch mv cp pkill pgrep lsof date python3 zstd colima uname
xargs find stat env tput ps column nohup kubectl-ate pbcopy""".split()
# What an interactive shell says when it cannot read or expand a pasted line.
ERRORS = re.compile(
    r"parse error|unmatched|event not found|bad substitution|no matches found|bad pattern|"
    r"command not found: #|syntax error|unexpected EOF|unexpected end of file")


# Special parameters of zsh 5.9 (zshparam(1)) that a block could plausibly use as a variable name.
ZSH_SPECIAL = """status path pipestatus argv options parameters commands functions aliases builtins
history historywords jobdirs jobstates jobtexts modules nameddirs userdirs reswords signals funcstack
funcfiletrace funcsourcetrace widgets keymaps psvar watch prompt cdpath fpath manpath mailpath
module_path match mbegin mend zsh_eval_context ERRNO LINENO PPID RANDOM SECONDS TTYIDLE UID EUID
GID EGID USERNAME HISTCMD""".split()
SPECIAL_USE = re.compile(
    r"(?:\b(?:local|typeset|declare|readonly|export|integer|float)\s+(?:-\w+\s+)*|^\s*|[;&|]\s*|\bfor\s+|\bread\s+(?:-\w+\s+)*)"
    r"(" + "|".join(ZSH_SPECIAL) + r")(?==|\s+in\b|\s*$|\s*;)")


def special_names(body):
    hits = set()
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for m in SPECIAL_USE.finditer(line):
            hits.add(m.group(1))
    return sorted(hits)


def blocks(path):
    lines, out, i = open(path).read().splitlines(), [], 0
    while i < len(lines):
        m = re.match(r"^```bash run id=(\S+)", lines[i])
        if m:
            j = i + 1
            while lines[j] != "```":
                j += 1
            out.append((m.group(1), "\n".join(lines[i + 1:j])))
            i = j
        i += 1
    return out


def paste(shell, body, stubdir, home):
    cmd = {"zsh": [shutil.which("zsh"), "-f", "-i"], "bash": [shutil.which("bash"), "--norc", "--noprofile", "-i"]}[shell]
    env = {"PATH": stubdir, "HOME": home, "TERM": "dumb", "PS1": "", "PS2": "", "LANG": "C"}
    try:
        r = subprocess.run(cmd, input=body + "\nexit\n", capture_output=True, text=True, env=env,
                           timeout=30, cwd=home)
        text = r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        text = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        text += "\n(timed out)"
    return sorted({re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\r", "", l).strip()
                   for l in text.splitlines() if ERRORS.search(l)})


def main():
    docs = sys.argv[1:] or DOCS
    found = [(os.path.basename(p), bid, body) for p in docs for bid, body in blocks(p)]
    setup = next(body for _, bid, body in found if bid == "shell-setup")
    with tempfile.TemporaryDirectory() as stubdir, tempfile.TemporaryDirectory() as home:
        for s in STUBS:
            p = os.path.join(stubdir, s)
            with open(p, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(p, 0o755)
        failed, info = [], []
        for doc, bid, body in found:
            names = special_names(body)
            if names:
                failed.append((doc, bid, "zsh", ["uses zsh special parameter(s) as a variable: " + ", ".join(names)]))
            for shell in ("zsh", "bash"):
                after = paste(shell, setup + "\n" + body, stubdir, home)
                default = paste(shell, body, stubdir, home)
                if after:
                    failed.append((doc, bid, shell, after))
                elif default:
                    info.append((doc, bid, shell, default))
    print(f"blocks pasted: {len(found)}, each into zsh and bash, with and without shell-setup")
    for doc, bid, shell, errs in failed:
        print(f"FAIL  {doc} {bid} ({shell}, after shell-setup): " + " | ".join(errs))
    for doc, bid, shell, errs in info:
        print(f"info  {doc} {bid} ({shell}, default options only): " + " | ".join(errs))
    print(f"failing after shell-setup: {len(failed)}; failing only without it: {len(info)}")
    sys.exit(1 if failed else 0)


main()
