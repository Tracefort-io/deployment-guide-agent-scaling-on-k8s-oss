#!/bin/bash
# Run the guide's blocks verbatim against the cluster your KUBECONFIG points at.
#
#   auto-build/run.sh                  every block, in order
#   auto-build/run.sh --from <id>      resume at a block
#   auto-build/run.sh --to <id>        stop after a block
#   auto-build/run.sh --dry-run        print the script, run nothing
#   auto-build/run.sh --shell zsh ...  run it under zsh instead of bash
#   auto-build/run.sh --in-order       include when=manual blocks where they appear, as a reader pasting
#                                      the guide top to bottom would
#
# The script runs from a new, empty directory outside the repository, so a block that depends on
# the repository or on the current directory fails here as it would for a reader.
#
# Credentials come from the environment, or from auto-build/.env.local (gitignored).
# The log, one section per block, goes to auto-build/logs/.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
GUIDE="$HERE/../docs/deployment-guide.md"
CREDS="ANTHROPIC_API_KEY"

if [ -f "$HERE/.env.local" ]; then set -a; . "$HERE/.env.local"; set +a; fi
# The sign-in step saves the UI users' generated passwords here, and reuses any already set, so a rebuilt
# cluster keeps the same sign-in.
export KAGENT_UI_CREDENTIALS_FILE="$HERE/.env.local"
for c in $CREDS; do
  v="${!c:-}"
  if [ -z "$v" ] || [ "${v#<}" != "$v" ]; then
    echo "run.sh: $c is not set (or is still the guide's placeholder)" >&2; exit 2
  fi
  export "__CRED_$c=$v"
done
# Optional: Attachment D's provider key, used only by its blocks.
for c in OPENAI_API_KEY; do
  v="${!c:-}"
  if [ -n "$v" ] && [ "${v#<}" = "$v" ]; then export "__CRED_$c=$v"; fi
done

RUN_SHELL=bash; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in --shell) RUN_SHELL="$2"; shift 2 ;; *) ARGS+=("$1"); shift ;; esac
done
case "$RUN_SHELL" in bash|zsh) ;; *) echo "run.sh: --shell must be bash or zsh" >&2; exit 2 ;; esac
SHELL_BIN=$(command -v "$RUN_SHELL") || { echo "run.sh: $RUN_SHELL not found" >&2; exit 2; }
set -- ${ARGS[@]+"${ARGS[@]}"}
SCRIPT="$(python3 -I "$HERE/extract.py" "$GUIDE" "$@")" || exit 2
case " $* " in *" --dry-run "*) printf '%s\n' "$SCRIPT"; exit 0 ;; esac
"$SHELL_BIN" -n <(printf '%s\n' "$SCRIPT") || { echo "run.sh: extracted script is not valid $RUN_SHELL" >&2; exit 2; }

mkdir -p "$HERE/logs"
MODE=normal; case " $* " in *" --in-order "*) MODE=in-order ;; esac
LOG="$HERE/logs/run-$(date -u +%Y%m%dT%H%M%SZ)-$RUN_SHELL-$MODE.log"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/guide-run.XXXXXX")
echo "context: $(kubectl config current-context 2>/dev/null || echo none)   shell: $RUN_SHELL   cwd: $WORK   log: $LOG"
# Run from a file with stdin closed: a block that reads stdin (kubectl run -i, for one) would
# otherwise consume the rest of the script and end the run early with status 0.
SCRIPT_FILE="$HERE/logs/$(basename "$LOG" .log).sh"
printf '%s\n' "$SCRIPT" > "$SCRIPT_FILE"
# zsh -f: no startup files, so the run sees only what the guide sets, as bash does.
case "$RUN_SHELL" in zsh) FLAGS=(-f) ;; *) FLAGS=() ;; esac
(cd "$WORK" && "$SHELL_BIN" ${FLAGS[@]+"${FLAGS[@]}"} "$SCRIPT_FILE") < /dev/null > "$LOG" 2>&1
rc=$?
leftover=$(ls -A "$WORK" | head -5 | tr '\n' ' ')
rm -rf "$WORK"
[ -n "$leftover" ] && echo "run.sh: blocks left files in the working directory: $leftover"
last=$(grep '^>>>' "$LOG" | tail -1)
if [ $rc -eq 0 ] && [ "$last" != ">>>DONE" ]; then
  echo "run.sh: the script ended without reaching its last block" >&2; rc=1
fi
echo "exit $rc   last marker: $last"
exit $rc
