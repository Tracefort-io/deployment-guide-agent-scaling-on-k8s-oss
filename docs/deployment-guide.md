# Deployment Guide for Operating, Isolating, and Scaling Agent Workloads on Kubernetes (Open Source)

**Updated:** November 2nd, 2026

**Certified against:** not yet certified. Target: kagent 1.0.0-rc1, Agent Substrate v1.0.0-rc1, agentgateway v1.7.0

## Introduction

Running one agent is a deployment. Running a platform of them is a different problem: every team
needs somewhere to run its agents, a way to keep each agent's credentials away from the agent
itself, and one controlled path out to the models and tools those agents call.

This guide runs agents on Agent Substrate under kagent, and sends every model call they make
through agentgateway. Every component is open source. It is a quick start: `kagent install` builds
the platform in one step, and the rest of the guide shows each part of it working.

**Up and Running** installs the platform, puts the gateway in front of the model and proves a first
turn went through it.

**Operate** follows one session from first message to suspension.

**Isolate** shows what keeps one session, one agent and one team apart.

**Scale** compares a pod per session with Substrate and sizes the worker pool from the numbers.

**Validate** sends one session through every control and maps each result to the step that
set it.

**The order matters.** Isolate closes things Up and Running leaves open, in particular sign-in, so
do not expose this cluster to anyone until Isolate is done.

## Version support

kagent and Agent Substrate are pre-release. This guide is pinned to one tested set:

| Component | Version |
|---|---|
| kagent (CLI, CRD and controller charts) | 1.0.0-rc1 |
| Agent Substrate (charts and worker image, installed by `kagent install`) | v1.0.0-rc1 |
| kubectl-ate | v1.0.0-rc1 |
| kagent runtime images (`golang-adk`, `claude-harness`) | 1.0.0-rc1, pinned by digest |
| agentgateway | v1.7.0 |
| Kubernetes | 1.35.0 on kind 0.32.0 |

Do not mix versions across this set. kagent 1.0.0-rc1 is built against Agent Substrate v1.0.0-rc1,
and the two are upgraded together.

**Fresh install only.** This guide installs this set on a new cluster. It does not cover upgrading
an earlier release in place.

## Environment setup

Run this first, in every new shell you paste this guide's blocks into:

```bash run id=shell-setup
if [ -n "${ZSH_VERSION:-}" ]; then setopt interactive_comments nobanghist; fi
if [ -n "${BASH_VERSION:-}" ]; then set +H; fi
```

- `interactive_comments`: an interactive zsh does not treat `#` as a comment unless this is set, so
  every comment line in a pasted block fails, and one containing `<` or `>` is a parse error.
- `nobanghist` (zsh) and `set +H` (bash): an interactive shell expands `!` inside double quotes as a
  history reference, which rewrites or rejects any line that contains one.

Then set these. Every command below uses them.

```bash run id=environment-setup-env
# Pinned versions
export KAGENT_VERSION=1.0.0-rc1
export SUBSTRATE_VERSION=v1.0.0-rc1
export KUBECTL_ATE_VERSION=v1.0.0-rc1
export AGENTGATEWAY_VERSION=v1.7.0
export GATEWAY_API_VERSION=v1.6.1

# The cluster. The node image is pinned by digest.
export KIND_CLUSTER_NAME=${KIND_CLUSTER_NAME:-agents}
export KIND_NODE_IMAGE=kindest/node:v1.35.0@sha256:4613778f3cfcd10e615029370f5786704559103cf27bef934597ba562b269661

# The agent runtime images. Substrate refuses an image that is not pinned: a changed image
# invalidates snapshots.
export RUNTIME_IMAGE=ghcr.io/kagent-dev/kagent/golang-adk:1.0.0-rc1@sha256:5b06741be8cb69c9293477bdbf3203c6526127c533fa940fd9f595a82a801701
export CLAUDE_HARNESS_IMAGE=ghcr.io/kagent-dev/kagent/claude-harness:1.0.0-rc1@sha256:359f8d8107df0ac5b0ebb306b0bc2e8d0ae0c3eae4540fe909724f1d8379dad5

# Model provider. Anthropic is the reference provider.
export ANTHROPIC_API_KEY='<your-anthropic-api-key>'
export MODEL=claude-haiku-4-5

# Where the kagent CLI and kubectl-ate are installed in Step 2. Must be on your PATH.
export ATE_BIN_DIR=${ATE_BIN_DIR:-$HOME/.local/bin}

# Where the sign-in step saves the passwords of the UI users it generates.
export KAGENT_UI_CREDENTIALS_FILE=${KAGENT_UI_CREDENTIALS_FILE:-$HOME/.kagent-ui-credentials}

# Local ports for the port-forwards.
export KAGENT_API_PORT=${KAGENT_API_PORT:-18083}
export KAGENT_UI_PORT=${KAGENT_UI_PORT:-14000}
```

### What you need before you start

| **Requirement** | **Needed for** | **Notes** |
|---|---|---|
| Docker on a machine with 6 CPU and 12 GiB for it | Everything | The reference environment is Colima on macOS (`colima start --cpu 6 --memory 12`). |
| `kind` 0.32.0, `kubectl`, `helm`, `jq`, `curl`, `openssl` | Everything | |
| `zstd` | Isolate | Decompresses a snapshot to search it |
| Anthropic API key | The gateway step | It is stored once, in the gateway. Nothing else holds it. |
| Egress from the cluster to `ghcr.io`, `docker.io` and `api.anthropic.com` | Everything | Images, and the model calls |
| Access from your machine to `github.com` | Step 2 | The kagent CLI and kubectl-ate from GitHub releases, each checked against a sha256 |

> [!WARNING]
> Use `curl -sS` rather than `curl -s` **throughout this guide.** Checks that go through a port-forward print nothing and exit 7 when the forward has died, and an empty result then looks like a pass. `-S` keeps curl quiet on success and makes it say so on failure.

### Port-forwards

**Every port-forward binds 127.0.0.1 only.** `kubectl port-forward` binds localhost by default; the
`--address 127.0.0.1` below makes it explicit so it is not changed by accident.

**Port-forwards die.** A port-forward is attached to one pod, and a rollout replaces the pod. Define
this now and re-run it whenever something refuses to connect:

```bash run id=ports-forwards-helper
forwards() {
  pkill -f "kubectl --context kind-${KIND_CLUSTER_NAME} port-forward .*-n kagent " 2>/dev/null || true
  kubectl --context "kind-${KIND_CLUSTER_NAME}" rollout status deployment/kagent-controller -n kagent --timeout=120s
  kubectl --context "kind-${KIND_CLUSTER_NAME}" rollout status deployment/kagent-ui -n kagent --timeout=120s
  kubectl --context "kind-${KIND_CLUSTER_NAME}" port-forward --address 127.0.0.1 -n kagent deployment/kagent-controller ${KAGENT_API_PORT}:8083 >/dev/null 2>&1 &
  local api=$!
  kubectl --context "kind-${KIND_CLUSTER_NAME}" port-forward --address 127.0.0.1 -n kagent service/kagent-ui ${KAGENT_UI_PORT}:8080 >/dev/null 2>&1 &
  local ui=$!
  sleep 2
  if ! kill -0 $api 2>/dev/null || ! kill -0 $ui 2>/dev/null; then
    echo "forwards: port ${KAGENT_API_PORT} or ${KAGENT_UI_PORT} is held by something else, often a forward to another cluster; set KAGENT_API_PORT and KAGENT_UI_PORT to free ports" >&2
    return 1
  fi
  echo "forwards up: controller ${KAGENT_API_PORT}, ui ${KAGENT_UI_PORT}"
}

# stop_forwards: close every port-forward this guide opened to this cluster, and say which ports it freed.
stop_forwards() {
  local pat="kubectl --context kind-${KIND_CLUSTER_NAME} port-forward " ports
  ports=$(pgrep -f "$pat" | while read -r p; do ps -o command= -p "$p"; done \
    | grep -oE ' [0-9]+:[0-9]+' | cut -d: -f1 | tr -d ' ' | sort -n | tr '\n' ' ')
  if pkill -f "$pat"; then
    echo "closed the port-forwards to kind-${KIND_CLUSTER_NAME}; ports freed: ${ports}"
  else
    echo "no port-forwards open to kind-${KIND_CLUSTER_NAME}"
  fi
}
```

Every port-forward in this guide names this cluster's context (`kind-${KIND_CLUSTER_NAME}`), and
`forwards` closes only the ones to its `kagent` namespace, so a forward you have open to another
cluster is never touched.

## UP AND RUNNING

**What you are going to do:** create a cluster with the certificate APIs Substrate uses, install
Substrate, PostgreSQL and kagent with `kagent install`, put agentgateway in front of the model,
fence it so only agent traffic reaches it, declare one agent, and prove its first answer went
through the gateway.

**What you will have that you did not have before:** an agent that holds no model key, whose
sessions each run in their own sandbox, and whose every model call goes through one gateway.

### Step 1: Create the cluster

*Not yet written.* A kind cluster with the `ClusterTrustBundle`, `ClusterTrustBundleProjection`
and `PodCertificateRequest` feature gates and `certificates.k8s.io/v1beta1` on, as kagent's own CI
runs it.

### Step 2: Install kagent, Substrate and PostgreSQL with `kagent install`

*Not yet written.* The kagent CLI and kubectl-ate from their GitHub releases, checked against a
sha256, then one `kagent install`.

### Step 3: See what `kagent install` built

*Not yet written.* The releases, namespaces, certificate authorities, database logins and worker
pool the CLI created, and which of them a production install would build differently.

### Step 4: Put agentgateway in front of the model

*Not yet written.*

### Step 5: Fence the gateway

*Not yet written.*

### Step 6: Declare the first agent

*Not yet written.*

### Step 7: Verify the first turn through the gateway

*Not yet written.*

## OPERATE

### Step 8: How an agent becomes actors

*Not yet written.*

### Step 9: Follow one session

*Not yet written.*

### Step 10: Trace a turn

*Not yet written.*

### Step 11: Inspect and act on an actor

*Not yet written.*

### Step 12: Where a session's context is kept

*Not yet written.*

### Step 13: Delete a session, and what stays behind

*Not yet written.*

### Step 14: Choose a Harness

*Not yet written.*

## ISOLATE

### Step 15: One gVisor sandbox per session

*Not yet written.*

### Step 16: What is left of a suspended session

*Not yet written.*

### Step 17: The agent never holds a key

*Not yet written.*

### Step 18: Test the egress allowlist from inside

*Not yet written.*

### Step 19: Give an agent an MCP tool through the gateway

*Not yet written.*

### Step 20: Cap each team's tokens

*Not yet written.*

### Step 21: Replace the snapshot store's credentials and fence it

*Not yet written.*

### Step 22: Put the UI and API behind single sign-on

*Not yet written.*

### Step 23: Sessions belong to the user who started them

*Not yet written.*

## SCALE

### Step 24: A pod per session, a pod per agent, or Substrate

*Not yet written.*

### Step 25: Memory follows turns in flight

*Not yet written.*

### Step 26: What suspended sessions leave behind

*Not yet written.*

### Step 27: Size the worker pool for concurrent turns

*Not yet written.*

### Step 28: Change the worker pool without losing capacity

*Not yet written.*

## VALIDATE

### Step 29: Drive one session through every control

*Not yet written.*

### Step 30: Map each result to the step that set it

*Not yet written.*

### What you built

*Not yet written.*

## Before production

*Not yet written.*
