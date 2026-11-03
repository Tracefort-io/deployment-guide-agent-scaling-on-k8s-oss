#!/usr/bin/env python3
"""Write the guide's runnable blocks out as one bash script, in document order.

A runnable block is a fenced block whose info string is `bash run id=<id>`. Blocks
marked `when=manual` are skipped unless named with --only. Each block is preceded by
a marker line (`>>>BLOCK <id>`) so a log can be split per block.

The credentials are applied right after the environment setup block, from
__CRED_<NAME> variables that run.sh exports, so the placeholders the guide sets
never reach a command.

  extract.py <guide.md> [--from <id>] [--to <id>]   script on stdout
  extract.py <guide.md> --in-order                  every block, manual ones too, as a reader pastes them
  extract.py <guide.md> --list                      block ids, one per line
  extract.py <guide.md> --attachment <file.md> ...  apply an attachment's blocks
  extract.py <guide.md> --attachment <file.md> --part <letter> ...  only that attachment's blocks
  extract.py <guide.md> [--attachment <f>] --only <id>[,<id>...]

The shell setup block (`shell-setup`) and the environment block always run, whatever is selected.

With --from, blocks before <id> are skipped except those two and any
block whose id ends in -helper or -helpers (function definitions later blocks
call), and values a reader's shell would still hold are restored from the cluster.

With --attachment, an attachment block tagged `replaces=<id>` runs in place of the
guide's block <id>, and one tagged `after=<id>` runs right after it. Untagged
attachment blocks run only when named with --only. With --part <letter>, only the attachment
blocks whose id starts with <letter>- are applied, so one attachment's replacements do not bring in
another's.

An attachment block tagged `after=environment-setup-env` is part of the environment setup: it
always runs, and the credentials are applied again after it.

With --in-order, `when=manual` blocks run where they appear, like every other block: what a reader
gets by pasting the guide top to bottom. It combines with --from and --to.

With --only, the script is the shell setup and environment blocks, the restore, every helper block,
and then the named blocks (manual ones included), in the order given.
"""
import re
import sys

CREDENTIALS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]

# Each value is read back from where the guide stored it. The consumer key comes from the gateway's
# Secret (Step 2), the sign-in values from Keycloak's Secrets and the kagent release (Step 22). A
# conversation ID is the oldest actor of that agent's template.
RESTORE = """\
sec() { kubectl get secret "$2" -n "$1" -o jsonpath="{.data.$3}" 2>/dev/null | base64 --decode || true; }
if kubectl get secret llm-virtual-keys -n agentgateway-system >/dev/null 2>&1; then
  v=$(sec agentgateway-system llm-virtual-keys team-agents | jq -r .key); if [ -n "$v" ]; then export AGENT_CONSUMER_KEY=$v; fi
fi
oldest_conv() {
  kubectl-ate get actors -a kagent -o json 2>/dev/null \\
    | jq -r --arg t "$1-" '[.actors[]? | select(.actorTemplate.name // "" | test("^" + $t + "[0-9a-f]{12}$"))] | sort_by(.metadata.createTime) | .[0].metadata.name // empty' \\
    | sed 's/^session-//'
}
# Only overwrite what the cluster actually holds, so values exported beforehand survive.
setif() { if [ -n "$2" ]; then export "$1=$2"; fi; }
setif FIRST_CONTEXT "$(oldest_conv assistant)"
setif CLAUDE_CONTEXT "$(oldest_conv assistant-claude-code)"
setif MCP_SERVER_TOKEN "$(sec mcp-tools deployment-codes-token MCP_TOKEN)"
setif KEYCLOAK_ADMIN_PASSWORD "$(sec keycloak keycloak-admin password)"
setif KAGENT_OIDC_CLIENT_SECRET "$(sec keycloak keycloak-realm 'realm\\.json' | jq -r '.clients[]? | select(.clientId == "kagent-ui") | .secret' 2>/dev/null)"
setif KAGENT_COOKIE_SECRET "$(helm get values kagent -n kagent -o json 2>/dev/null | jq -r '."oauth2-proxy".config.cookieSecret // empty' 2>/dev/null)"
for u in alice bob; do
  v=$(sec keycloak keycloak-realm 'realm\\.json' | jq -r --arg u "$u" '.users[]? | select(.username == $u) | .credentials[0].value' 2>/dev/null)
  if [ -n "$v" ]; then export "KAGENT_$(echo "$u" | tr '[:lower:]' '[:upper:]')_PASSWORD=$v"; fi
done"""



def blocks(path):
    """(id, info, body) for every runnable block in a markdown file."""
    lines = open(path).read().splitlines()
    out, i = [], 0
    while i < len(lines):
        m = re.match(r"^```bash run id=(\S+)(.*)$", lines[i])
        if m:
            body = []
            i += 1
            while lines[i] != "```":
                body.append(lines[i])
                i += 1
            out.append((m.group(1), m.group(2), "\n".join(body)))
        i += 1
    return out


def attr(info, name):
    m = re.search(rf"\b{name}=(\S+)", info)
    return m.group(1) if m else None


ALWAYS = ("shell-setup", "environment-setup-env")


def is_helper(bid):
    return bid.endswith("-helper") or bid.endswith("-helpers")


def arg(args, name):
    return args[args.index(name) + 1] if name in args else None


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    found = blocks(args[0])
    attachment = arg(args, "--attachment")
    extra = blocks(attachment) if attachment else []
    part = arg(args, "--part")
    if part:
        extra = [e for e in extra if e[0].startswith(part.lower() + "-")]
    for bid, info, _ in extra:
        target = attr(info, "replaces") or attr(info, "after")
        if target and target not in [b[0] for b in found]:
            sys.exit(f"attachment block {bid} names {target}, which the guide does not have")
    merged = []
    for b in found:
        rep = [e for e in extra if attr(e[1], "replaces") == b[0]]
        merged.append(rep[0] if rep else b)
        merged += [e for e in extra if attr(e[1], "after") == b[0]]
    standalone = [e for e in extra if not attr(e[1], "replaces") and not attr(e[1], "after")]
    ids = [b[0] for b in merged + standalone]
    if len(set(ids)) != len(ids):
        sys.exit("duplicate block ids: " + ", ".join(sorted({i for i in ids if ids.count(i) > 1})))
    if "--list" in args:
        for b in merged + standalone:
            tags = (["when=manual"] if "when=manual" in b[1] else []) + [f"{k}={attr(b[1], k)}" for k in ("replaces", "after") if attr(b[1], k)]
            print(b[0] + (f"  ({', '.join(tags)})" if tags else ""))
        return

    script = ["set -eo pipefail"]

    env_extra = [e[0] for e in extra if attr(e[1], "after") == "environment-setup-env"]
    always = ALWAYS + tuple(env_extra)

    def emit(b):
        script.extend([f"echo '>>>BLOCK {b[0]}'", b[2]])
        if b[0] in ("environment-setup-env", *env_extra):
            script.extend(f'if [ -n "${{__CRED_{c}:-}}" ]; then export {c}="$__CRED_{c}"; fi' for c in CREDENTIALS)
            script.append('export PATH="$ATE_BIN_DIR:$PATH"')

    only = arg(args, "--only")
    if only:
        want = only.split(",")
        index = {b[0]: b for b in merged + standalone}
        missing = [w for w in want if w not in index]
        if missing:
            sys.exit("no block with id " + ", ".join(missing))
        for a in always:
            emit(index[a])
        script.append(RESTORE)
        for b in merged:
            if is_helper(b[0]) and b[0] not in want:
                emit(b)
        for w in want:
            emit(index[w])
        script.append("echo '>>>DONE'")
        print("\n".join(script))
        return

    frm, to = arg(args, "--from"), arg(args, "--to")
    mids = [b[0] for b in merged]
    for want in (frm, to):
        if want and want not in mids:
            sys.exit(f"no block with id {want}")
    start = mids.index(frm) if frm else 0
    end = mids.index(to) if to else len(merged) - 1
    for n, b in enumerate(merged):
        if "when=manual" in b[1] and "--in-order" not in args:
            continue
        if n > end:
            break
        if n < start and not (b[0] in always or is_helper(b[0])):
            continue
        emit(b)
        if b[0] == always[-1] and frm:
            script.append(RESTORE)
    script.append("echo '>>>DONE'")
    print("\n".join(script))


main()
