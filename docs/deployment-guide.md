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

# The trace store, Step 10. In memory: traces are lost when its pod restarts.
export JAEGER_IMAGE=jaegertracing/jaeger:2.12.0@sha256:4c8423cd8fa8b727bd632f0acda2cc39118f21a4bdcef322a6c9ddbeb6dcd424

# The reference MCP server's runtime, Step 19.
export MCP_SERVER_IMAGE=python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4

# The identity provider, Step 22. The port is part of the issuer URL, so it is the same on your
# machine and inside the cluster.
export KEYCLOAK_IMAGE=quay.io/keycloak/keycloak:26.4.2@sha256:3617b09bb4b7510a8d8d9b9fc5707399e2d70688dbcc2f8fb013a144829be1b9
export KEYCLOAK_PORT=${KEYCLOAK_PORT:-18080}

# The S3 client Step 12 uses to read the snapshot store.
export AWS_CLI_IMAGE=amazon/aws-cli:2.17.0@sha256:643507c10ada7964ca6157b3d799f030b90577643da9955d319a77399ed80d73

# Model provider. Anthropic is the reference provider.
export ANTHROPIC_API_KEY='<your-anthropic-api-key>'
export MODEL=claude-haiku-4-5

# Where the kagent CLI and kubectl-ate are installed in Step 3. Must be on your PATH.
export ATE_BIN_DIR=${ATE_BIN_DIR:-$HOME/.local/bin}

# Where the sign-in step saves the passwords of the UI users it generates.
export KAGENT_UI_CREDENTIALS_FILE=${KAGENT_UI_CREDENTIALS_FILE:-$HOME/.kagent-ui-credentials}

# Local ports for the port-forwards.
export KAGENT_API_PORT=${KAGENT_API_PORT:-18083}
export KAGENT_UI_PORT=${KAGENT_UI_PORT:-14000}
export JAEGER_PORT=${JAEGER_PORT:-16686}
```

### What you need before you start

| **Requirement** | **Needed for** | **Notes** |
|---|---|---|
| Docker on a machine with 6 CPU and 12 GiB for it | Everything | The reference environment is Colima on macOS (`colima start --cpu 6 --memory 12`). |
| `kind` 0.32.0, `kubectl`, `helm`, `jq`, `curl`, `openssl` | Everything | |
| `zstd` | Isolate | Decompresses a snapshot to search it |
| Anthropic API key | Step 2 | It is stored once, in the gateway. Nothing else holds it. |
| Egress from the cluster to `ghcr.io`, `cr.agentgateway.dev`, `docker.io` and `api.anthropic.com` | Everything | Images, and the model calls |
| Access from your machine to `github.com` and `cr.agentgateway.dev` | Steps 2 and 3 | The Gateway API CRDs, the kagent CLI and kubectl-ate from GitHub releases (the CLIs checked against a sha256), and agentgateway's charts |

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

**What you are going to do:** create a cluster with the certificate APIs Substrate uses, put
agentgateway in front of the model with the only copy of the provider key, install Substrate,
PostgreSQL and kagent with one `kagent install` pointed at that gateway, fence the gateway so only
agent traffic reaches it, declare one agent, and prove its first answer went through the gateway.

**What you will have that you did not have before:** an agent that holds no model key, whose
sessions each run in their own sandbox, and whose every model call goes through one gateway.

**Verified on:** a kind cluster created fresh for it (kind 0.32.0, `kindest/node:v1.35.0`), 2026-11-02 22:28 UTC: every block from the top of this guide to the end of Step 7 executed verbatim in one pass of `auto-build/run.sh` under bash, from an empty directory outside the repository, on the versions in Version support. Every output block in Up and Running comes from that run.

### Step 1: Create the cluster

Substrate gives every control plane and worker pod a certificate through two Kubernetes APIs,
PodCertificateRequest and ClusterTrustBundle, both in `certificates.k8s.io/v1beta1` and behind
feature gates on 1.35. This is the configuration kagent's own CI tests on. A cluster where you
cannot turn them on is covered in Attachment B.

```bash run id=kind-cluster
kind create cluster --name "${KIND_CLUSTER_NAME}" --config - <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
featureGates:
  ClusterTrustBundle: true
  ClusterTrustBundleProjection: true
  PodCertificateRequest: true
runtimeConfig:
  "api/alpha": "false"
  "certificates.k8s.io/v1beta1": "true"
nodes:
  - role: control-plane
    image: ${KIND_NODE_IMAGE}
EOF
```

Confirm the API is served and the gates are on:

```bash run id=step-1-certificates-api
kubectl api-resources --api-group=certificates.k8s.io
kubectl get --raw /metrics | grep -E 'kubernetes_feature_enabled\{name="(ClusterTrustBundle|ClusterTrustBundleProjection|PodCertificateRequest)"'
```

**Verified on 2026-11-02:**

```text
NAME                         SHORTNAMES   APIVERSION                    NAMESPACED   KIND
certificatesigningrequests   csr          certificates.k8s.io/v1        false        CertificateSigningRequest
clustertrustbundles                       certificates.k8s.io/v1beta1   false        ClusterTrustBundle
podcertificaterequests                    certificates.k8s.io/v1beta1   true         PodCertificateRequest
kubernetes_feature_enabled{name="ClusterTrustBundle",stage="BETA"} 1
kubernetes_feature_enabled{name="ClusterTrustBundleProjection",stage="BETA"} 1
kubernetes_feature_enabled{name="PodCertificateRequest",stage="BETA"} 1
```

`clustertrustbundles` and `podcertificaterequests` at `v1beta1`, and all three gates at 1. A gate
at 0 or a missing resource here means `kagent install` never finishes in Step 3: PostgreSQL and
every Substrate pod wait for a certificate that nothing can issue.

### Step 2: Put agentgateway in front of the model

The gateway comes first, so that kagent is installed already pointing at it and never holds the
provider key. Every agent's model calls will go to this gateway rather than to the provider. It
holds the real provider key, authenticates each caller by its own consumer key, and is the one
place token caps are applied in Isolate.

Install the Gateway API CRDs, then agentgateway's CRDs and control plane:

```bash run id=gateway-api-crds
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml
```

```bash run id=helm-agentgateway
helm upgrade -i agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds \
  --create-namespace --namespace agentgateway-system --version ${AGENTGATEWAY_VERSION}
helm upgrade -i agentgateway oci://cr.agentgateway.dev/charts/agentgateway \
  --namespace agentgateway-system --version ${AGENTGATEWAY_VERSION} --wait
```

Create the Gateway:

```bash run id=gateway-agentgateway-proxy
kubectl apply -f- <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: agentgateway-system
spec:
  gatewayClassName: agentgateway
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      allowedRoutes:
        namespaces:
          from: All
EOF
```

```bash run id=step-2-gateway-programmed
kubectl wait --for=condition=Programmed --timeout=180s gateway/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system --timeout=180s
```

**Verified on 2026-11-02:**

```text
gateway.gateway.networking.k8s.io/agentgateway-proxy condition met
deployment "agentgateway-proxy" successfully rolled out
```

Store the provider key in the gateway's namespace. This is the only copy of it in the cluster:

```bash run id=secret-anthropic-api-key
kubectl delete secret anthropic-api-key -n agentgateway-system --ignore-not-found
printf '%s' "$ANTHROPIC_API_KEY" | kubectl create secret generic anthropic-api-key \
  -n agentgateway-system --from-file=Authorization=/dev/stdin
```

Connect the provider and expose it on a route. kagent's runtime calls Anthropic's native Messages
API at `/v1/messages`, so the backend maps that path to the Messages route type and the route
matches it:

```bash run id=backend-anthropic-messages
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: anthropic-messages-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      anthropic:
        model: "${MODEL}"
  policies:
    auth:
      secretRef:
        name: anthropic-api-key
    ai:
      routes:
        "/v1/messages": Messages
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: anthropic-messages
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /v1/messages
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayBackend
          name: anthropic-messages-backend
          namespace: agentgateway-system
EOF
```

Issue the agents a consumer key. It identifies the caller to the gateway, not to Anthropic, and its
`group` is what token caps hang on in Isolate. The key is generated in your shell and stored in a
Secret created with `kubectl create`, not `apply`: `apply` would copy the whole Secret, key
included, into a `last-applied-configuration` annotation that anyone allowed to read the object's
metadata can see.

```bash run id=secret-llm-virtual-keys
: "${AGENT_CONSUMER_KEY:=agw_team_agents_$(openssl rand -hex 32)}"; export AGENT_CONSUMER_KEY
kubectl delete secret llm-virtual-keys -n agentgateway-system --ignore-not-found
kubectl create -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: llm-virtual-keys
  namespace: agentgateway-system
type: Opaque
stringData:
  team-agents: |
    {
      "key": "${AGENT_CONSUMER_KEY}",
      "metadata": {
        "id": "team-agents-key",
        "user": "assistant-agent",
        "group": "team-agents"
      }
    }
EOF
```

Require that key on the route, on its own header:

```bash run id=policy-anthropic-messages-apikey-auth
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: anthropic-messages-apikey-auth
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: anthropic-messages
  traffic:
    apiKeyAuthentication:
      mode: Strict
      location:
        header:
          name: x-api-key
      secretRef:
        name: llm-virtual-keys
EOF
kubectl wait agentgatewaypolicy/anthropic-messages-apikey-auth -n agentgateway-system \
  --for=jsonpath='{.status.ancestors[0].conditions[?(@.type=="Attached")].status}'=True --timeout=60s
```

`x-api-key` is the header Anthropic's SDKs send their key in, so the agent runtime sends the
consumer key there without being told anything about the gateway.

Prove the route refuses a caller with no key and serves one with the consumer key, before anything
else is installed. The port-forward goes straight to the proxy pod and is closed again at the end:

```bash run id=step-2-gateway-check
kubectl --context "kind-${KIND_CLUSTER_NAME}" port-forward --address 127.0.0.1 -n agentgateway-system deployment/agentgateway-proxy 18081:80 >/dev/null 2>&1 &
GW_PF=$!
sleep 3
MSG="{\"model\":\"${MODEL}\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: gateway ok\"}]}"
echo -n "no key:       "; curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18081/v1/messages \
  -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' -d "$MSG"
echo -n "consumer key: "; curl -sS http://127.0.0.1:18081/v1/messages \
  -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -H "x-api-key: ${AGENT_CONSUMER_KEY}" -d "$MSG" | jq -r '.content[0].text // .error.message'
kill $GW_PF; wait $GW_PF 2>/dev/null || true
```

**Verified on 2026-11-02:**

```text
no key:       401
consumer key: gateway ok
```

A 401 with no key and an answer with the consumer key means the gateway is authenticating callers
and holding the provider key itself. `invalid x-api-key` on the second line instead of an answer
comes from Anthropic, not the gateway: the gateway accepted the consumer key, and the key in
`anthropic-api-key` is wrong. Fix `ANTHROPIC_API_KEY` and run `secret-anthropic-api-key` again.

### Step 3: Install kagent, Substrate and PostgreSQL with `kagent install`

Download the kagent CLI and kubectl-ate, Substrate's CLI, and check each against its release's
sha256:

```bash run id=install-clis
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  PLATFORM=darwin-arm64
    KAGENT_SHA=b51b1ea64aece6b8b0a5122cdb5783a7392e8fc273a3e8891c39f2d1764ce97d
    ATE_SHA=4e902b0390a9b60b13a35200900e22e831aaf39f2a3e3f3352775e2c445b686c ;;
  Darwin-x86_64) PLATFORM=darwin-amd64
    KAGENT_SHA=5c578b5a83b811e67f1869fcd778d0bb0bace87cca462475da53a0ad7b6f5980
    ATE_SHA=423011ab8509000c48628e9c5b4618406c4d1165a09270951a568ce2caa590a0 ;;
  Linux-aarch64|Linux-arm64) PLATFORM=linux-arm64
    KAGENT_SHA=38025b797a7d29a2b3e34eea493c68dec9831659b66e24e6113b25a8ca0a4928
    ATE_SHA=3cdf70971829002bca8e92edd7c83d090b6c476ea7eb085a2dfe8f5a6bffc1ea ;;
  Linux-x86_64)  PLATFORM=linux-amd64
    KAGENT_SHA=cf7b98936e6aca37c0c85c8103603e1a86ee24634a3d78dd73c902df73ea335e
    ATE_SHA=d254c761d960f13cc3200b7e75a3a5eae9dc01fdfd73eac6a168a69b2d67e180 ;;
  *) echo "no pinned build for $(uname -s)-$(uname -m)" >&2; false ;;
esac && {
mkdir -p "$ATE_BIN_DIR"
DL=$(mktemp -d)
curl -sSfL -o "$DL/kagent" "https://github.com/kagent-dev/kagent/releases/download/v${KAGENT_VERSION}/kagent-${PLATFORM}"
curl -sSfL -o "$DL/kubectl-ate" "https://github.com/kagent-dev/substrate/releases/download/${KUBECTL_ATE_VERSION}/kubectl-ate-${PLATFORM}"
if (cd "$DL" && echo "${KAGENT_SHA}  kagent" | shasum -a 256 -c - && echo "${ATE_SHA}  kubectl-ate" | shasum -a 256 -c -); then
  install -m 0755 "$DL/kagent" "$DL/kubectl-ate" "$ATE_BIN_DIR/"
  rm -rf "$DL"
  kagent version
  kubectl-ate --version
else
  rm -rf "$DL"; echo "sha256 mismatch: nothing installed" >&2; false
fi
}
```

**Verified on 2026-11-02:**

```text
kagent: OK
kubectl-ate: OK
{"backend_version":"unknown","build_date":"2026-10-30","git_commit":"eed2723","kagent_version":"1.0.0-rc1"}
kubectl-ate version v1.0.0-rc1 commit=71b1f9ec80848f8fd46f5d5d7e6b84ba36a80966 built=2026-10-30T11:40:37-04:00 darwin/arm64
```

Now install. `kagent install` creates the Substrate and kagent releases, a PostgreSQL that every
component logs in to with a certificate rather than a password, and the certificate authorities
behind those certificates. It installs into the cluster your current kubectl context points at,
which `kind create cluster` set to this one.

It also creates kagent's default model: a ModelConfig with an API key. Two settings make that model
go through the gateway instead of straight to Anthropic. The key the CLI is given is the
gateway's consumer key, not the Anthropic key, and a values file sets the model's `baseUrl` to the
gateway. `--profile minimal` leaves out the example agents the chart otherwise installs.

```bash run id=install-kagent
: "${AGENT_CONSUMER_KEY:?is empty: Step 2 exported it in another shell. Read it back first (below).}" && {
KAGENT_CLI_DIR=$(mktemp -d)
cat > "$KAGENT_CLI_DIR/config.yaml" <<EOF
install:
  defaultModelProvider: Anthropic
  kagent:
    valuesFiles:
      - kagent-values.yaml
EOF
cat > "$KAGENT_CLI_DIR/kagent-values.yaml" <<EOF
providers:
  anthropic:
    model: ${MODEL}
    config:
      baseUrl: http://agentgateway-proxy.agentgateway-system.svc.cluster.local
EOF
ANTHROPIC_API_KEY="$AGENT_CONSUMER_KEY" kagent --config "$KAGENT_CLI_DIR/config.yaml" install --profile minimal
rm -rf "$KAGENT_CLI_DIR"
}
```

**Verified on 2026-11-02:**

```text
kagent installed successfully
```

**Only if the guard above stopped you.** This reads the key back from the gateway only when your
shell has none, so it changes nothing otherwise:

```bash run id=step-3-read-back-consumer-key when=manual
if [ -z "${AGENT_CONSUMER_KEY:-}" ]; then
  export AGENT_CONSUMER_KEY=$(kubectl get secret llm-virtual-keys -n agentgateway-system \
    -o jsonpath='{.data.team-agents}' | base64 --decode | jq -r .key)
  echo "AGENT_CONSUMER_KEY read back; run install-kagent again"
fi
```

`kagent install` takes about two minutes. The `ANTHROPIC_API_KEY=` in front of the command applies
to that command only, so a real Anthropic key in your shell is not replaced, and is not used.

### Step 4: See what `kagent install` built

One command added five Helm releases to the two agentgateway releases from Step 2:

```bash run id=step-4-releases
helm list -A -o json | jq -r '.[] | "\(.name)\t\(.namespace)\t\(.chart)"' | sort
```

**Verified on 2026-11-02:**

```text
agentgateway	agentgateway-system	agentgateway-v1.7.0
agentgateway-crds	agentgateway-system	agentgateway-crds-v1.7.0
kagent	kagent	kagent-1.0.0-rc1
kagent-crds	kagent	kagent-crds-1.0.0-rc1
substrate	ate-system	substrate-1.0.0-rc1
substrate-crds	ate-system	substrate-crds-1.0.0-rc1
substrate-podcert	podcertificate-controller-system	substrate-podcert-1.0.0-rc1
```

- `substrate-crds` and `substrate` are Agent Substrate: the API, the controller, `atelet` on every
  node (it runs the sandboxes), the router and egress gateway every actor's traffic passes through,
  and RustFS, the store snapshots are saved to.
- `substrate-podcert` is the controller that issues the Pod Certificates everything logs in with.
- `kagent-crds` and `kagent` are kagent, with a worker pool of gVisor sandboxes on Substrate.

```bash run id=step-4-pods
kubectl get pods -n ate-system
kubectl get pods -n kagent
kubectl get workerpools.ate.dev -n kagent
```

**Verified on 2026-11-02:**

```text
NAME                                      READY   STATUS      RESTARTS   AGE
ate-api-server-74f8b87bf9-6lq4w           2/2     Running     0          2m14s
ate-api-server-74f8b87bf9-vsjch           2/2     Running     0          2m14s
ate-controller-c994f4b54-7hldf            1/1     Running     0          2m14s
ate-idp-server-85748d8dd6-56mbz           1/1     Running     0          2m14s
ate-idp-server-85748d8dd6-tkft7           1/1     Running     0          2m14s
atelet-c29qh                              2/2     Running     0          2m14s
atenet-egress-654675ddfb-b49ps            1/1     Running     0          2m14s
atenet-router-57d98fd5bf-7jhkw            1/1     Running     0          2m14s
k8s-credential-provider-fdbb6987b-sl4ds   1/1     Running     0          2m14s
rustfs-5d695f5fd-hsmb4                    1/1     Running     0          2m14s
rustfs-bucket-init-dn49v                  0/1     Completed   0          2m14s
NAME                                 READY   STATUS    RESTARTS   AGE
kagent-controller-875d9c8-ndg5t      1/1     Running   0          71s
kagent-default-5ffdf9d9f7-pwpbq      1/1     Running   0          71s
kagent-postgresql-8466db8889-v665k   2/2     Running   0          2m38s
kagent-tools-787df48f55-c597m        1/1     Running   0          71s
kagent-ui-6f5995c9b9-qt6ll           1/1     Running   0          71s
NAME             DESIRED   REPLICAS   READY   SANDBOXCONFIG    RESOLVED   AGE
kagent-default   1         1          1       gvisor-default   True       71s
```

`kagent-default` is the worker pool. Each worker is a pod that runs many sessions' sandboxes,
which Operate looks at closely. `kagent-postgresql` is the database kagent and Substrate share; it
has two containers because a sidecar keeps its server certificate current.

kagent's default model now goes to the gateway, with the consumer key:

```bash run id=step-4-default-model
kubectl get modelconfig default-model-config -n kagent \
  -o jsonpath='{.spec.provider} {.spec.model} {.spec.anthropic.baseUrl} {.spec.apiKeySecret}{"\n"}'
held=$(kubectl get secret kagent-anthropic -n kagent -o jsonpath='{.data.ANTHROPIC_API_KEY}' | base64 --decode)
[ "$held" = "$AGENT_CONSUMER_KEY" ] && echo "kagent-anthropic holds the gateway consumer key" \
  || echo "kagent-anthropic does NOT hold the consumer key"
unset held
```

**Verified on 2026-11-02:**

```text
Anthropic claude-haiku-4-5 http://agentgateway-proxy.agentgateway-system.svc.cluster.local kagent-anthropic
kagent-anthropic holds the gateway consumer key
```

The Secret is named for the provider, but what it holds is the gateway's consumer key. The
Anthropic key is still only in `agentgateway-system`.

> [!NOTE]
> **What `kagent install` chose for you.** It is built for getting started. Its PostgreSQL is a single pod with no pgvector extension, which kagent's documentation says is not intended for production. It installs into the namespaces `kagent` and `ate-system` and sizes the worker pool at one replica. `kagent install --dry-run --output-dir <dir>` writes the same install out as Helm commands and values files, which is the place to start when you need to change any of it.

### Step 5: Fence the gateway

The gateway accepts any caller that presents a valid consumer key, from anywhere in the cluster. The
only callers it should have are agents, and every call an agent makes leaves through Substrate's
egress gateway, `atenet-egress`. The one other caller is the kagent controller, which discovers an
MCP server's tools by calling it itself, so an MCP server behind this gateway (Step 19) needs it
admitted.

```bash run id=networkpolicy-agentgateway-proxy
kubectl apply -f- <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agentgateway-proxy-from-substrate-egress
  namespace: agentgateway-system
spec:
  podSelector:
    matchLabels:
      gateway.networking.k8s.io/gateway-name: agentgateway-proxy
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ate-system
          podSelector:
            matchLabels:
              app: atenet-egress
      ports:
        - protocol: TCP
          port: 80
        - protocol: TCP
          port: 443
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kagent
          podSelector:
            matchLabels:
              app.kubernetes.io/component: controller
              app.kubernetes.io/name: kagent
      ports:
        - protocol: TCP
          port: 80
        - protocol: TCP
          port: 443
EOF
```

Only the listener ports are admitted; the proxy's admin and metrics ports are reachable from no pod.

Prove another pod cannot reach it. Read the error text rather than the exit status: `wget` exits 1
both when the connection is dropped and when the gateway answers 401, and only the first proves the
policy:

```bash run id=step-5-fence-check
kubectl run fence-check -n default --rm -i --restart=Never --image=busybox:1.36 --quiet -- \
  sh -c 'wget -T 8 -O /dev/null http://agentgateway-proxy.agentgateway-system.svc.cluster.local/v1/messages 2>&1 | tail -1' \
  < /dev/null
```

**Verified on 2026-11-02:**

```text
wget: download timed out
```

`< /dev/null` keeps `kubectl run -i` from reading your terminal: without it, anything pasted after
this block is fed to the pod instead of your shell, and silently never runs.

`download timed out` is the policy dropping the connection. `401 Unauthorized` would mean the pod
reached the gateway and only the key check stopped it.

> [!NOTE]
> **The policy admits by label, not identity.** Any pod someone can create in `kagent` with the controller's two labels passes it, and still needs a valid consumer key. Restrict who can create pods in `kagent`.

### Step 6: Declare the first agent

An agent is three resources here. An `AgentTemplate` says what the agent is: its model, its
instructions and its tools. A `Harness` says what runs it: the runtime image and the worker pool.
The `Agent` pairs a template with a harness. The model is the default one Step 3 pointed at the
gateway.

kagent never gives the agent the consumer key: it gives the agent the placeholder
`kagent-credential-injected`, and tells Substrate's egress gateway to replace the `x-api-key`
header with the real value on the way to the address in `baseUrl`.

```bash run id=agent-assistant
kubectl apply -f- <<EOF
apiVersion: api.kagent.dev/v1alpha3
kind: AgentTemplate
metadata:
  name: assistant
  namespace: kagent
spec:
  description: A general assistant with no tools.
  modelConfig:
    name: default-model-config
  systemPrompt: |
    You are a helpful assistant. Answer concisely.
---
apiVersion: api.kagent.dev/v1alpha3
kind: Harness
metadata:
  name: kagent-adk
  namespace: kagent
spec:
  kagent: {}
  workload:
    image: ${RUNTIME_IMAGE}
  substrate:
    workerPoolRef:
      name: kagent-default
    snapshotPolicy:
      location: gs://ate-snapshots/kagent/
---
apiVersion: api.kagent.dev/v1alpha3
kind: Agent
metadata:
  name: assistant
  namespace: kagent
spec:
  template:
    ref:
      name: assistant
  harnessRef:
    name: kagent-adk
EOF
```

`snapshotPolicy.location` is required. Only its bucket and prefix are used: with Substrate's
bundled store, snapshots go to bucket `ate-snapshots` under `kagent/` in RustFS whatever the scheme
says.

`baseUrl` is `http://`. The hop from Substrate's egress gateway to agentgateway cannot be HTTPS to
a private certificate: Substrate's egress gateway trusts only public CAs.

Wait for the agent:

```bash run id=step-6-agent-ready
kubectl wait agent/assistant -n kagent --for=condition=Ready --timeout=300s
kubectl get agent assistant -n kagent -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'
```

**Verified on 2026-11-02:**

```text
agent.api.kagent.dev/assistant condition met
Accepted=True Agent explicitly selects its template and harness
EgressPoliciesApplied=True All requested egress grants are admitted
ResolvedRefs=True All runtime references resolved
Compatible=True Resolved configuration is compatible with the Harness
Ready=True ActorTemplate golden snapshot is ready
```

`Ready` means Substrate started the agent once, let it initialize, and saved that state as a
golden snapshot. Every session from now on starts from a copy of it instead of booting.

### Step 7: Verify the first turn through the gateway

Bring the forwards up:

```bash run id=step-7-forwards
forwards
```

`kagent_send` sends one message to an agent over A2A and prints the reply and the session's ID:

```bash run id=step-7-helpers
# kagent_send <agent> <message> [contextId]
kagent_send() {
  local ctx=""
  [ -n "${3:-}" ] && ctx="\"contextId\":\"$3\","
  curl -sS --max-time 300 -X POST "http://127.0.0.1:${KAGENT_API_PORT}/agents/kagent/$1" \
    -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"SendMessage\",\"params\":{\"message\":{\"messageId\":\"$(openssl rand -hex 8)\",${ctx}\"role\":\"ROLE_USER\",\"parts\":[{\"text\":\"$2\"}]}}}" \
    | jq -r 'if .result.task then (.result.task | "\(.status.state)\t\(.contextId)\t\(((.artifacts[-1].parts // .status.message.parts // []) | map(.text // "") | join("")) | gsub("\\s*\n\\s*"; " "))")
             else "NO_TASK\t\t\(.error.message // ("no task in the response: " + (tostring | .[0:200])))" end'
}
```

It prints the reply, or for a failed turn the error the agent got, or `NO_TASK` and the JSON-RPC
error when the controller returned no task at all, so a failure is never a blank line, and always on
one line, so `cut -f2` gets the session ID. `--max-time 300` because a turn includes a model call.

> [!WARNING]
> **The API has no sign-in yet.** `kagent install` leaves the controller in `insecure` mode: every request is treated as user `admin@kagent.dev`, or as whatever user the caller names in an `X-User-Id` header. That is why `kagent_send` sends no token. Step 22 puts the UI and API behind single sign-on. Until then, keep the port-forwards on 127.0.0.1 and do not expose this cluster.

Send the first message:

```bash run id=step-7-first-turn
TURN=$(kagent_send assistant "Reply with exactly: up and running")
echo "$TURN"
export FIRST_CONTEXT=$(echo "$TURN" | cut -f2)
```

**Verified on 2026-11-02:**

```text
TASK_STATE_COMPLETED	01a19ea9-2405-73cc-94e0-8151075388c8	up and running
```

`TASK_STATE_COMPLETED`, the session's ID, and the reply. An answer alone does not prove the call
went through the gateway, so check both ends.

The session's actor is named after its ID. Its egress policy is what kagent told Substrate's
egress gateway to allow:

```bash run id=step-7-egress-policy
kubectl-ate get egress-policy "session-${FIRST_CONTEXT}" --atespace kagent -o json \
  | jq -r '.rules[] | to_entries[0] | "\(.key)\t\(.value.hostnames[0]):\(.value.ports.numbers[0])\t\([.value.effects.replaceHeaders[]? | "\(.header) <- \(.credentialUri // "actor JWT, audience \(.actorJwt.audiences | join(",")), \(.actorJwt.expirationSeconds)s")"] | join(", "))"'
```

**Verified on 2026-11-02:**

```text
Warning: no --token-file given; acting as the shared identity system:serviceaccount:ate-system:ate-client rather than as yourself. Pass --token-file to act as yourself.
http	agentgateway-proxy.agentgateway-system.svc.cluster.local:80	x-api-key <- ate-secret://k8s.io/default/kagent/kagent-anthropic/ANTHROPIC_API_KEY
http	kagent-controller.kagent.svc.cluster.local:8083	authorization <- actor JWT, audience kagent-runtime, 300s
https	api.anthropic.com:443	
```

kubectl-ate's first line is a warning on every call that does not pass `--token-file`: it is using
the shared `ate-client` identity `kagent install` set up for it, not one of yours.

The gateway rule replaces `x-api-key` with the Secret from Step 4, which holds the consumer key.
The controller rule gives every call the actor makes back to kagent a Bearer token that Substrate
mints for that actor and that expires after five minutes, so the controller knows which session is
calling without the actor holding a credential of its own. The `api.anthropic.com` rule is added by
kagent for the provider whatever `baseUrl` says, with no credential on it, so an actor can reach
Anthropic but has nothing to authenticate with.

And the gateway's own record of the call:

```bash run id=step-7-gateway-log
EGRESS_IP=$(kubectl get pod -n ate-system -l app=atenet-egress -o jsonpath='{.items[0].status.podIP}')
echo "atenet-egress: ${EGRESS_IP}"
kubectl logs -n agentgateway-system deployment/agentgateway-proxy --since=10m \
  | grep 'route=agentgateway-system/anthropic-messages' | grep "src.addr=${EGRESS_IP}:" | tail -1 \
  | tr ' ' '\n' | grep -E '^(route|src.addr|http.status|gen_ai.usage.input_tokens|gen_ai.usage.output_tokens|endpoint)='
```

**Verified on 2026-11-02:**

```text
atenet-egress: 10.244.0.17
route=agentgateway-system/anthropic-messages
endpoint=api.anthropic.com:443
src.addr=10.244.0.17:38160
http.status=200
gen_ai.usage.input_tokens=1849
gen_ai.usage.output_tokens=6
```

The call arrived from `atenet-egress`'s address, on the `anthropic-messages` route, and went on to
Anthropic. The log does not name the key, but that route refuses any request without a valid
consumer key (Step 2), so a 200 there means the consumer key was presented and accepted. The token
counts are the gateway's own, which is what token caps in Isolate are built on.

**What you now have:** an agent that never holds a model key, sessions that each run in their own
sandbox and stop costing memory when a turn ends, and a gateway that every model call passes
through and nothing else in the cluster can reach. What you do not have yet is anything protecting
the UI and API, or the snapshot store, from the rest of the cluster. Isolate closes both.

## OPERATE

Up and Running proved one turn works. This section follows a session through its whole life,
so that when something misbehaves you know which part to look at.

**What you are going to do:** see what kagent created in Substrate for the agent, follow one
session through two turns and a suspension, turn on tracing and read one turn end to end, inspect
and act on its actor directly, find where its context is stored, delete it and check what is left,
and run the same agent on a second runtime.

**What you will have that you did not have before:** a working model of the agent lifecycle on this
platform, and the commands to see each stage of it on your own cluster.

**Verified on:** a kind cluster created fresh for it (kind 0.32.0, `kindest/node:v1.35.0`), 2026-11-02 23:43 UTC: every block from the top of this guide to the end of Step 14 executed verbatim in one pass of `auto-build/run.sh` under bash, from an empty directory outside the repository. Every output block in Operate comes from that run.

### Step 8: How an agent becomes actors

Applying the `Agent` in Step 6 did not start a pod. kagent asked Substrate for an **actor template**:
the image, the sandbox class and the egress policy every session with this agent will get.
Substrate started one actor from it, let it initialize, and saved its volumes as a **golden
snapshot**. That snapshot is what `Ready` meant.

From here on, kubectl-ate's warning about the shared identity (Step 7) goes to `/dev/null`, so the
output is only the answer. Leave it in when a call fails: errors go to the same stream.

```bash run id=step-8-template
kubectl-ate get actor-template -a kagent 2>/dev/null
kubectl-ate get tags -a kagent 2>/dev/null
```

**Verified on 2026-11-02:**

```text
ATESPACE   NAME                     GOLDEN TAG                             ERROR   AGE
kagent     assistant-ef2afecb25fc   417f09ac-0323-41cc-b065-be336531f7b3           18s
ATESPACE   NAME                                   SCOPE                 STATE   SNAPSHOT                                                                               FIDELITY                    AGE
kagent     417f09ac-0323-41cc-b065-be336531f7b3   TAG_SCOPE_PUBLISHED   Ready   gs://ate-snapshots/kagent/atespaces/kagent/tags/59ca8c00-3196-4fdf-a4c8-7a1aed1f4fe6   SNAPSHOT_FIDELITY_VOLUMES   3s
```

The template's `GOLDEN TAG` is a tag in the agent's own atespace, and the tag's snapshot is in the
snapshot store beside the agent's sessions. Every agent you declare gets one of each. Its fidelity,
`VOLUMES`, is the lowest Substrate offers: the snapshot holds the sandbox's durable volumes and
not its process memory, so a session starts by booting the runtime image with those volumes in
place. kagent asks for this fidelity for every template and every session, and it is not a
setting.

Then each session becomes its own **actor**, named `session-<session id>`, in the `kagent`
atespace:

```bash run id=step-8-actors
kubectl-ate get actors -a kagent 2>/dev/null
kubectl-ate get workers 2>/dev/null
```

**Verified on 2026-11-02:**

```text
ATESPACE   NAME                                           TEMPLATE                        STATE                   WORKER POD   WORKER IPS   VERSION   AGE
kagent     session-01a19ea9-2405-73cc-94e0-8151075388c8   kagent/assistant-ef2afecb25fc   ACTOR_STATE_SUSPENDED   <none>                    5         1s
NAME                                   POOL             STATE                 ACTORS   CPU(MILLICORES)   MEMORY         POD                                      AGE
814d9ff9-7252-4614-ba17-65c793f79966   kagent-default   WORKER_STATE_ACTIVE   0/1000   0/6000            0/12154772Ki   kagent/kagent-default-5ffdf9d9f7-pwpbq   81s
```

Step 7's session is `SUSPENDED`, with no worker pod and no IP. It holds nothing on a worker
until its next message. The worker reports the actors placed on it right now, which is none.

There is no long-lived actor per agent. Ten sessions with one agent are ten actors, each
started from the same golden snapshot, which is why Scale counts sessions rather than agents.

### Step 9: Follow one session

Two helpers. `actor_events` reads atelet's log, where Substrate records every restore and
checkpoint with its timing. `wait_suspended` polls the actor until it is suspended:

```bash run id=step-9-helpers
# actor_events <session id>: restores and checkpoints for that session's actor.
actor_events() {
  kubectl logs -n ate-system daemonset/atelet -c atelet --since=30m \
    | jq -r --arg a "session-$1" 'select(."ate.actor.name" == $a and (.msg | test("timing breakdown")))
        | [.time[11:23], .msg, ."ate.snapshot.kind" + "/" + ."ate.snapshot.fidelity",
           (((."ate.actor.restore.duration.total" // ."ate.actor.checkpoint.duration.total") * 1000 | floor | tostring) + " ms")]
        | @tsv'
}

# wait_suspended <session id>: block until the actor is suspended, at most 60s.
wait_suspended() {
  local waited=0 state
  while [ "$waited" -lt 60 ]; do
    state=$(kubectl-ate get actor "session-$1" -a kagent -o json 2>/dev/null | jq -r .status.state)
    [ "$state" = ACTOR_STATE_SUSPENDED ] && { echo "suspended (polled ${waited}s after the reply)"; return 0; }
    sleep 1; waited=$((waited + 1))
  done
  echo "still ${state} after 60s" >&2; return 1
}
```

`daemonset/atelet` reads one atelet pod. On a single-node cluster that is the only one; on more
nodes, read the pod on the node the actor ran on.

Send the first turn of a new session, then watch its actor:

```bash run id=step-9-turn-1
TURN=$(kagent_send assistant "My project is called Larkspur. Reply with OK.")
echo "$TURN"
export CONV=$(echo "$TURN" | cut -f2)
wait_suspended "$CONV"
kubectl-ate get actor "session-${CONV}" -a kagent 2>/dev/null
```

**Verified on 2026-11-02:**

```text
TASK_STATE_COMPLETED	01a19ea9-2902-729f-a03a-026abadb1269	OK.
suspended (polled 0s after the reply)
ATESPACE   NAME                                           TEMPLATE                        STATE                   WORKER POD   WORKER IPS   VERSION   AGE
kagent     session-01a19ea9-2902-729f-a03a-026abadb1269   kagent/assistant-ef2afecb25fc   ACTOR_STATE_SUSPENDED   <none>                    5         0s
```

The actor was suspended as soon as the turn ended. **There is no idle timeout to configure.** kagent
suspends a session's actor when its last turn finishes, so idle lasts about a second. A turn
that ends waiting for input (approval, authentication) pauses the actor on its worker instead of
suspending it.

Send the second turn in the same session:

```bash run id=step-9-turn-2
kagent_send assistant "What is my project called?" "$CONV"
wait_suspended "$CONV"
actor_events "$CONV"
```

**Verified on 2026-11-02:**

```text
TASK_STATE_COMPLETED	01a19ea9-2902-729f-a03a-026abadb1269	Your project is called **Larkspur**.
suspended (polled 1s after the reply)
23:47:28.800	Restore timing breakdown	latest/volumes	63 ms
23:47:29.377	Checkpoint timing breakdown	latest/volumes	36 ms
23:47:29.800	Restore timing breakdown	local/volumes	66 ms
23:47:30.426	Checkpoint timing breakdown	latest/volumes	37 ms
```

The agent remembered, and atelet's log shows how:

| Event | Snapshot | What it means |
|---|---|---|
| First restore | `latest/volumes` | The new actor started, with the volumes of the agent's golden snapshot (Step 8) |
| Checkpoint | `latest/volumes` | At the end of the turn, the session's volumes were saved |
| Second restore | `local/volumes` | The session's volumes as the last turn left them, from the copy kept on this node |
| Checkpoint | `latest/volumes` | Saved again, replacing the previous one |

Every snapshot is `volumes`: the runtime keeps the session in a database on its volume (Step 12),
so that is all a resume needs. Whatever the runtime held only in memory when it suspended is not
kept, and each resume boots the runtime image again, in about 65 ms here. `local` means atelet
had the snapshot on the node already and did not fetch it from the store; Step 11 shows which node
that is.

A suspended session can resume on a different worker from the one it last ran on, because
everything it needs is in the snapshot store.

### Step 10: Trace a turn

Nothing has been recording traces so far: `kagent install` leaves telemetry off. Turn it on, with a
trace store to send it to. Jaeger, in a single pod, is the smallest one that shows a whole turn:

```bash run id=jaeger
kubectl create namespace observability --dry-run=client -o yaml | kubectl apply -f-
kubectl apply -f- <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jaeger
  namespace: observability
spec:
  replicas: 1
  selector:
    matchLabels: {app: jaeger}
  template:
    metadata:
      labels: {app: jaeger}
    spec:
      containers:
        - name: jaeger
          image: ${JAEGER_IMAGE}
          ports:
            - {name: otlp-grpc, containerPort: 4317}
            - {name: query, containerPort: 16686}
---
apiVersion: v1
kind: Service
metadata:
  name: jaeger
  namespace: observability
spec:
  selector: {app: jaeger}
  ports:
    - {name: otlp-grpc, port: 4317, targetPort: 4317}
    - {name: query, port: 16686, targetPort: 16686}
EOF
kubectl rollout status deployment/jaeger -n observability --timeout=180s
```

Point kagent at it. Two settings: `config.telemetry` for the controller, and
`config.runtimeDefaults.telemetry` for every agent runtime. `--reuse-values` keeps everything
`kagent install` set. Both URLs are full DNS names, because the runtime's sandbox can only resolve
names its egress policy allows:

```bash run id=helm-kagent-telemetry
OLD_REVISION=$(kubectl get agent assistant -n kagent -o jsonpath='{.status.latestSuccessfulRevision}')
helm upgrade kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version ${KAGENT_VERSION} \
  --namespace kagent --reuse-values --wait -f- >/dev/null <<EOF
config:
  telemetry:
    traces:
      endpoint: http://jaeger.observability.svc.cluster.local:4317
      protocol: grpc
  runtimeDefaults:
    telemetry:
      traces:
        endpoint: http://jaeger.observability.svc.cluster.local:4317
        protocol: grpc
EOF
helm history kagent -n kagent -o json | jq -r '.[-1] | "revision \(.revision): \(.status)"'
```

**Verified on 2026-11-02:**

```text
Pulled: ghcr.io/kagent-dev/kagent/helm/kagent:1.0.0-rc1
Digest: sha256:372385ceb67ea2281645ca3755c27861f8b5dc0b60c8149995e658d46f5737e8
revision 2: deployed
```

A change to the runtime's configuration is a new revision of every agent: kagent builds a new
actor template and golden snapshot for it. **The Agent stays `Ready` while it does**, because the
previous revision still serves, and a turn sent before the new one is ready runs on the old one,
untraced. Wait for the new revision rather than for `Ready`:

```bash run id=step-10-wait-revision
for i in $(seq 1 60); do
  NEW_REVISION=$(kubectl get agent assistant -n kagent -o jsonpath='{.status.latestSuccessfulRevision}')
  [ -n "$NEW_REVISION" ] && [ "$NEW_REVISION" != "$OLD_REVISION" ] && break
  sleep 5
done
echo "revision ${OLD_REVISION:0:12} -> ${NEW_REVISION:0:12}"
kubectl-ate get actor-template -a kagent 2>/dev/null
```

**Verified on 2026-11-02:**

```text
revision ef2afecb25fc -> b355498dd959
ATESPACE   NAME                     GOLDEN TAG                             ERROR   AGE
kagent     assistant-b355498dd959   ed029698-1271-406b-a939-5a30678c6e58           14s
kagent     assistant-ef2afecb25fc   417f09ac-0323-41cc-b065-be336531f7b3           74s
```

Two templates now: the new revision's, and the old one, which stays while sessions made from it
(Steps 7 and 9) exist. Those sessions keep running untraced on the old revision. New sessions start
from the new one.

The gateway traces too. Send its spans to the same place:

```bash run id=policy-agentgateway-tracing
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: agentgateway-proxy-tracing
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  frontend:
    tracing:
      backendRef:
        kind: Service
        name: jaeger
        namespace: observability
        port: 4317
      protocol: GRPC
      randomSampling: "true"
EOF
kubectl wait agentgatewaypolicy/agentgateway-proxy-tracing -n agentgateway-system \
  --for=jsonpath='{.status.ancestors[0].conditions[?(@.type=="Attached")].status}'=True --timeout=60s
```

`randomSampling: "true"` traces every request. The gateway joins the trace the agent's request
carries in its `traceparent` header, which Substrate's egress gateway passes through.

The controller restarted with the upgrade, so bring the forwards back, then send a turn in a new
session:

```bash run id=step-10-traced-turn
forwards
TURN=$(kagent_send assistant "Reply with exactly: traced")
echo "$TURN"
export TRACED_CONTEXT=$(echo "$TURN" | cut -f2)
kubectl-ate get egress-policy "session-${TRACED_CONTEXT}" --atespace kagent -o json 2>/dev/null \
  | jq -r '.rules[] | to_entries[0] | "\(.key)\t\(.value.hostnames[0]):\(.value.ports.numbers[0])"'
```

**Verified on 2026-11-02:**

```text
deployment "kagent-controller" successfully rolled out
deployment "kagent-ui" successfully rolled out
forwards up: controller 18083, ui 14000
TASK_STATE_COMPLETED	01a19eaa-0d47-7b6f-82a4-29d4a6554170	traced
http	agentgateway-proxy.agentgateway-system.svc.cluster.local:80
http	jaeger.observability.svc.cluster.local:4317
http	kagent-controller.kagent.svc.cluster.local:8083
https	api.anthropic.com:443
```

The session's egress policy now has a rule for the trace store: kagent added it, because the runtime
is configured to send there.

Read the turn's trace from Jaeger's API. The forward is opened for this block and closed at its end:

```bash run id=step-10-read-trace
kubectl --context "kind-${KIND_CLUSTER_NAME}" port-forward --address 127.0.0.1 -n observability service/jaeger ${JAEGER_PORT}:16686 >/dev/null 2>&1 &
JAEGER_PF=$!
sleep 10
TRACE=$(curl -sS "http://127.0.0.1:${JAEGER_PORT}/api/traces?service=assistant&limit=1&lookback=15m")
echo "$TRACE" | jq -r '.data[0] as $t | "trace \($t.traceID): \($t.spans | length) spans",
  ($t.spans | sort_by(.startTime) | .[]
    | select(.operationName | test("^(POST /agents|invoke_agent|generate_content|POST /v1/messages|POST api.anthropic)"))
    | "\($t.processes[.processID].serviceName)\t\(.operationName)\t\(.duration / 1000 | floor) ms")'
echo "-- the gateway's span:"
echo "$TRACE" | jq -r '.data[0] as $t | $t.spans[]
  | select($t.processes[.processID].serviceName == "agentgateway-proxy" and (.operationName | startswith("POST /v1/messages")))
  | .tags[] | select(.key | test("^(route|http.response.status_code|gen_ai.request.model|gen_ai.usage.input_tokens|gen_ai.usage.output_tokens)$"))
  | "\(.key)=\(.value)"'
kill $JAEGER_PF; wait $JAEGER_PF 2>/dev/null || true
```

**Verified on 2026-11-02:**

```text
trace 26be57d046b9ac34216584c623c677b2: 26 spans
kagent-controller	POST /agents/	866 ms
assistant	invoke_agent assistant	518 ms
assistant	generate_content claude-haiku-4-5	516 ms
agentgateway-proxy	POST /v1/messages/*	507 ms
agentgateway-proxy	POST api.anthropic.com:443	478 ms
-- the gateway's span:
gen_ai.request.model=claude-haiku-4-5
gen_ai.usage.input_tokens=1847
gen_ai.usage.output_tokens=4
http.response.status_code=200
route=agentgateway-system/anthropic-messages
```

One trace, three services. `kagent-controller` received the A2A request, the agent runtime
(service `assistant`, the agent's name) ran it and called the model, and `agentgateway-proxy`
handled that call and made its own to Anthropic. The gateway's span carries the same route and
token counts as its log in Step 7. Open `http://127.0.0.1:${JAEGER_PORT}` with the same
port-forward to browse it in Jaeger's UI.

Traces do not hold the conversation: no span here contains the prompt or the reply. The runtime
records content only when `capture_content` is set in its telemetry configuration, and the guide
leaves it off.

> [!NOTE]
> **This Jaeger is for looking, not keeping.** It stores traces in memory, in one pod, and its API and UI have no sign-in: anyone who can reach the Service can read every trace. Use a trace backend with storage and access control for anything beyond this guide.

### Step 11: Inspect and act on an actor

kagent drives actors for you. kubectl-ate lets you look at one and act on it directly, which is
what you need when a session is stuck:

```bash run id=step-11-inspect
kubectl-ate get actor "session-${CONV}" -a kagent -o json 2>/dev/null \
  | jq '{name: .metadata.name, uid: .metadata.uid, version: .metadata.version, state: .status.state, snapshot: (.status.snapshots[-1] | {uri: .durableSnapshot.object.snapshotUri, state: .durableSnapshot.state, fidelity, locality})}'
```

**Verified on 2026-11-02:**

```text
{
  "name": "session-01a19ea9-2902-729f-a03a-026abadb1269",
  "uid": "0d762116-837e-4efb-9000-221ddf8c7f5e",
  "version": "9",
  "state": "ACTOR_STATE_SUSPENDED",
  "snapshot": {
    "uri": "gs://ate-snapshots/kagent/atespaces/kagent/actors/0d762116-837e-4efb-9000-221ddf8c7f5e/snapshots/d37d76ad-91a3-43bb-8423-beb92c6ae860",
    "state": "SNAPSHOT_STORAGE_STATE_COMPLETED",
    "fidelity": "SNAPSHOT_FIDELITY_VOLUMES",
    "locality": "agents-control-plane"
  }
}
```

`version` counts state changes: every turn moves it through resuming, running, suspending and
suspended. `snapshot` is its latest checkpoint: where it is in the store, whether the upload has
finished, its fidelity (Step 8), and the node that also keeps a copy (`locality`).

Resume it by hand, see it placed on a worker, then suspend it again:

```bash run id=step-11-resume-suspend
kubectl-ate resume actor "session-${CONV}" -a kagent 2>/dev/null
kubectl-ate get workers 2>/dev/null
kubectl-ate suspend actor "session-${CONV}" -a kagent --mode sync 2>/dev/null
```

**Verified on 2026-11-02:**

```text
ATESPACE   NAME                                           TEMPLATE                        STATE                 WORKER POD                               WORKER IPS    VERSION   AGE
kagent     session-01a19ea9-2902-729f-a03a-026abadb1269   kagent/assistant-ef2afecb25fc   ACTOR_STATE_RUNNING   kagent/kagent-default-5ffdf9d9f7-pwpbq   10.244.0.25   11        69s
NAME                                   POOL             STATE                 ACTORS   CPU(MILLICORES)   MEMORY           POD                                      AGE
814d9ff9-7252-4614-ba17-65c793f79966   kagent-default   WORKER_STATE_ACTIVE   1/1000   1000/6000         1Gi/12154772Ki   kagent/kagent-default-5ffdf9d9f7-pwpbq   2m30s
ATESPACE   NAME                                           TEMPLATE                        STATE                   WORKER POD   WORKER IPS   VERSION   AGE
kagent     session-01a19ea9-2902-729f-a03a-026abadb1269   kagent/assistant-ef2afecb25fc   ACTOR_STATE_SUSPENDED   <none>                    13        69s
```

While it runs, the actor holds 1000 millicores and 1 GiB of its worker's capacity: the limits every
agent runtime gets from the chart's `config.runtimeDefaults`, which Scale comes back to.
`--mode sync` waits until the snapshot is uploaded before returning. Without it, kubectl-ate
returns once the actor stops and the upload finishes in the background.

> [!WARNING]
> **A hand-resumed actor stays running until a turn ends or you suspend it.** kagent suspends an actor when a turn finishes, and a resume with no turn never finishes one, so the actor holds its place on a worker indefinitely. Suspend what you resume.

### Step 12: Where a session's context is kept

The session survived suspension because atelet wrote it to the snapshot store. With Substrate's
bundled store, that is the RustFS bucket `ate-snapshots`, under the prefix the Harness's
`snapshotPolicy.location` named (`kagent/`).

Substrate's chart denies all ingress in `ate-system` by default and admits only atelet, ate-api
and its bucket-init Job to the store. To look inside it, admit one more pod, labelled
`app: snapshot-ls`, to the S3 port. Step 21 removes this exception again:

```bash run id=networkpolicy-rustfs-inspect
kubectl apply -f- <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: rustfs-snapshot-inspect
  namespace: ate-system
spec:
  podSelector:
    matchLabels:
      app: rustfs
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: snapshot-ls
      ports:
        - protocol: TCP
          port: 9000
EOF
```

This helper lists a prefix in that bucket from that short-lived pod. It reads the store's
credentials from the atelet DaemonSet's `objectstore-plugin` init container, which is where the
chart puts them:

```bash run id=step-12-helpers
# snapshot_ls <prefix>: list objects under s3://ate-snapshots/<prefix>.
# Uses the store's credentials from the atelet DaemonSet, unless S3_ACCESS_KEY and S3_SECRET_KEY are set.
snapshot_ls() {
  local ak sk
  ak=${S3_ACCESS_KEY:-$(kubectl get daemonset atelet -n ate-system -o json | jq -r '[(.spec.template.spec.containers + (.spec.template.spec.initContainers // []))[].env[]? | select(.name=="AWS_ACCESS_KEY_ID")][0].value')}
  sk=${S3_SECRET_KEY:-$(kubectl get daemonset atelet -n ate-system -o json | jq -r '[(.spec.template.spec.containers + (.spec.template.spec.initContainers // []))[].env[]? | select(.name=="AWS_SECRET_ACCESS_KEY")][0].value')}
  kubectl delete pod snapshot-ls -n ate-system --ignore-not-found >/dev/null
  kubectl create -f- >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: snapshot-ls
  namespace: ate-system
  labels: {app: snapshot-ls}
spec:
  restartPolicy: Never
  containers:
    - name: aws
      image: ${AWS_CLI_IMAGE}
      args: ["s3", "ls", "--recursive", "s3://ate-snapshots/$1", "--endpoint-url", "http://rustfs.ate-system.svc:9000",
             "--cli-connect-timeout", "10", "--cli-read-timeout", "10"]
      env:
        - {name: AWS_ACCESS_KEY_ID, value: "${ak}"}
        - {name: AWS_SECRET_ACCESS_KEY, value: "${sk}"}
        - {name: AWS_DEFAULT_REGION, value: us-east-1}
EOF
  local phase="" waited=0 out
  while [ "$waited" -lt 120 ]; do
    phase=$(kubectl get pod snapshot-ls -n ate-system -o jsonpath='{.status.phase}')
    case "$phase" in Succeeded|Failed) break ;; esac
    sleep 1; waited=$((waited + 1))
  done
  out=$(kubectl logs pod/snapshot-ls -n ate-system)
  if [ -n "$out" ]; then echo "$out"; else echo "(no objects)"; fi
  kubectl delete pod snapshot-ls -n ate-system --wait=false >/dev/null
}
```

`aws s3 ls` exits 1 when a prefix holds nothing, so the pod ends `Failed` for an empty prefix. The
helper waits for the pod to finish either way and prints `(no objects)` rather than reading the
phase as an answer.

List this session's snapshot, and the agent's golden images:

```bash run id=step-12-snapshot-objects
export CONV_ACTOR_UID=$(kubectl-ate get actor "session-${CONV}" -a kagent -o json 2>/dev/null | jq -r .metadata.uid)
echo "-- session ${CONV} (actor ${CONV_ACTOR_UID})"
snapshot_ls "kagent/atespaces/kagent/actors/${CONV_ACTOR_UID}/"
echo "-- golden images"
snapshot_ls "kagent/atespaces/kagent/tags/"
```

**Verified on 2026-11-02:**

```text
-- session 01a19ea9-2902-729f-a03a-026abadb1269 (actor 0d762116-837e-4efb-9000-221ddf8c7f5e)
2026-11-02 23:48:38        105 kagent/atespaces/kagent/actors/0d762116-837e-4efb-9000-221ddf8c7f5e/snapshots/24b2a8cf-df65-4fba-b433-0f5402ca3557/fs/fscheckpoint.pb.zstd
2026-11-02 23:48:38        183 kagent/atespaces/kagent/actors/0d762116-837e-4efb-9000-221ddf8c7f5e/snapshots/24b2a8cf-df65-4fba-b433-0f5402ca3557/fs/multitar.img.zstd
2026-11-02 23:48:38       1511 kagent/atespaces/kagent/actors/0d762116-837e-4efb-9000-221ddf8c7f5e/snapshots/24b2a8cf-df65-4fba-b433-0f5402ca3557/fs/pages.img.zstd
2026-11-02 23:48:38        113 kagent/atespaces/kagent/actors/0d762116-837e-4efb-9000-221ddf8c7f5e/snapshots/24b2a8cf-df65-4fba-b433-0f5402ca3557/fs/pages_meta.img.zstd
2026-11-02 23:48:38        611 kagent/atespaces/kagent/actors/0d762116-837e-4efb-9000-221ddf8c7f5e/snapshots/24b2a8cf-df65-4fba-b433-0f5402ca3557/manifest.json
-- golden images
2026-11-02 23:47:24        105 kagent/atespaces/kagent/tags/59ca8c00-3196-4fdf-a4c8-7a1aed1f4fe6/fs/fscheckpoint.pb.zstd
2026-11-02 23:47:24        183 kagent/atespaces/kagent/tags/59ca8c00-3196-4fdf-a4c8-7a1aed1f4fe6/fs/multitar.img.zstd
2026-11-02 23:47:24        793 kagent/atespaces/kagent/tags/59ca8c00-3196-4fdf-a4c8-7a1aed1f4fe6/fs/pages.img.zstd
2026-11-02 23:47:24        113 kagent/atespaces/kagent/tags/59ca8c00-3196-4fdf-a4c8-7a1aed1f4fe6/fs/pages_meta.img.zstd
2026-11-02 23:47:24        603 kagent/atespaces/kagent/tags/59ca8c00-3196-4fdf-a4c8-7a1aed1f4fe6/manifest.json
2026-11-02 23:48:19        105 kagent/atespaces/kagent/tags/a3244842-7a96-4446-ad11-0ef2979bb03a/fs/fscheckpoint.pb.zstd
2026-11-02 23:48:19        182 kagent/atespaces/kagent/tags/a3244842-7a96-4446-ad11-0ef2979bb03a/fs/multitar.img.zstd
2026-11-02 23:48:19        793 kagent/atespaces/kagent/tags/a3244842-7a96-4446-ad11-0ef2979bb03a/fs/pages.img.zstd
2026-11-02 23:48:19        113 kagent/atespaces/kagent/tags/a3244842-7a96-4446-ad11-0ef2979bb03a/fs/pages_meta.img.zstd
2026-11-02 23:48:19        603 kagent/atespaces/kagent/tags/a3244842-7a96-4446-ad11-0ef2979bb03a/manifest.json
```

Each session keeps one snapshot, which each checkpoint replaces: its volumes, under `fs/`, and a
manifest. The volumes hold the runtime's database, the session's text included. That **is the
session, in readable text**: compressed with zstd, not encrypted. A golden image has the same
shape and holds the agent's volumes at start-up, shared by every session with that agent; there is
one per revision (Step 10), and neither kind holds process memory (Step 8). kagent's tables in
PostgreSQL hold a second copy of each session's events.

> [!WARNING]
> **Anyone who can read the atelet DaemonSet has the store's credentials, and with them any pod the store admits can read every session.** The helper above works for exactly that reason, through the exception you just added. Step 21 replaces the credentials and removes the exception. Until then, treat every session on this cluster as readable by anyone who can read `ate-system`.

### Step 13: Delete a session, and what stays behind

The kagent CLI from Step 3 manages sessions through the same API the UI uses. Point it at the
controller's port-forward. With no sign-in yet (Step 7), it acts as `admin@kagent.dev`, the user
every request so far has been treated as, so it sees the sessions this guide started:

```bash run id=step-13-list
kagent --api-url "http://127.0.0.1:${KAGENT_API_PORT}" agent session list
```

**Verified on 2026-11-02:**

```text
+--------------------------------------+------------------+-------+----------------------+
| ID                                   | AGENT            | STATE | CREATED              |
+--------------------------------------+------------------+-------+----------------------+
| 01a19ea9-2405-73cc-94e0-8151075388c8 | kagent/assistant | READY | 2026-11-02T23:47:27Z |
| 01a19ea9-2902-729f-a03a-026abadb1269 | kagent/assistant | READY | 2026-11-02T23:47:28Z |
| 01a19eaa-0d47-7b6f-82a4-29d4a6554170 | kagent/assistant | READY | 2026-11-02T23:48:27Z |
+--------------------------------------+------------------+-------+----------------------+
```

Delete the session from Step 9:

```bash run id=step-13-delete
kagent --api-url "http://127.0.0.1:${KAGENT_API_PORT}" agent session delete "$CONV"
```

**Verified on 2026-11-02:**

```text
+--------------------------------------+------------------+---------+----------------------+
| ID                                   | AGENT            | STATE   | CREATED              |
+--------------------------------------+------------------+---------+----------------------+
| 01a19ea9-2902-729f-a03a-026abadb1269 | kagent/assistant | DELETED | 2026-11-02T23:47:28Z |
+--------------------------------------+------------------+---------+----------------------+
```

Then check the actor, the snapshot and the worker node:

```bash run id=step-13-check
kubectl-ate get actor "session-${CONV}" -a kagent 2>&1 | grep -v 'no --token-file' || true
echo "-- snapshot objects left for the actor:"
snapshot_ls "kagent/atespaces/kagent/actors/${CONV_ACTOR_UID}/"
echo "-- directory left on the node:"
docker exec "${KIND_CLUSTER_NAME}-control-plane" sh -c "ls /var/lib/ate/actors/${CONV_ACTOR_UID} 2>/dev/null || echo '(none)'"
```

**Verified on 2026-11-02:**

```text
Error: failed to get actor "session-01a19ea9-2902-729f-a03a-026abadb1269": rpc error: code = NotFound desc = Actor kagent/session-01a19ea9-2902-729f-a03a-026abadb1269 not found
-- snapshot objects left for the actor:
(no objects)
-- directory left on the node:
(none)
```

The actor is gone from Substrate, its snapshot from the store, and its working directory from
the node.

Deleting an `Agent` does not delete its sessions. Its actor template and golden image stay
in Substrate for as long as any of its sessions exist. Delete an agent's sessions as well as
the `Agent` when you retire it.

### Step 14: Choose a Harness

An `Agent` pairs a template with a harness, and the two are independent. The `assistant` template
says what the agent is; the harness says what runs it. This runs the same template on the Claude
Code runtime instead of kagent's Go ADK runtime, with no change to the template, the model
configuration or the gateway:

```bash run id=harness-claude-code
kubectl apply -f- <<EOF
apiVersion: api.kagent.dev/v1alpha3
kind: Harness
metadata:
  name: claude-code
  namespace: kagent
spec:
  claude: {}
  workload:
    image: ${CLAUDE_HARNESS_IMAGE}
  substrate:
    workerPoolRef:
      name: kagent-default
    snapshotPolicy:
      location: gs://ate-snapshots/kagent/
---
apiVersion: api.kagent.dev/v1alpha3
kind: Agent
metadata:
  name: assistant-claude-code
  namespace: kagent
spec:
  template:
    ref:
      name: assistant
  harnessRef:
    name: claude-code
EOF
kubectl wait agent/assistant-claude-code -n kagent --for=condition=Ready --timeout=300s
```

**Verified on 2026-11-02:**

```text
harness.api.kagent.dev/claude-code created
agent.api.kagent.dev/assistant-claude-code created
agent.api.kagent.dev/assistant-claude-code condition met
```

```bash run id=step-14-claude-code-turn
TURN=$(kagent_send assistant-claude-code "Reply with exactly: claude code harness")
echo "$TURN"
export CLAUDE_CONTEXT=$(echo "$TURN" | cut -f2)
kubectl-ate get egress-policy "session-${CLAUDE_CONTEXT}" --atespace kagent -o json 2>/dev/null \
  | jq -r '.rules[] | to_entries[0] | "\(.key)\t\(.value.hostnames[0]):\(.value.ports.numbers[0])\t\([.value.effects.replaceHeaders[]? | "\(.header) <- \(.credentialUri // "actor JWT, audience \(.actorJwt.audiences | join(",")), \(.actorJwt.expirationSeconds)s")"] | join(", "))"'
```

**Verified on 2026-11-02:**

```text
TASK_STATE_COMPLETED	01a19eaa-b7fb-7da6-aae6-29afce92d730	claude code harness
http	agentgateway-proxy.agentgateway-system.svc.cluster.local:80	x-api-key <- ate-secret://k8s.io/default/kagent/kagent-anthropic/ANTHROPIC_API_KEY
http	jaeger.observability.svc.cluster.local:4317	
http	kagent-controller.kagent.svc.cluster.local:8083	authorization <- actor JWT, audience kagent-runtime, 300s
```

Same consumer key, same injection, same gateway. The runtime changed and nothing around it did.

One difference from Step 7: this actor's allowlist has no `api.anthropic.com` rule. The kagent
runtime's actors get one whatever `baseUrl` says; the Claude Code runtime's do not, so its actors
can reach the provider only through the gateway. It does have the trace store's rule, because
Step 10's telemetry applies to every runtime.

Four harness kinds exist on this release, and an Agent's harness names exactly one:

| Kind | What runs | In this guide |
|---|---|---|
| `kagent` | kagent's Go ADK runtime | Steps 6 to 13 |
| `claude` | Claude Code | This step |
| `codex` | OpenAI Codex | Not covered: not verified on this release |
| `byo` | Your own image | Not covered: see below |

Every harness in this guide names its image in `workload.image`, pinned by digest. Only `byo` must:
for `claude` and `codex`, `workload` is optional, and the controller fills in the image from the
chart's `config.harnessImages`, which on this release ships the same Claude Code digest pinned
here. The guide names it anyway, so the image a session runs is in the Harness you read.

`byo` is the path for other frameworks, such as LangGraph or CrewAI, and it is not a plain
container: the image must implement kagent's agent contract, which kagent's adapter packages for
those frameworks provide. An unmodified framework image does not run.

**What you now have:** the lifecycle of a session, end to end, and the commands to see each
stage: the template and golden image behind an agent, an actor per session that holds nothing
on a worker while suspended, one trace per turn across kagent, the runtime and the gateway,
context in the snapshot store, and what deletion leaves behind. You have also seen how exposed
that context is. Isolate starts there.

## ISOLATE

Operate showed how a session lives. This section shows what keeps sessions, credentials and users
apart, proves each control from the inside, and closes the two things Up and Running left open: the
snapshot store and the API with no sign-in.

**What you are going to do:** look inside a sandbox, see what is left of a suspended session, prove
the agent never holds a key, test the egress allowlist from inside two runtimes, give an agent an
MCP tool through the gateway, cap the team's tokens, replace the snapshot store's credentials and
fence it, put the UI and API behind single sign-on, and prove a session belongs to the user who
started it.

**What you will have that you did not have before:** every outbound credential held outside the
agent, every session's data reachable only by the components that need it, and every caller of the
UI and API signed in, with no way around the sign-in from inside the cluster.

Each step says which runtime it shows: the kagent Go ADK runtime (agent `assistant`) or Claude Code
(agent `assistant-claude-code`, from Step 14).

**Verified on:** a kind cluster created fresh for it (kind 0.32.0, `kindest/node:v1.35.0`), 2026-11-03 01:02 UTC: every block from the top of this guide to the end of Step 23 executed verbatim in one pass of `auto-build/run.sh` under bash, from an empty directory outside the repository. Every output block in Isolate comes from that run, except `keycloak-secrets`, whose output is from a second run of that block. The browser sign-in was checked by hand on the same cluster.

### Step 15: One gVisor sandbox per session

*Runtime: kagent.*

Every actor runs in its own gVisor sandbox on a worker pod. gVisor puts a user-space kernel between
the agent and the node's kernel, so actors from different teams packed onto one worker never share a
kernel with each other or make system calls into the host's.

This helper runs a command inside a session's sandbox, through `runsc`, the gVisor runtime
Substrate unpacks onto each node:

```bash run id=step-15-helpers
# sandbox_exec <session id> <command...>: run a command inside that session's sandbox.
# The actor must be running (kubectl-ate resume actor ...).
sandbox_exec() {
  local conv=$1 uid; shift
  uid=$(kubectl-ate get actor "session-${conv}" -a kagent -o json 2>/dev/null | jq -r .metadata.uid)
  docker exec "${KIND_CLUSTER_NAME}-control-plane" sh -c '
    R=/var/lib/ate/actors/'"$uid"'/sandbox-assets/gvisor/runsc
    "$R" --root=/var/lib/ate/actors/'"$uid"'/runsc-state exec kagent "$@" 2>&1 | grep -a -v "sandbox is not running" || true' sh "$@"
}

# sandbox_procs <session id>: gVisor processes on the node for that session's actor.
sandbox_procs() {
  local uid
  uid=$(kubectl-ate get actor "session-$1" -a kagent -o json 2>/dev/null | jq -r .metadata.uid)
  docker exec "${KIND_CLUSTER_NAME}-control-plane" sh -c "ps -eo args | grep -c '^runsc-sandbox .*--root=/var/lib/ate/actors/${uid}/' || true"
}
```

On this release `runsc exec` prints `sandbox is not running` and exits non-zero after every command,
including ones that ran. The helper drops that line and ignores the status, so read the output, not
the exit code. On a cluster other than kind, run the same `runsc` command from a node shell.

Resume Step 7's session and look inside its sandbox:

```bash run id=step-15-inside
kubectl-ate resume actor "session-${FIRST_CONTEXT}" -a kagent >/dev/null 2>&1
echo "-- kernel inside the sandbox:"
sandbox_exec "$FIRST_CONTEXT" uname -a
echo "-- kernel on the node:"
docker exec "${KIND_CLUSTER_NAME}-control-plane" uname -r
echo "-- gVisor sandboxes on the node for this actor: $(sandbox_procs "$FIRST_CONTEXT")"
```

**Verified on 2026-11-02:**

```text
-- kernel inside the sandbox:
Linux actor 4.19.0-gvisor #1 SMP Sun Jan 10 15:06:54 PST 2016 aarch64 Linux
-- kernel on the node:
6.8.0-117-generic
-- gVisor sandboxes on the node for this actor: 1
```

The agent sees gVisor's kernel, not the node's. Its processes are not in the node's process table
at all; what the node runs is the gVisor sandbox around them.

### Step 16: What is left of a suspended session

*Runtime: kagent.*

Suspend the same session and look again:

```bash run id=step-16-suspended
kubectl-ate suspend actor "session-${FIRST_CONTEXT}" -a kagent 2>/dev/null
echo "-- gVisor sandboxes on the node for this actor: $(sandbox_procs "$FIRST_CONTEXT")"
echo "-- its snapshot:"
snapshot_ls "kagent/atespaces/kagent/actors/$(kubectl-ate get actor "session-${FIRST_CONTEXT}" -a kagent -o json 2>/dev/null | jq -r .metadata.uid)/"
```

**Verified on 2026-11-02:**

```text
ATESPACE   NAME                                           TEMPLATE                        STATE                   WORKER POD   WORKER IPS   VERSION   AGE
kagent     session-01a19ef2-22b5-726d-a842-058b2f823b3e   kagent/assistant-d9f06e613f41   ACTOR_STATE_SUSPENDED   <none>                    9         2m32s
-- gVisor sandboxes on the node for this actor: 0
-- its snapshot:
2026-11-03 01:07:12        105 kagent/atespaces/kagent/actors/27a9e1b9-e3e4-4077-87ec-da86efc0018a/snapshots/990d97ed-7268-47bf-82a1-a3eacdc271b9/fs/fscheckpoint.pb.zstd
2026-11-03 01:07:12        182 kagent/atespaces/kagent/actors/27a9e1b9-e3e4-4077-87ec-da86efc0018a/snapshots/990d97ed-7268-47bf-82a1-a3eacdc271b9/fs/multitar.img.zstd
2026-11-03 01:07:12       1301 kagent/atespaces/kagent/actors/27a9e1b9-e3e4-4077-87ec-da86efc0018a/snapshots/990d97ed-7268-47bf-82a1-a3eacdc271b9/fs/pages.img.zstd
2026-11-03 01:07:12        113 kagent/atespaces/kagent/actors/27a9e1b9-e3e4-4077-87ec-da86efc0018a/snapshots/990d97ed-7268-47bf-82a1-a3eacdc271b9/fs/pages_meta.img.zstd
2026-11-03 01:07:12        611 kagent/atespaces/kagent/actors/27a9e1b9-e3e4-4077-87ec-da86efc0018a/snapshots/990d97ed-7268-47bf-82a1-a3eacdc271b9/manifest.json
```

| | Running | Suspended |
|---|---|---|
| Process and sandbox | gVisor sandbox on a worker | **None** |
| Network | An IP on the worker | **None** |
| Identity certificate | Mounted in the sandbox | **None**: a new one is issued on resume |
| Session data | In the sandbox | Its volumes, one snapshot in the store (Step 12) |
| Record in Substrate | The actor | The actor, `SUSPENDED` |
| On the node | Its actor directory | Its actor directory, and a copy of its snapshot (Step 9) |

A suspended session cannot be reached, because there is nothing running to reach. What it
leaves is data: the session's volumes as they were when the turn ended, and not its memory
(Step 9). Check what that includes.

This helper streams a session's latest snapshot out of the store and decompresses it, writing
nothing to disk. Each snapshot object is 12 bytes of Substrate header (`ATESPRSE` and a format
version) in front of one zstd stream, so the helper drops those bytes and pipes the rest to your
`zstd`:

```bash run id=step-16-helpers
# snapshot_cat <session id>: the decompressed contents of that session's latest snapshot.
# Uses the store's credentials from the atelet DaemonSet, unless S3_ACCESS_KEY and S3_SECRET_KEY are set.
snapshot_cat() {
  local prefix ak sk
  prefix=$(kubectl-ate get actor "session-$1" -a kagent -o json 2>/dev/null \
    | jq -r '.status.snapshots[-1].durableSnapshot.object.snapshotUri | sub("^[a-z0-9]+://ate-snapshots/"; "")')
  ak=${S3_ACCESS_KEY:-$(kubectl get daemonset atelet -n ate-system -o json | jq -r '[(.spec.template.spec.containers + (.spec.template.spec.initContainers // []))[].env[]? | select(.name=="AWS_ACCESS_KEY_ID")][0].value')}
  sk=${S3_SECRET_KEY:-$(kubectl get daemonset atelet -n ate-system -o json | jq -r '[(.spec.template.spec.containers + (.spec.template.spec.initContainers // []))[].env[]? | select(.name=="AWS_SECRET_ACCESS_KEY")][0].value')}
  kubectl delete pod snapshot-reader -n ate-system --ignore-not-found >/dev/null
  kubectl create -f- >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: snapshot-reader
  namespace: ate-system
  labels: {app: snapshot-ls}
spec:
  restartPolicy: Never
  containers:
    - name: aws
      image: ${AWS_CLI_IMAGE}
      command: ["sleep", "300"]
      env:
        - {name: AWS_ACCESS_KEY_ID, value: "${ak}"}
        - {name: AWS_SECRET_ACCESS_KEY, value: "${sk}"}
        - {name: AWS_DEFAULT_REGION, value: us-east-1}
EOF
  kubectl wait --for=condition=Ready pod/snapshot-reader -n ate-system --timeout=120s >/dev/null
  kubectl exec -n ate-system snapshot-reader -- sh -c '
    E=http://rustfs.ate-system.svc:9000
    for o in $(aws --endpoint-url $E s3 ls --recursive "s3://ate-snapshots/$0/" | awk "/zstd\$/ {print \$4}"); do
      aws --endpoint-url $E s3 cp "s3://ate-snapshots/$o" - | tail -c +13
    done' "$prefix" | zstd -dc
  kubectl delete pod snapshot-reader -n ate-system --wait=false >/dev/null
}
```

Start a new session that carries a marker, a random code word the agent is asked to remember,
and let it suspend. The marker is the control: it is in the session, so a search that cannot
find it proves nothing. Then search the snapshot once for the marker, the consumer key and the
Anthropic key. The search reports which string matched, never the match itself:

```bash run id=step-16-snapshot-search
: "${AGENT_CONSUMER_KEY:?is empty: read it back first (Step 3)}" "${ANTHROPIC_API_KEY:?is empty}" && {
SNAP_MARKER="snapshot-control-$(openssl rand -hex 6)"
TURN=$(kagent_send assistant "Remember this code word, and reply only with OK: ${SNAP_MARKER}")
export SNAP_CONV=$(echo "$TURN" | cut -f2)
echo "$TURN" | cut -f1,3
wait_suspended "$SNAP_CONV"
kubectl-ate get actor "session-${SNAP_CONV}" -a kagent -o json 2>/dev/null | jq -r '"snapshot fidelity: " + .status.snapshots[-1].fidelity'
snapshot_cat "$SNAP_CONV" \
  | LC_ALL=C grep -a -o -F -e "$SNAP_MARKER" -e "$AGENT_CONSUMER_KEY" -e "$ANTHROPIC_API_KEY" \
  | while read -r hit; do
      case "$hit" in
        "$SNAP_MARKER") echo marker ;; "$AGENT_CONSUMER_KEY") echo consumer ;; "$ANTHROPIC_API_KEY") echo provider ;;
      esac
    done \
  | awk '{n[$1]++} END {printf "marker (control): %d, consumer key: %d, Anthropic key: %d\n", n["marker"], n["consumer"], n["provider"]}'
}
```

**Verified on 2026-11-02:**

```text
TASK_STATE_COMPLETED	OK
suspended (polled 0s after the reply)
snapshot fidelity: SNAPSHOT_FIDELITY_VOLUMES
marker (control): 1, consumer key: 0, Anthropic key: 0
```

The marker is there: the session is in the snapshot, in the runtime's database. Neither key is.
The agent never held the Anthropic key, and the consumer key is added by Substrate's egress gateway
after the request leaves the sandbox (Step 17), so neither reaches the actor. The snapshot's fidelity
is `VOLUMES`: it holds what the runtime wrote to its volumes, and nothing it held only in memory, so this search covers everything a suspended session leaves in the store. A marker count
of 0 means the search failed, not that the snapshot is clean: check that `zstd` is installed.

What the store does hold is every session's text, and Step 21 is about who can read it.

### Step 17: The agent never holds a key

*Runtimes: kagent and Claude Code.*

Both agents authenticate to the gateway with the consumer key, and neither process ever sees it.
Resume one session from each runtime:

```bash run id=step-17-resume
kubectl-ate resume actor "session-${FIRST_CONTEXT}" -a kagent >/dev/null 2>&1
kubectl-ate resume actor "session-${CLAUDE_CONTEXT}" -a kagent >/dev/null 2>&1
kubectl-ate get actors -a kagent 2>/dev/null | grep -E "NAME|${FIRST_CONTEXT}|${CLAUDE_CONTEXT}"
```

**Verified on 2026-11-02:**

```text
ATESPACE   NAME                                           TEMPLATE                                    STATE                   WORKER POD                               WORKER IPS    VERSION   AGE
kagent     session-01a19ef4-6d72-73b6-8182-7d20d6852062   kagent/assistant-claude-code-d0cef9cf4995   ACTOR_STATE_RUNNING     kagent/kagent-default-5ffdf9d9f7-68v2d   10.244.0.25   7         9s
kagent     session-01a19ef2-22b5-726d-a842-058b2f823b3e   kagent/assistant-d9f06e613f41               ACTOR_STATE_RUNNING     kagent/kagent-default-5ffdf9d9f7-68v2d   10.244.0.25   11        2m39s
```

What each agent process has for a credential:

```bash run id=step-17-environment
for conv in "$FIRST_CONTEXT" "$CLAUDE_CONTEXT"; do
  echo "-- session-${conv}"
  sandbox_exec "$conv" cat /proc/1/environ | tr '\0' '\n' | grep -E '^(ANTHROPIC_API_KEY|KAGENT_CREDENTIAL_)' || true
done
```

**Verified on 2026-11-02:**

```text
-- session-01a19ef2-22b5-726d-a842-058b2f823b3e
ANTHROPIC_API_KEY=kagent-credential-injected
-- session-01a19ef4-6d72-73b6-8182-7d20d6852062
ANTHROPIC_API_KEY=kagent-credential-injected
```

The placeholder, in both. Now look for the real consumer key anywhere in either sandbox's
filesystem. The session's own text is the control: it is in the runtime's database, so a search
that cannot find it proves nothing.

```bash run id=step-17-search
# count_in_sandbox <session id> <string>: lines of the sandbox's filesystem containing the string.
count_in_sandbox() {
  sandbox_exec "$1" tar -cf - --exclude=./proc --exclude=./sys -C / . | LC_ALL=C grep -a -c -F "$2" || true
}
for pair in "${FIRST_CONTEXT}:up and running" "${CLAUDE_CONTEXT}:claude code harness"; do
  conv=${pair%%:*}; control=${pair#*:}
  echo "-- session-${conv}: consumer key $(count_in_sandbox "$conv" "$AGENT_CONSUMER_KEY"), control \"${control}\" $(count_in_sandbox "$conv" "$control")"
done
```

**Verified on 2026-11-02:**

```text
-- session-01a19ef2-22b5-726d-a842-058b2f823b3e: consumer key 0, control "up and running" 1
-- session-01a19ef4-6d72-73b6-8182-7d20d6852062: consumer key 0, control "claude code harness" 6
```

The key is not in either sandbox, and the Anthropic key is not even in the `kagent` namespace: it
exists only in the gateway's Secret (Step 2).

Yet the agent authenticates. Send a request from inside the kagent sandbox, carrying only the
placeholder it was given:

```bash run id=step-17-injected
sandbox_exec "$FIRST_CONTEXT" wget -q -O - -T 30 \
  --header 'content-type: application/json' --header 'anthropic-version: 2023-06-01' \
  --header 'x-api-key: kagent-credential-injected' \
  --post-data "{\"model\":\"${MODEL}\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: injected\"}]}" \
  http://agentgateway-proxy.agentgateway-system.svc.cluster.local/v1/messages | jq -r '.content[0].text'
kubectl-ate suspend actor "session-${FIRST_CONTEXT}" -a kagent >/dev/null 2>&1
kubectl-ate suspend actor "session-${CLAUDE_CONTEXT}" -a kagent >/dev/null 2>&1
```

**Verified on 2026-11-02:**

```text
injected
```

The gateway answered a request whose key, as sent, was the placeholder. Substrate's egress gateway
replaced it with the consumer key on the way out, as the egress policy in Step 7 said it would. An
attacker who takes over the agent process gets the placeholder.

> [!NOTE]
> **The agent never holds the key; the kagent controller does.** The controller reads the Secrets it turns into egress bindings, and sends MCP header values itself during tool discovery (Step 19). Restrict who can read the `kagent` namespace's Secrets and exec into its pods.

### Step 18: Test the egress allowlist from inside

*Runtimes: kagent and Claude Code, which differ here.*

Each actor can reach only the destinations kagent put in its egress policy. Try three from
inside each runtime's sandbox: a site no policy names, the provider, and the gateway:

```bash run id=step-18-allowlist
kubectl-ate resume actor "session-${FIRST_CONTEXT}" -a kagent >/dev/null 2>&1
kubectl-ate resume actor "session-${CLAUDE_CONTEXT}" -a kagent >/dev/null 2>&1
for conv in "$FIRST_CONTEXT" "$CLAUDE_CONTEXT"; do
  echo "-- session-${conv}"
  for url in https://example.com/ https://api.anthropic.com/v1/models \
             http://agentgateway-proxy.agentgateway-system.svc.cluster.local/v1/messages; do
    printf '  %-80s %s\n' "$url" "$(sandbox_exec "$conv" wget -T 10 -O /dev/null "$url" | grep -o -E 'HTTP/1.1 [0-9]+ [A-Za-z ]+|bad address' | head -1)"
  done
done
kubectl-ate suspend actor "session-${FIRST_CONTEXT}" -a kagent >/dev/null 2>&1
kubectl-ate suspend actor "session-${CLAUDE_CONTEXT}" -a kagent >/dev/null 2>&1
```

**Verified on 2026-11-02:**

```text
-- session-01a19ef2-22b5-726d-a842-058b2f823b3e
  https://example.com/                                                             bad address
  https://api.anthropic.com/v1/models                                              HTTP/1.1 401 Unauthorized
  http://agentgateway-proxy.agentgateway-system.svc.cluster.local/v1/messages      HTTP/1.1 401 Unauthorized
-- session-01a19ef4-6d72-73b6-8182-7d20d6852062
  https://example.com/                                                             bad address
  https://api.anthropic.com/v1/models                                              bad address
  http://agentgateway-proxy.agentgateway-system.svc.cluster.local/v1/messages      HTTP/1.1 401 Unauthorized
```

| Result | Meaning |
|---|---|
| `bad address` | Not in the egress policy: the name does not resolve inside the sandbox |
| `401 Unauthorized` | In the egress policy, no credential sent: the destination answered, so it is reachable |

`example.com` is unreachable for both: a name outside the policy does not even resolve. The
gateway is reachable for both. `api.anthropic.com` is the difference: the kagent runtime's actors can reach it directly (without a key, so they cannot use
it), while the Claude Code runtime's actors cannot reach it at all. With the kagent runtime, the
allowlist alone does not force model traffic through the gateway; on this release no supported
setting removes that rule.

### Step 19: Give an agent an MCP tool through the gateway

*Runtime: kagent.*

An MCP server's credential is handled like a model key: kagent turns a Secret-backed header on a
`RemoteMCPServer` into an egress binding, and the agent gets a placeholder. Put the server behind
the gateway and the agent needs only the consumer key; the server's own credential stays
in the gateway.

Deploy an MCP server that requires a bearer token. It is a small reference server, written for this
guide with Python's standard library: one tool, `lookup_code`, which returns a code the model cannot
guess, and a log line per request saying whether the token matched, never the token itself.

```bash run id=mcp-deployment-codes
kubectl get namespace mcp-tools >/dev/null 2>&1 || kubectl create namespace mcp-tools
: "${MCP_SERVER_TOKEN:=$(openssl rand -hex 24)}"; export MCP_SERVER_TOKEN
kubectl delete secret deployment-codes-token -n mcp-tools --ignore-not-found
printf '%s' "$MCP_SERVER_TOKEN" | kubectl create secret generic deployment-codes-token \
  -n mcp-tools --from-file=MCP_TOKEN=/dev/stdin
kubectl delete configmap deployment-codes-server -n mcp-tools --ignore-not-found
kubectl create configmap deployment-codes-server -n mcp-tools --from-file=server.py=/dev/stdin <<'PY'
# Minimal MCP server: streamable HTTP, JSON responses, one tool.
# Requires "Authorization: Bearer <MCP_TOKEN>". Logs a classification of each request's
# Authorization header, never its value.
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ["MCP_TOKEN"]
PLACEHOLDER = "kagent-credential-injected"
CODE = "K7-teal-otter-0429"
TOOL = {"name": "lookup_code", "description": "Look up the deployment code for a named service.",
        "inputSchema": {"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]}}

def classify(v):
    if v is None: return "absent"
    if v == "Bearer " + TOKEN: return "bearer-match"
    if v == TOKEN: return "raw-match"
    if PLACEHOLDER in v: return "placeholder"
    return f"other(len={len(v)})"

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def log(self, **kw):
        kw.update(t=time.strftime("%H:%M:%S", time.gmtime()), src=self.client_address[0], verb=self.command,
                  auth=classify(self.headers.get("Authorization")),
                  headers=sorted(k.lower() for k in self.headers.keys()))
        print(json.dumps(kw), flush=True)
    def send(self, code, body=None, ctype="application/json"):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        if body is not None: self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Mcp-Session-Id", "c10")
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        self.log(rpc="GET"); self.send(405)
    def do_DELETE(self):
        self.log(rpc="DELETE"); self.send(200)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        msg = json.loads(self.rfile.read(n) or b"{}")
        method = msg.get("method"); auth = classify(self.headers.get("Authorization"))
        extra = {}
        if method == "tools/call": extra["tool"] = msg.get("params", {}).get("name")
        self.log(rpc=method, **extra)
        if auth != "bearer-match":
            return self.send(401, {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32001, "message": "unauthorized"}})
        if "id" not in msg:
            return self.send(202)
        if method == "initialize":
            ver = msg.get("params", {}).get("protocolVersion", "2025-03-26")
            res = {"protocolVersion": ver, "capabilities": {"tools": {}}, "serverInfo": {"name": "c10-stub", "version": "1"}}
        elif method == "tools/list":
            res = {"tools": [TOOL]}
        elif method == "tools/call":
            svc = msg.get("params", {}).get("arguments", {}).get("service", "")
            res = {"content": [{"type": "text", "text": f"The deployment code for {svc} is {CODE}."}]}
        elif method == "ping":
            res = {}
        else:
            return self.send(200, {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "method not found"}})
        self.send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": res})

ThreadingHTTPServer(("", 8080), H).serve_forever()
PY
kubectl apply -f- <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: deployment-codes
  namespace: mcp-tools
spec:
  replicas: 1
  selector:
    matchLabels: {app: deployment-codes}
  template:
    metadata:
      labels: {app: deployment-codes}
    spec:
      containers:
        - name: server
          image: ${MCP_SERVER_IMAGE}
          command: ["python", "-u", "/app/server.py"]
          env:
            - name: MCP_TOKEN
              valueFrom: {secretKeyRef: {name: deployment-codes-token, key: MCP_TOKEN}}
          ports: [{containerPort: 8080}]
          volumeMounts: [{name: app, mountPath: /app}]
      volumes:
        - name: app
          configMap: {name: deployment-codes-server}
---
apiVersion: v1
kind: Service
metadata:
  name: deployment-codes
  namespace: mcp-tools
spec:
  selector: {app: deployment-codes}
  ports: [{name: http, port: 8080, targetPort: 8080}]
EOF
kubectl rollout status deployment/deployment-codes -n mcp-tools --timeout=180s
```

**Verified on 2026-11-02:**

```text
namespace/mcp-tools created
secret/deployment-codes-token created
configmap/deployment-codes-server created
deployment.apps/deployment-codes created
service/deployment-codes created
deployment "deployment-codes" successfully rolled out
```

Put it behind the gateway. The backend holds the server's token and sends it as
`Authorization: Bearer <token>`; the route requires the same consumer key as the model route:

```bash run id=backend-deployment-codes-mcp
kubectl delete secret deployment-codes-gw-auth -n agentgateway-system --ignore-not-found
printf '%s' "$MCP_SERVER_TOKEN" | kubectl create secret generic deployment-codes-gw-auth \
  -n agentgateway-system --from-file=Authorization=/dev/stdin
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: deployment-codes-mcp
  namespace: agentgateway-system
spec:
  mcp:
    targets:
      - name: deployment-codes
        static:
          host: deployment-codes.mcp-tools.svc.cluster.local
          port: 8080
          path: /mcp
          protocol: StreamableHTTP
          policies:
            auth:
              secretRef:
                name: deployment-codes-gw-auth
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: deployment-codes-mcp
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /mcp
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayBackend
          name: deployment-codes-mcp
          namespace: agentgateway-system
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: deployment-codes-mcp-apikey-auth
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: deployment-codes-mcp
  traffic:
    apiKeyAuthentication:
      mode: Strict
      location:
        header:
          name: x-api-key
      secretRef:
        name: llm-virtual-keys
EOF
```

> [!WARNING]
> **Every route needs its own key policy.** The policy above targets this route only. A route added later without one accepts anonymous traffic, and nothing reports it: the route is accepted, the backend answers.

Declare the tool server to kagent, pointing at the gateway with the consumer key, and an agent that
uses it:

```bash run id=agent-deployment-assistant
kubectl apply -f- <<EOF
apiVersion: api.kagent.dev/v1alpha3
kind: RemoteMCPServer
metadata:
  name: deployment-codes
  namespace: kagent
spec:
  description: Deployment codes, through agentgateway.
  protocol: STREAMABLE_HTTP
  url: http://agentgateway-proxy.agentgateway-system.svc.cluster.local/mcp
  headersFrom:
    - name: x-api-key
      valueFrom:
        type: Secret
        name: kagent-anthropic
        key: ANTHROPIC_API_KEY
---
apiVersion: api.kagent.dev/v1alpha3
kind: AgentTemplate
metadata:
  name: deployment-helper
  namespace: kagent
spec:
  description: Looks up deployment codes.
  modelConfig:
    name: default-model-config
  systemPrompt: |
    You are a helpful assistant. Use the lookup_code tool when asked for a deployment code. Answer concisely.
  tools:
    - mcp:
        server:
          kind: RemoteMCPServer
          name: deployment-codes
        tools: [lookup_code]
---
apiVersion: api.kagent.dev/v1alpha3
kind: Agent
metadata:
  name: deployment-helper
  namespace: kagent
spec:
  template:
    ref:
      name: deployment-helper
  harnessRef:
    name: kagent-adk
EOF
kubectl wait remotemcpserver/deployment-codes -n kagent --for=condition=Accepted --timeout=120s
kubectl get remotemcpserver deployment-codes -n kagent -o jsonpath='{.status.conditions[0].reason}: {.status.conditions[0].message}{"\n"}'
kubectl wait agent/deployment-helper -n kagent --for=condition=Ready --timeout=300s
```

**Verified on 2026-11-02:**

```text
remotemcpserver.api.kagent.dev/deployment-codes created
agenttemplate.api.kagent.dev/deployment-helper created
agent.api.kagent.dev/deployment-helper created
remotemcpserver.api.kagent.dev/deployment-codes condition met
DiscoverySucceeded: Discovered 1 MCP tools
agent.api.kagent.dev/deployment-helper condition met
```

`DiscoverySucceeded` depends on Step 5's second rule. The kagent controller discovers tools by
calling the server's URL itself, from its own pod; without that rule the gateway drops it, the
`RemoteMCPServer` reports `DiscoveryFailed … context deadline exceeded`, and the agent still goes
`Ready` and still works, so nothing else tells you.

Ask for a code, then read the session's egress policy and the MCP server's log:

```bash run id=step-19-tool-call
TURN=$(kagent_send deployment-helper "What is the deployment code for the service payments? Use the tool.")
echo "$TURN"
echo "-- egress policy:"
kubectl-ate get egress-policy "session-$(echo "$TURN" | cut -f2)" --atespace kagent -o json 2>/dev/null \
  | jq -r '.rules[] | to_entries[0] | "\(.key)\t\(.value.hostnames[0]):\(.value.ports.numbers[0])\t\([.value.effects.replaceHeaders[]? | "\(.header) <- \(.credentialUri // "actor JWT, audience \(.actorJwt.audiences | join(",")), \(.actorJwt.expirationSeconds)s")"] | join(", "))"'
echo "-- MCP server log for the tool call (gateway pod: $(kubectl get pod -n agentgateway-system -l gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[0].status.podIP}')):"
kubectl logs -n mcp-tools deployment/deployment-codes | jq -c 'select(.rpc == "tools/call") | {t, src, rpc, tool, auth}' | tail -1
```

**Verified on 2026-11-02:**

```text
TASK_STATE_COMPLETED	01a19ef4-fe01-7a10-9091-ffe616d29ade	The deployment code for the **payments** service is **K7-teal-otter-0429**.
-- egress policy:
http	agentgateway-proxy.agentgateway-system.svc.cluster.local:80	x-api-key <- ate-secret://k8s.io/default/kagent/kagent-anthropic/ANTHROPIC_API_KEY
http	jaeger.observability.svc.cluster.local:4317	
http	kagent-controller.kagent.svc.cluster.local:8083	authorization <- actor JWT, audience kagent-runtime, 300s
https	api.anthropic.com:443	
-- MCP server log for the tool call (gateway pod: 10.244.0.6):
{"t":"01:10:19","src":"10.244.0.6","rpc":"tools/call","tool":"lookup_code","auth":"bearer-match"}
```

The answer carries the code only the server knows, so the tool ran. The actor's policy names the
gateway, not the MCP server: it has no route to the server except through the gateway. The server
saw the call come from the gateway's pod with its own token (`bearer-match`). The agent held only a
placeholder for the consumer key, and never had the server's token at all.

> [!WARNING]
> **The tool call's arguments and its full response are stored in the turn's trace, whatever the content-capture setting.** The kagent runtime's `execute_tool` span records both (`gcp.vertex.agent.tool_call_args` and `gcp.vertex.agent.tool_response`): here, the service name asked for and the deployment code that came back. Anything an MCP tool returns ends up in the trace store for as long as traces are kept, and anyone who can reach Jaeger can read it (Step 10).

### Step 20: Cap each team's tokens

*Runtime: either. Both agents present the same consumer key, so they share the team's cap.*

The consumer key's metadata names a `group` (`team-agents`, Step 2). agentgateway can count tokens
per value of any expression, so a cap keyed on `apiKey.group` covers every agent holding the key,
whatever runtime it uses. Cap the model route at 200,000 tokens an hour per team, and label the
gateway's metrics with the caller:

```bash run id=policy-team-token-caps
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: team-token-caps
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: anthropic-messages
  traffic:
    rateLimit:
      local:
        - tokens: 200000
          unit: Hours
          key: apiKey.group
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: llm-caller-attribution
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  frontend:
    metrics:
      attributes:
        add:
          - name: user_id
            expression: 'apiKey.user'
          - name: team
            expression: 'apiKey.group'
EOF
kubectl wait agentgatewaypolicy/team-token-caps agentgatewaypolicy/llm-caller-attribution -n agentgateway-system \
  --for=jsonpath='{.status.ancestors[0].conditions[?(@.type=="Attached")].status}'=True --timeout=60s
```

**Verified on 2026-11-02:**

```text
agentgatewaypolicy.agentgateway.dev/team-token-caps created
agentgatewaypolicy.agentgateway.dev/llm-caller-attribution created
agentgatewaypolicy.agentgateway.dev/team-token-caps condition met
agentgatewaypolicy.agentgateway.dev/llm-caller-attribution condition met
```

The cap counts input and output tokens together, and a request's tokens are only known when it
finishes, so a request is never cut off part way: the next one is refused. The longest window is an
hour (`Seconds`, `Minutes` or `Hours`). Size the cap for the runtime the team uses: a turn of the
Claude Code runtime carries a much larger system prompt than a turn of the kagent runtime.

> [!WARNING]
> **The cap is per gateway replica.** A `local` limit is kept in each proxy pod's memory, so two replicas let the team spend twice the cap, and a proxy restart starts the count again. A cap that holds across replicas needs agentgateway's `global` rate limit, which calls out to a rate-limit service this guide does not deploy.

Send a turn from each runtime, then read the gateway's token metrics. The metrics port is 15020 on
the proxy; the forward uses local port 18082, and the block checks the forward started, because a
forward that cannot bind fails silently in the background while `curl` reads whatever already holds
the port:

```bash run id=step-20-attribution
kagent_send assistant "Reply with exactly: attributed"
kagent_send assistant-claude-code "Reply with exactly: attributed"
kubectl --context "kind-${KIND_CLUSTER_NAME}" port-forward --address 127.0.0.1 -n agentgateway-system deployment/agentgateway-proxy 18082:15020 >/dev/null 2>&1 &
PF=$!; sleep 3
if ! kill -0 $PF 2>/dev/null; then
  echo "the port-forward on 18082 did not start: something else holds the port" >&2
else
  curl -sS http://127.0.0.1:18082/metrics | grep '^agentgateway_gen_ai_client_token_usage_sum' | grep 'team="team-agents"' \
    | while read -r line; do
        printf '%s %s\n' "$(printf '%s' "$line" | grep -oE '(team|user_id|gen_ai_token_type)="[^"]*"' | tr '\n' ' ')" "${line##* }"
      done
  kill $PF; wait $PF 2>/dev/null || true
fi
```

**Verified on 2026-11-02:**

```text
TASK_STATE_COMPLETED	01a19ef5-0788-7657-933d-ef9a64ed8416	attributed
TASK_STATE_COMPLETED	01a19ef5-0a77-779f-ae91-e816f39bc1e0	attributed
gen_ai_token_type="input_cache_write" user_id="assistant-agent" team="team-agents"  2698.0
gen_ai_token_type="input" user_id="assistant-agent" team="team-agents"  36075.0
gen_ai_token_type="output" user_id="assistant-agent" team="team-agents"  68.0
gen_ai_token_type="input_cache_read" user_id="assistant-agent" team="team-agents"  31520.0
```

Every token is attributed to the key's `user` and `group`, so spend is per team or per agent key,
not per person: the kagent user behind a session is not passed to the gateway. The series count
from when the attribution policy was applied.

Prove the cap is enforced, not just accepted: drop it to one token, send a turn, then put it back.
A turn the cap refuses does not end on its own (below), so the block streams it for 15 seconds,
reads its state, checks the gateway refused it, and cancels it:

```bash run id=step-20-cap-block
kubectl patch agentgatewaypolicy team-token-caps -n agentgateway-system --type json \
  -p '[{"op":"replace","path":"/spec/traffic/rateLimit/local/0/tokens","value":1}]'
sleep 5
kagent_send assistant "Reply with exactly: this one is counted" | cut -f1,3
OVER=$( { curl -sS -N --max-time 15 -X POST "http://127.0.0.1:${KAGENT_API_PORT}/agents/kagent/assistant" \
    -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"SendStreamingMessage\",\"params\":{\"message\":{\"messageId\":\"$(openssl rand -hex 8)\",\"role\":\"ROLE_USER\",\"parts\":[{\"text\":\"Reply with exactly: over the cap\"}]}}}" \
    2>/dev/null || true; } \
  | sed -n 's/^data: //p' | jq -r 'select(.result.statusUpdate) | "\(.result.statusUpdate.taskId) \(.result.statusUpdate.status.state)"' | tail -1)
echo "over-cap turn after 15 s: ${OVER#* }"
echo "at the gateway: $(kubectl logs -n agentgateway-system deployment/agentgateway-proxy --since=1m \
  | grep 'route=agentgateway-system/anthropic-messages' | grep 'http.status=429' | tail -1 | grep -oE 'http.status=[0-9]+|reason=[A-Za-z]+' | tr '\n' ' ')"
CANCELLED=$(curl -sS --max-time 30 -X POST "http://127.0.0.1:${KAGENT_API_PORT}/agents/kagent/assistant" \
  -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"CancelTask\",\"params\":{\"id\":\"${OVER%% *}\"}}" | jq -r '.result.status.state // .error.message')
echo "cancel: ${CANCELLED}"
kubectl patch agentgatewaypolicy team-token-caps -n agentgateway-system --type json \
  -p '[{"op":"replace","path":"/spec/traffic/rateLimit/local/0/tokens","value":200000}]'
sleep 5
kagent_send assistant "Reply with exactly: back under the cap" | cut -f1,3
```

**Verified on 2026-11-02:**

```text
agentgatewaypolicy.agentgateway.dev/team-token-caps patched
TASK_STATE_COMPLETED	this one is counted
over-cap turn after 15 s: TASK_STATE_WORKING
at the gateway: http.status=429 reason=RateLimit 
cancel: TASK_STATE_CANCELED
agentgatewaypolicy.agentgateway.dev/team-token-caps patched
TASK_STATE_COMPLETED	back under the cap
```

The first turn is let through and counted; the next one is over the cap. The gateway refuses its
model call with `429`, and the turn does not fail: the refusal carries `Retry-After` set to the
time left in the window, up to an hour, and the kagent runtime waits that long before trying
again. The turn stays working, its actor keeps running, and its session refuses another message
until the turn is cancelled with A2A's `CancelTask`, as above. Changing the cap starts its count
again, which is why the last turn goes through.

### Step 21: Replace the snapshot store's credentials and fence it

*Runtime: kagent.*

Step 12 showed the problem: the store holds every session in readable form, and Substrate's chart
runs it with fixed default credentials (`rustfsadmin`). Its network fence admits only atelet,
ate-api and bucket-init, but Step 12 added an exception, and any exception you or a later chart adds
would hand those credentials a way in. Two changes close it.

**First, non-default credentials.** The chart cannot read them from a Secret: it writes
`rustfs.accessKey` and `rustfs.secretKey` as plain values into the RustFS, bucket-init, atelet and
ate-api pod specs. So generate them in your shell and pass them at upgrade time, never in a values
file you commit. `--reuse-values` keeps everything `kagent install` set. The completed bucket-init
Job goes first, because its pod template changes with the credentials and a finished Job cannot be
updated:

```bash run id=substrate-rustfs-credentials
export RUSTFS_ACCESS_KEY=$(openssl rand -hex 10)
export RUSTFS_SECRET_KEY=$(openssl rand -hex 20)
kubectl delete job rustfs-bucket-init -n ate-system --ignore-not-found
helm upgrade substrate oci://ghcr.io/kagent-dev/substrate/helm/substrate \
  --version ${SUBSTRATE_VERSION#v} --namespace ate-system --reuse-values -f - >/dev/null <<EOF
rustfs:
  accessKey: ${RUSTFS_ACCESS_KEY}
  secretKey: ${RUSTFS_SECRET_KEY}
EOF
helm history substrate -n ate-system -o json | jq -r '.[-1] | "substrate revision \(.revision): \(.status)"'
for r in $(kubectl get deployment,daemonset -n ate-system -o name); do
  kubectl rollout status "$r" -n ate-system --timeout=300s
done
kubectl wait --for=condition=complete job/rustfs-bucket-init -n ate-system --timeout=180s
# atelet restarted: wait until every worker has registered with it again before sending a turn.
for i in $(seq 1 60); do
  want=$(kubectl get pods -n kagent -o name | grep -c '/kagent-default-')
  have=$(kubectl-ate get workers 2>/dev/null | awk 'NR > 1 && $4 != "0/0" {print $(NF-1)}' | while read -r pod; do
    kubectl get pod -n kagent "${pod#kagent/}" >/dev/null 2>&1 && echo registered; done | grep -c registered || true)
  [ "$have" -ge "$want" ] && break
  sleep 5
done
echo "workers registered: ${have} of ${want}"
```

**Verified on 2026-11-02:**

```text
job.batch "rustfs-bucket-init" deleted from ate-system namespace
Pulled: ghcr.io/kagent-dev/substrate/helm/substrate:1.0.0-rc1
Digest: sha256:b241353b8ed882b848710c56ad4f86102b8d3c957e227df79193d0fb5e97e9d6
substrate revision 2: deployed
deployment "ate-api-server" successfully rolled out
deployment "ate-controller" successfully rolled out
deployment "ate-idp-server" successfully rolled out
deployment "atenet-egress" successfully rolled out
deployment "atenet-router" successfully rolled out
deployment "k8s-credential-provider" successfully rolled out
deployment "rustfs" successfully rolled out
daemon set "atelet" successfully rolled out
job.batch/rustfs-bucket-init condition met
workers registered: 1 of 1
```

The block waits for every rollout in `ate-system` and for the workers to register again before
going on: a turn sent while atelet is still restarting can come back with no task. The values go
through stdin, so they never appear in a command line, but Helm keeps them in its release Secret
and they are in the four pod specs. Anyone who can read those still has them, which is why the
second change matters more.

Prove the defaults no longer work, and that a session suspended under the old credentials is still
readable under the new ones:

```bash run id=step-21-credentials-check
echo "-- default credentials:"
S3_ACCESS_KEY=rustfsadmin S3_SECRET_KEY=rustfsadmin snapshot_ls "kagent/" | tail -2
echo "-- Step 7's session, suspended before the change:"
kagent_send assistant "What exact phrase did I ask you to reply with in my first message? Answer with only that phrase." "$FIRST_CONTEXT"
```

**Verified on 2026-11-02:**

```text
-- default credentials:

An error occurred (InvalidAccessKeyId) when calling the ListObjectsV2 operation: The Access Key Id you provided does not exist in our records.
-- Step 7's session, suspended before the change:
TASK_STATE_COMPLETED	01a19ef2-22b5-726d-a842-058b2f823b3e	up and running
```

**Second, close the exception.** The chart's own policy, `rustfs`, already admits only the
components that read and write snapshots, and the S3 port only, so the RustFS console on port 9001
is unreachable too. Remove the inspection exception Step 12 added, and read back who the chart's
policy admits:

```bash run id=networkpolicy-rustfs
kubectl delete networkpolicy rustfs-snapshot-inspect -n ate-system --ignore-not-found
kubectl get networkpolicy rustfs -n ate-system -o json \
  | jq -r '.spec.ingress[].from[].podSelector | ((.matchLabels // {}) | to_entries[] | "\(.key)=\(.value)"), (.matchExpressions[]? | "\(.key) in (\(.values | join(",")))")'
```

**Verified on 2026-11-02:**

```text
networkpolicy.networking.k8s.io "rustfs-snapshot-inspect" deleted from ate-system namespace
app in (ate-api-server,atelet)
batch.kubernetes.io/job-name=rustfs-bucket-init
```

atelet uploads and downloads snapshots, ate-api lists, copies and deletes them, and the chart's
bucket-init Job creates the bucket on a fresh install. Nothing else needs the store.

Prove it, from a pod in the store's own namespace, with the right credentials:

```bash run id=step-21-fence-check
echo "-- snapshot_ls with the new credentials, from a pod that is not atelet or ate-api:"
snapshot_ls "kagent/" | tail -2
echo "-- a session still resumes:"
kagent_send assistant "Reply with exactly: fenced" "$FIRST_CONTEXT"
```

**Verified on 2026-11-02:**

```text
-- snapshot_ls with the new credentials, from a pod that is not atelet or ate-api:

Connect timeout on endpoint URL: "http://rustfs.ate-system.svc:9000/ate-snapshots?list-type=2&prefix=kagent%2F&encoding-type=url"
-- a session still resumes:
TASK_STATE_COMPLETED	01a19ef2-22b5-726d-a842-058b2f823b3e	fenced
```

Correct credentials, wrong pod, no connection. `snapshot_ls` and `snapshot_cat` no longer work on
this cluster; atelet and ate-api are unaffected.

> [!NOTE]
> **This fences the store; it does not encrypt it.** Substrate sends no server-side encryption parameters, and the bundled store has no encryption setting. On an external store, only the bucket's own default encryption would apply.

### Step 22: Put the UI and API behind single sign-on

*Runtime: kagent.*

Until now the controller has had no sign-in at all: every request was `admin@kagent.dev`, or
whoever its `X-User-Id` header named (Step 7). This step puts oauth2-proxy, which the kagent chart
bundles, in front of the UI, with Keycloak as the identity provider, and switches the controller to
`trusted-proxy` mode, where it takes the user from the token the proxy passes on. Attachment A
explains what is in that token.

**The issuer has to be one URL that both your browser and the cluster can reach.** Keycloak writes
its own URL into every token's `iss` claim, and oauth2-proxy rejects a token whose `iss` differs
from its configured issuer by a single byte. This guide uses `keycloak.localhost`: browsers and curl
resolve any `*.localhost` name to 127.0.0.1 themselves, where a port-forward reaches Keycloak, and
inside the cluster a CoreDNS rewrite sends the same name to the Keycloak Service, which listens on
the same port.

Generate the secrets in your shell. The two users' passwords are saved, readable only by you, to
`KAGENT_UI_CREDENTIALS_FILE`; nothing else writes a secret to a file:

```bash run id=keycloak-secrets
: "${KEYCLOAK_ADMIN_PASSWORD:=$(openssl rand -hex 16)}"; export KEYCLOAK_ADMIN_PASSWORD
: "${KAGENT_OIDC_CLIENT_SECRET:=$(openssl rand -hex 24)}"; export KAGENT_OIDC_CLIENT_SECRET
: "${KAGENT_COOKIE_SECRET:=$(openssl rand -hex 16)}"; export KAGENT_COOKIE_SECRET
# ui_password NAME: keep NAME if it is set, otherwise generate it; save it either way, replacing any older line.
ui_password() {
  local how=reused
  if ! eval "[ -n \"\${$1:-}\" ]"; then eval "export $1=$(openssl rand -hex 12)"; how=generated; fi
  ( umask 077; touch "$KAGENT_UI_CREDENTIALS_FILE"
    { grep -v "^$1=" "$KAGENT_UI_CREDENTIALS_FILE" || true; eval "printf '%s=%s\n' $1 \"\$$1\""; } > "$KAGENT_UI_CREDENTIALS_FILE.new"
    mv "$KAGENT_UI_CREDENTIALS_FILE.new" "$KAGENT_UI_CREDENTIALS_FILE" )
  echo "$1: ${how}, saved"
}
ui_password KAGENT_ALICE_PASSWORD
ui_password KAGENT_BOB_PASSWORD
echo "UI passwords are in the file KAGENT_UI_CREDENTIALS_FILE names (Environment setup)"
```

**Verified on 2026-11-02:**

```text
KAGENT_ALICE_PASSWORD: reused, saved
KAGENT_BOB_PASSWORD: reused, saved
UI passwords are in the file KAGENT_UI_CREDENTIALS_FILE names (Environment setup)
```

Each value already set in your shell is kept, so the block is safe to run again; the output above is from a second run. In a new shell,
`set -a; . ~/.kagent-ui-credentials; set +a` brings the passwords back.

The realm has two clients and two users. `kagent-ui` is oauth2-proxy's client, confidential, with
its secret. `kagent-cli` is for scripts: public, with the password grant, and its tokens carry
`kagent-ui` as their audience so that oauth2-proxy accepts them too. The block refuses to go on if
any value is empty or a placeholder is left:

```bash run id=keycloak-realm
kubectl get namespace keycloak >/dev/null 2>&1 || kubectl create namespace keycloak
REALM_FILE=$(umask 077; mktemp)
cat > "$REALM_FILE" <<EOF
{
  "realm": "kagent",
  "enabled": true,
  "sslRequired": "none",
  "clients": [
    {
      "clientId": "kagent-ui",
      "name": "kagent UI, through oauth2-proxy (confidential)",
      "enabled": true,
      "publicClient": false,
      "secret": "${KAGENT_OIDC_CLIENT_SECRET}",
      "standardFlowEnabled": true,
      "directAccessGrantsEnabled": false,
      "redirectUris": ["http://localhost:${KAGENT_UI_PORT}/oauth2/callback"],
      "webOrigins": ["http://localhost:${KAGENT_UI_PORT}"]
    },
    {
      "clientId": "kagent-cli",
      "name": "kagent scripts (public, password grant; lab only)",
      "enabled": true,
      "publicClient": true,
      "standardFlowEnabled": false,
      "directAccessGrantsEnabled": true,
      "protocolMappers": [
        {
          "name": "kagent-ui audience",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "config": {"included.client.audience": "kagent-ui", "id.token.claim": "true", "access.token.claim": "true"}
        }
      ]
    }
  ],
  "users": [
    {
      "username": "alice", "email": "alice@example.com", "emailVerified": true,
      "firstName": "Alice", "lastName": "User", "enabled": true,
      "credentials": [{"type": "password", "value": "${KAGENT_ALICE_PASSWORD}", "temporary": false}]
    },
    {
      "username": "bob", "email": "bob@example.com", "emailVerified": true,
      "firstName": "Bob", "lastName": "User", "enabled": true,
      "credentials": [{"type": "password", "value": "${KAGENT_BOB_PASSWORD}", "temporary": false}]
    }
  ]
}
EOF
MISSING=""
for v in KAGENT_OIDC_CLIENT_SECRET KAGENT_ALICE_PASSWORD KAGENT_BOB_PASSWORD KEYCLOAK_ADMIN_PASSWORD; do
  eval "[ -n \"\${$v:-}\" ]" || MISSING="$MISSING $v"
done
if [ -n "$MISSING" ] || [ ! -s "$REALM_FILE" ] || grep -q '\${' "$REALM_FILE"; then
  echo "realm not created: empty:${MISSING:- none}; placeholders left: $(grep -c '\${' "$REALM_FILE" || true). Run keycloak-secrets first." >&2
  false
else
  kubectl delete secret keycloak-admin keycloak-realm -n keycloak --ignore-not-found
  printf '%s' "$KEYCLOAK_ADMIN_PASSWORD" | kubectl create secret generic keycloak-admin \
    -n keycloak --from-file=password=/dev/stdin
  kubectl create secret generic keycloak-realm -n keycloak --from-file=realm.json="$REALM_FILE"
  echo "realm: $(jq -r '"\(.clients | length) clients, \(.users | length) users"' "$REALM_FILE"), $(wc -c < "$REALM_FILE" | tr -d ' ') bytes"
fi
rm -f "$REALM_FILE"
```

**Verified on 2026-11-02:**

```text
namespace/keycloak created
secret/keycloak-admin created
secret/keycloak-realm created
realm: 2 clients, 2 users, 1579 bytes
```

Deploy Keycloak, with its hostname set to the issuer's:

```bash run id=keycloak-deployment
kubectl apply -f- <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak
  namespace: keycloak
  labels: {app: keycloak}
spec:
  replicas: 1
  selector:
    matchLabels: {app: keycloak}
  template:
    metadata:
      labels: {app: keycloak}
    spec:
      containers:
        - name: keycloak
          image: ${KEYCLOAK_IMAGE}
          args: ["start-dev", "--import-realm"]
          env:
            - {name: KC_BOOTSTRAP_ADMIN_USERNAME, value: admin}
            - name: KC_BOOTSTRAP_ADMIN_PASSWORD
              valueFrom: {secretKeyRef: {name: keycloak-admin, key: password}}
            - {name: KC_HTTP_ENABLED, value: "true"}
            - {name: KC_HOSTNAME_STRICT, value: "false"}
            - {name: KC_HOSTNAME, value: "http://keycloak.localhost:${KEYCLOAK_PORT}"}
          ports: [{name: http, containerPort: 8080}]
          readinessProbe:
            httpGet: {path: /realms/kagent/.well-known/openid-configuration, port: 8080}
            initialDelaySeconds: 20
            periodSeconds: 5
            failureThreshold: 40
          volumeMounts: [{name: realm, mountPath: /opt/keycloak/data/import, readOnly: true}]
      volumes:
        - name: realm
          secret: {secretName: keycloak-realm}
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak
  namespace: keycloak
  labels: {app: keycloak}
spec:
  selector: {app: keycloak}
  ports: [{name: http, port: ${KEYCLOAK_PORT}, targetPort: 8080}]
EOF
kubectl rollout status deployment/keycloak -n keycloak --timeout=600s
```

**Verified on 2026-11-02:**

```text
deployment.apps/keycloak created
service/keycloak created
deployment "keycloak" successfully rolled out
```

Make `keycloak.localhost` resolve to that Service inside the cluster. This block is safe to re-run:

```bash run id=coredns-keycloak-rewrite
CORE=$(kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}')
if printf '%s' "$CORE" | grep -qF 'rewrite name keycloak.localhost'; then
  echo "rewrite already present"
else
  NEW=$(printf '%s\n' "$CORE" | awk '{print} /^[[:space:]]*ready[[:space:]]*$/ && !done {print "    rewrite name keycloak.localhost keycloak.keycloak.svc.cluster.local"; done=1}')
  kubectl create configmap coredns -n kube-system --from-literal=Corefile="$NEW" --dry-run=client -o yaml \
    | kubectl patch configmap coredns -n kube-system --type merge --patch-file /dev/stdin
fi
```

The change reaches CoreDNS only after the kubelet refreshes the mounted ConfigMap and CoreDNS's
`reload` plugin next checks it, which can take a minute or more. Forward the same port on your
machine, and wait until the issuer is byte-identical from both sides. Do not go on until it is:
oauth2-proxy reads the issuer when it starts, and restarts until it can:

```bash run id=keycloak-forward
pkill -f "kubectl --context kind-${KIND_CLUSTER_NAME} port-forward .*-n keycloak " 2>/dev/null || true
kubectl --context "kind-${KIND_CLUSTER_NAME}" port-forward --address 127.0.0.1 -n keycloak service/keycloak ${KEYCLOAK_PORT}:${KEYCLOAK_PORT} >/dev/null 2>&1 &
PF=$!; sleep 3
kill -0 $PF 2>/dev/null || echo "the port-forward on ${KEYCLOAK_PORT} did not start: something else holds the port" >&2
echo "workstation: $(curl -sS http://keycloak.localhost:${KEYCLOAK_PORT}/realms/kagent/.well-known/openid-configuration | jq -r .issuer)"
CLUSTER_ISSUER=""
for i in $(seq 1 24); do
  CLUSTER_ISSUER=$(kubectl run "issuer-check-${i}" -n default --rm -i --restart=Never --image=busybox:1.36 --quiet -- \
    wget -qO- http://keycloak.localhost:${KEYCLOAK_PORT}/realms/kagent/.well-known/openid-configuration < /dev/null 2>/dev/null \
    | jq -r .issuer 2>/dev/null || true)
  [ -n "$CLUSTER_ISSUER" ] && break
  sleep 5
done
echo "cluster:     ${CLUSTER_ISSUER:-not resolvable from inside the cluster after 24 attempts}"
```

**Verified on 2026-11-02:**

```text
workstation: http://keycloak.localhost:18080/realms/kagent
cluster:     http://keycloak.localhost:18080/realms/kagent
```

Two identical lines. If the cluster side never resolves, check the rewrite with
`kubectl get configmap coredns -n kube-system -o yaml`.

Now turn on oauth2-proxy and `trusted-proxy` mode. `userIdClaim` names the token claim that
identifies the user; `preferred_username` is the Keycloak username. The proxy's arguments are the
chart's defaults with three changes: the scope drops `groups`, which this realm does not define;
cookies are not marked secure, because this lab serves the UI over plain HTTP on localhost; and the
issuer, redirect and client are this realm's:

```bash run id=helm-kagent-sso
: "${KAGENT_OIDC_CLIENT_SECRET:?is empty: run keycloak-secrets first}" "${KAGENT_COOKIE_SECRET:?is empty}" && {
helm upgrade kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version ${KAGENT_VERSION} \
  --namespace kagent --reuse-values --wait -f- >/dev/null <<EOF
config:
  server:
    auth:
      mode: trusted-proxy
      userIdClaim: preferred_username
oauth2-proxy:
  enabled: true
  config:
    clientID: kagent-ui
    clientSecret: "${KAGENT_OIDC_CLIENT_SECRET}"
    cookieSecret: "${KAGENT_COOKIE_SECRET}"
  extraEnv:
    - name: KAGENT_OAUTH2_PROXY_SIGNIN_TEMPLATE_CHECKSUM
      value: '{{ include "kagent.oauth2ProxySignInHTML" . | sha256sum }}'
    - name: OIDC_ISSUER_URL
      value: http://keycloak.localhost:${KEYCLOAK_PORT}/realms/kagent
    - name: OIDC_REDIRECT_URL
      value: http://localhost:${KAGENT_UI_PORT}/oauth2/callback
    - name: UPSTREAM_URL
      value: http://kagent-ui:8080
  extraArgs:
    provider: oidc
    oidc-issuer-url: "\$(OIDC_ISSUER_URL)"
    redirect-url: "\$(OIDC_REDIRECT_URL)"
    upstream: "\$(UPSTREAM_URL)"
    email-domain: "*"
    pass-authorization-header: true
    set-authorization-header: true
    approval-prompt: "auto"
    scope: "openid profile email"
    cookie-secure: false
    cookie-samesite: "lax"
    skip-jwt-bearer-tokens: true
    skip-auth-route: "^/(health|login)$"
    skip-auth-regex: "^/(login|assets/|env-config\\\\.js|logo-.*\\\\.png|favicon\\\\.ico).*$"
    custom-templates-dir: "/templates"
EOF
helm history kagent -n kagent -o json | jq -r '.[-1] | "kagent revision \(.revision): \(.status)"'
kubectl rollout status deployment/kagent-oauth2-proxy -n kagent --timeout=180s
}
```

**Verified on 2026-11-02:**

```text
Pulled: ghcr.io/kagent-dev/kagent/helm/kagent:1.0.0-rc1
Digest: sha256:372385ceb67ea2281645ca3755c27861f8b5dc0b60c8149995e658d46f5737e8
kagent revision 3: deployed
deployment "kagent-oauth2-proxy" successfully rolled out
```

`extraEnv` and `extraArgs` are given whole, because Helm replaces a list rather than merging it.

The UI's port-forward now goes to oauth2-proxy rather than to the UI. Redefine `forwards`, and give
scripts a token: `kagent_token <user>` signs in through `kagent-cli`, and `kagent_send` now sends
the token of the user in `KAGENT_AS`, `alice` unless you say otherwise:

```bash run id=step-22-helpers
forwards() {
  pkill -f "kubectl --context kind-${KIND_CLUSTER_NAME} port-forward .*-n kagent " 2>/dev/null || true
  kubectl --context "kind-${KIND_CLUSTER_NAME}" rollout status deployment/kagent-controller -n kagent --timeout=120s
  kubectl --context "kind-${KIND_CLUSTER_NAME}" rollout status deployment/kagent-oauth2-proxy -n kagent --timeout=120s
  kubectl --context "kind-${KIND_CLUSTER_NAME}" port-forward --address 127.0.0.1 -n kagent deployment/kagent-controller ${KAGENT_API_PORT}:8083 >/dev/null 2>&1 &
  local api=$!
  kubectl --context "kind-${KIND_CLUSTER_NAME}" port-forward --address 127.0.0.1 -n kagent service/kagent-oauth2-proxy ${KAGENT_UI_PORT}:4180 >/dev/null 2>&1 &
  local ui=$!
  sleep 2
  if ! kill -0 $api 2>/dev/null || ! kill -0 $ui 2>/dev/null; then
    echo "forwards: port ${KAGENT_API_PORT} or ${KAGENT_UI_PORT} is held by something else, often a forward to another cluster; set KAGENT_API_PORT and KAGENT_UI_PORT to free ports" >&2
    return 1
  fi
  echo "forwards up: controller ${KAGENT_API_PORT}, ui (through oauth2-proxy) ${KAGENT_UI_PORT}"
}

# kagent_token <user>: an access token for alice or bob, from Keycloak's kagent-cli client.
kagent_token() {
  local pw
  case "$1" in alice) pw=${KAGENT_ALICE_PASSWORD:-} ;; bob) pw=${KAGENT_BOB_PASSWORD:-} ;; *) pw="" ;; esac
  [ -n "$pw" ] || { echo "kagent_token: no password for '$1' in this shell" >&2; return 1; }
  curl -sS --max-time 10 "http://keycloak.localhost:${KEYCLOAK_PORT}/realms/kagent/protocol/openid-connect/token" \
    -d grant_type=password -d client_id=kagent-cli -d "username=$1" --data-urlencode "password=${pw}" -d scope=openid \
    | jq -er '.access_token // empty' || { echo "kagent_token: Keycloak issued no token for '$1'" >&2; return 1; }
}

# kagent_send <agent> <message> [contextId], as the user in KAGENT_AS (default alice).
kagent_send() {
  local ctx="" token
  [ -n "${3:-}" ] && ctx="\"contextId\":\"$3\","
  token=$(kagent_token "${KAGENT_AS:-alice}") || return 1
  curl -sS --max-time 300 -X POST "http://127.0.0.1:${KAGENT_API_PORT}/agents/kagent/$1" \
    -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"SendMessage\",\"params\":{\"message\":{\"messageId\":\"$(openssl rand -hex 8)\",${ctx}\"role\":\"ROLE_USER\",\"parts\":[{\"text\":\"$2\"}]}}}" \
    | jq -rR 'fromjson? // {"error": {"message": .}}
             | if .result.task then (.result.task | "\(.status.state)\t\(.contextId)\t\(((.artifacts[-1].parts // .status.message.parts // []) | map(.text // "") | join("")) | gsub("\\s*\n\\s*"; " "))")
               else "NO_TASK\t\t\(.error.message // ("no task in the response: " + (tostring | .[0:200])))" end'
}
```

Bring the forwards back and check both doors:

```bash run id=step-22-check
forwards
echo -n "UI with no sign-in:            "; curl -sS -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:${KAGENT_UI_PORT}/"
echo -n "API with no token:             "; curl -sS -o /dev/null -w '%{http_code}\n' -X POST "http://127.0.0.1:${KAGENT_API_PORT}/agents/kagent/assistant" -H 'Content-Type: application/json' -d '{}'
echo -n "API with only an X-User-Id:    "; curl -sS -o /dev/null -w '%{http_code}\n' -X POST "http://127.0.0.1:${KAGENT_API_PORT}/agents/kagent/assistant" -H 'X-User-Id: alice' -H 'Content-Type: application/json' -d '{}'
echo -n "alice:                         "; kagent_send assistant "Reply with exactly: signed in" | cut -f1,3
```

**Verified on 2026-11-02:**

```text
deployment "kagent-controller" successfully rolled out
deployment "kagent-oauth2-proxy" successfully rolled out
forwards up: controller 18083, ui (through oauth2-proxy) 14000
UI with no sign-in:            403
API with no token:             401
API with only an X-User-Id:    401
alice:                         TASK_STATE_COMPLETED	signed in
```

A request with no token is refused at both doors, and the `X-User-Id` header that named the user
until now is no longer enough. Alice's Keycloak token works.

**In the browser.** Open `http://localhost:14000` (or your `KAGENT_UI_PORT`). oauth2-proxy sends you
to Keycloak's sign-in page; sign in as `alice` with `KAGENT_ALICE_PASSWORD` from your credentials
file, and the UI says *Signed in as alice*. Its dashboard lists every agent, and only Alice's own
sessions (verified on this release after Step 23, which lists two). Sessions from before this
step, and Bob's, are not among them.

> [!NOTE]
> **The `kagent` CLI stops working here.** It sends a user ID, not a token, so it is refused by `trusted-proxy` mode. Use the UI, or a token as `kagent_send` does.

Sessions started before this step belong to `admin@kagent.dev`, the user every request was until
now. No one who signs in is that user, so those sessions are no longer reachable through the API or
the UI; they still exist in Substrate and the database until you delete them.

### Step 23: Sessions belong to the user who started them

*Runtime: kagent.*

There are no roles in kagent's open-source release: every signed-in user can see, change and
delete every agent, and talk to any of them. What kagent does enforce is that a session belongs to
the user who started it. Alice starts one, Bob tries to continue it:

```bash run id=step-23-ownership
TURN=$(KAGENT_AS=alice kagent_send assistant "My favourite colour is teal. Reply with OK.")
echo "alice starts:      $(echo "$TURN" | cut -f1,3)"
export ALICE_CONTEXT=$(echo "$TURN" | cut -f2)
echo "bob continues it:  $(KAGENT_AS=bob kagent_send assistant "What is my favourite colour? One word." "$ALICE_CONTEXT" | cut -f1,3)"
echo "alice continues:   $(KAGENT_AS=alice kagent_send assistant "What is my favourite colour? One word." "$ALICE_CONTEXT" | cut -f1,3)"
```

**Verified on 2026-11-02:**

```text
alice starts:      TASK_STATE_COMPLETED	OK.
bob continues it:  NO_TASK	task not found
alice continues:   TASK_STATE_COMPLETED	Teal.
```

Bob's request is refused as if the session did not exist. kagent records the owner as the issuer
and the `preferred_username` claim together, so the same name from another issuer is someone else.

**That holds only for requests that came through oauth2-proxy.** In `trusted-proxy` mode the
controller trusts the proxy to have checked the token: it reads the user from the token's claims
without checking its signature. oauth2-proxy checks it, but anything that can reach the controller
without going through the proxy can claim to be anyone. Prove it from an ordinary pod in another
namespace, with a token that has Alice's claims and a signature that is not one:

```bash run id=step-23-forged-open
FORGED="$(kagent_token alice | cut -d. -f1,2).not-a-signature"
FORGE_BODY="{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"SendMessage\",\"params\":{\"message\":{\"messageId\":\"$(openssl rand -hex 8)\",\"contextId\":\"${ALICE_CONTEXT}\",\"role\":\"ROLE_USER\",\"parts\":[{\"text\":\"What is my favourite colour? One word.\"}]}}}"
# forge_check: a pod in default sends the forged token straight to the controller, and to the UI behind the proxy.
forge_check() {
  kubectl run forge-check -n default --rm -i --restart=Never --image=busybox:1.36 --quiet \
    --env="T=${FORGED}" --env="B=${FORGE_BODY}" -- sh -c '
      C=$(wget -T 8 -q -O - --header "Authorization: Bearer $T" --header "Content-Type: application/json" --post-data "$B" \
        http://kagent-controller.kagent.svc.cluster.local:8083/agents/kagent/assistant 2>&1)
      echo "controller: $(echo "$C" | grep -oE "TASK_STATE_[A-Z]+|download timed out" | head -1)"
      U=$(wget -T 8 -q -O /dev/null http://kagent-ui.kagent.svc.cluster.local:8080/ 2>&1)
      echo "ui:         ${U:-answered}"' < /dev/null
}
forge_check
```

**Verified on 2026-11-02:**

```text
controller: TASK_STATE_COMPLETED
ui:         answered
```

The controller ran the forged request in Alice's session. Close the gap: admit to the UI only
oauth2-proxy, and to the controller only the UI and Substrate's egress gateway, which carries every
call an agent makes back to kagent (Step 7):

```bash run id=networkpolicy-kagent-sign-in
kubectl apply -f- <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: kagent-ui-from-oauth2-proxy
  namespace: kagent
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: kagent
      app.kubernetes.io/component: ui
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: oauth2-proxy
      ports:
        - protocol: TCP
          port: 8080
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: kagent-controller-from-ui-and-agents
  namespace: kagent
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: kagent
      app.kubernetes.io/component: controller
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: kagent
              app.kubernetes.io/component: ui
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ate-system
          podSelector:
            matchLabels:
              app: atenet-egress
      ports:
        - protocol: TCP
          port: 8083
EOF
sleep 5
```

Then run the same forgery again, and check that the doors that should work still do:

```bash run id=step-23-forged-closed
forge_check
echo -n "forged token through oauth2-proxy: "; curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST "http://127.0.0.1:${KAGENT_UI_PORT}/api/kagent.api.v1.SessionService/ListSessions" \
  -H "Authorization: Bearer ${FORGED}" -H 'Content-Type: application/grpc-web+proto' -H 'X-Grpc-Web: 1' --data-binary @/dev/null
echo -n "alice through oauth2-proxy:        "; curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST "http://127.0.0.1:${KAGENT_UI_PORT}/api/kagent.api.v1.SessionService/ListSessions" \
  -H "Authorization: Bearer $(kagent_token alice)" -H 'Content-Type: application/grpc-web+proto' -H 'X-Grpc-Web: 1' --data-binary @/dev/null
echo "alice's agent turn:                $(kagent_send assistant "Reply with exactly: still works" "$ALICE_CONTEXT" | cut -f1,3)"
```

**Verified on 2026-11-02:**

```text
controller: download timed out
ui:         wget: download timed out
forged token through oauth2-proxy: 403
alice through oauth2-proxy:        200
alice's agent turn:                TASK_STATE_COMPLETED	still works
```

The pod can no longer reach the controller or the UI. oauth2-proxy refuses the forged token, because
it does check signatures, and accepts Alice's real one. Agent turns are unaffected: they reach the
controller through Substrate's egress gateway, which the policy admits.

> [!WARNING]
> **Anyone allowed to port-forward to the controller can be any user.** `kubectl port-forward` does not pass through NetworkPolicies, and this guide's scripts use one: they are trusted because they send real tokens, not because the controller checks them. Treat `pods/portforward` and `pods/exec` in the `kagent` namespace as the right to act as every user, and grant them only to the platform team. The NetworkPolicies also admit by label: a pod someone can create in `kagent` with the UI's labels reaches the controller.

**What you now have:** every outbound credential held outside the agent, in Substrate's egress
gateway and agentgateway; each team's tokens capped and attributed at the gateway; the snapshot
store on its own credentials and reachable only by atelet and ate-api; and every caller of the UI
and API signed in through Keycloak, with sessions that belong to the user who started them and no
path around the sign-in from inside the cluster. What open-source kagent does not have is roles:
every signed-in user can change every agent.

## SCALE

Isolate made each session its own sandbox. This section is about what that costs: what a session
holds while it waits, what it holds while it runs, and how to size the worker pool from that.

**What you are going to do:** measure Substrate's own footprint and compare it with running agents
as pods, watch memory follow turns in flight, measure what suspended sessions leave behind, size
the worker pool, and change it safely.

**What you will have that you did not have before:** numbers to size a platform with, the method to
re-measure them on your own nodes, and a way to change the worker pool without losing capacity.

The steps you run here make five model calls in total and finish in a few minutes. Figures not
measured by a block in this section are labelled as reference measurements, with where they come
from. From here on, every turn is alice's: sessions started before Step 22 belong to the built-in
user the API assumed with no sign-in, and are out of reach now that every caller signs in.

**Verified on:** a kind cluster created fresh for it (kind 0.32.0, `kindest/node:v1.35.0`), 2026-11-03 06:51 UTC: every block from the top of this guide to the end of Step 28 executed verbatim in one pass of `auto-build/run.sh` under bash, from an empty directory outside the repository. Every output block in Scale comes from that run.

### Step 24: A pod per session, a pod per agent, or Substrate

*No runtime: one reading from your cluster, and reference measurements.*

There are two ways to run agents as ordinary pods, and they are not the same comparison:

- **A pod per agent**: one long-lived process serves every session with that agent. It is the
  cheapest thing you can run. **It uses less memory than Substrate at every load measured**, because
  it adds almost nothing per session. What it gives up is everything Isolate built: all of an
  agent's sessions, for every user, share one process, one memory space and one kernel, and the
  process holds the model key in its environment.
- **A pod per session**: the plain-Kubernetes way to get Substrate's isolation, one sandboxed pod
  for each open session. It holds its memory whether the session is active or waiting for someone
  to reply.

Substrate is the second model without its standing memory: a session holds memory only while a
turn is running. While it waits, it is a snapshot in the store and rows in PostgreSQL, tens of
kilobytes in all (Step 26), instead of tens of megabytes of memory.

What Substrate adds is fixed: its control plane. Read it from the node, as the working set of every
container in `ate-system` and the pod certificate controller's namespace. That includes RustFS, the
bundled snapshot store, which a production install replaces with an external object store:

```bash run id=step-24-control-plane
docker exec "${KIND_CLUSTER_NAME}-control-plane" crictl stats -o json \
  | jq -r '[.stats[] | select(.attributes.labels["io.kubernetes.pod.namespace"] | IN("ate-system", "podcertificate-controller-system"))
            | {c: .attributes.labels["io.kubernetes.container.name"], m: ((.memory.workingSetBytes.value // "0") | tonumber)}]
           | (group_by(.c) | map("  \(.[0].c): \(map(.m) | add / 1048576 | floor) MiB") | .[]),
             "Substrate control plane, in all: \(map(.m) | add / 1048576 | floor) MiB"'
```

**Verified on 2026-11-03:**

```text
  agentgateway: 31 MiB
  ate-api-server: 58 MiB
  ate-controller: 15 MiB
  ate-idp-server: 15 MiB
  atelet: 16 MiB
  controller: 8 MiB
  create-bucket: 0 MiB
  k8s-credential-provider: 7 MiB
  objectstore-plugin: 44 MiB
  rustfs: 91 MiB
Substrate control plane, in all: 290 MiB
```

`agentgateway` is the two atenet pods, the egress gateway and the router, and `objectstore-plugin`
is the sidecar that gives atelet and ate-api their access to the store.

**Reference measurements** for the pod side. These were measured on 2026-10-14 for this guide's
companion, the Solo Enterprise for kagent guide, on the same reference environment: one kind node,
Kubernetes 1.35.0, Colima on Apple silicon with 6 vCPU and 12 GiB. They are the same kagent Go ADK
runtime image, run as ordinary pods, so they do not depend on which distribution of kagent runs
the platform; they were not repeated for this guide. Memory is the node's used memory before and
after (from `/proc/meminfo`), divided by the number of units. The pod-per-session comparator is one
pod of the kagent runtime, run eight times under gVisor through a RuntimeClass with the same gVisor
build Substrate uses, on a separate throwaway kind cluster, because Substrate's nodes do not
register gVisor as a container runtime. The same pod under runc is included as the comparator most
favourable to pods.

| | Memory per unit | Storage per unit | Unit | Measured |
|---|---|---|---|---|
| Substrate control plane | 290 MiB | | Fixed, once per cluster | Above |
| Substrate, a turn in flight | about 37 MiB | | Per running turn, released when it ends | Step 25 |
| Substrate, a suspended session | 0 | about 56 KiB | Per session, while it waits | Step 26 |
| Substrate, an agent | | about 48 KiB | Golden snapshot, per agent revision | Step 26 |
| Pod per session, gVisor | 59.7 MiB | | Per open session, active or not | Reference |
| Pod per session, runc (weaker isolation) | 13.4 MiB (PSS) | | Per open session, active or not | Reference |
| Pod per agent, runc | 13.4 MiB (PSS) | | Per agent | Reference |

The runc figure is the pod's proportional share of memory (PSS), not the node's: at about 13 MiB a
pod, the node's own figure moved by more than that between samples. None of the pod figures
include a turn's own cost, which can only add to the pod side. The kagent platform itself
(controller, UI, PostgreSQL) is needed either way and is left out of every figure.

**Projected from those constants**, at 100 open sessions with one agent:

| Turns in flight | Substrate memory | Substrate storage | Pod per session, gVisor | Pod per session, runc | Pod per agent |
|---|---|---|---|---|---|
| 5 | 0.48 GiB | about 6 MB | 5.8 GiB, plus turn cost | 1.3 GiB, plus turn cost | 13 to 60 MiB, plus turn cost |
| 10 | 0.66 GiB | about 6 MB | 5.8 GiB, plus turn cost | 1.3 GiB, plus turn cost | 13 to 60 MiB, plus turn cost |
| 25 | 1.21 GiB | about 6 MB | 5.8 GiB, plus turn cost | 1.3 GiB, plus turn cost | 13 to 60 MiB, plus turn cost |

Substrate memory is the control plane, one idle worker at about 20 MiB, and 37 MiB per
turn in flight. Its storage is 100 suspended sessions in the store plus one golden snapshot; the
pods need none of that, and hold their memory instead.

Against equal isolation, Substrate uses less memory at every load, and the gap grows with every
session held open, because a waiting session costs kilobytes of storage rather than tens of
megabytes of memory. Against a pod per agent, Substrate always costs more, and buys isolation per
session with it.

### Step 25: Memory follows turns in flight

*Runtime: kagent.*

Measure it on your own cluster. These helpers read the worker pods' memory from the node (the
working set of each worker's container, through `crictl`) and count the actors running right now:

```bash run id=step-25-helpers
# worker_total: summed working set of the worker containers, in MiB.
worker_total() {
  docker exec "${KIND_CLUSTER_NAME}-control-plane" crictl stats -o json --label io.kubernetes.container.name=worker \
    | jq '[.stats[].memory.workingSetBytes.value | tonumber] | add / 1048576 | floor'
}

# running_actors: actors in the kagent atespace that are running now.
running_actors() {
  kubectl-ate get actors -a kagent -o json 2>/dev/null | jq '[.actors[] | select(.status.state == "ACTOR_STATE_RUNNING")] | length'
}
```

Start four sessions at once as alice, each asking for a long enough answer to stay in flight for a
few seconds, and sample while they run. The loop watches the four turns by process ID, kept in an
array so bash and zsh read it the same way: the port-forwards `forwards` started are background jobs
in the same shell, so waiting for "no background jobs" would never end. In an interactive zsh, lines
such as `[2] 48213` and `[2]  + done` are its job control reporting the four turns, not errors:

```bash run id=step-25-burst
BEFORE=$(worker_total); PEAK=$BEFORE; PEAK_RUNNING=0; TURNS=()
for i in 1 2 3 4; do
  kagent_send assistant "Write about 250 words on the history of the bicycle." >/dev/null &
  TURNS+=($!)
done
in_flight() { for p in "${TURNS[@]}"; do kill -0 "$p" 2>/dev/null && return 0; done; return 1; }
while in_flight; do
  M=$(worker_total); R=$(running_actors)
  [ "$M" -gt "$PEAK" ] && PEAK=$M
  [ "$R" -gt "$PEAK_RUNNING" ] && PEAK_RUNNING=$R
done
wait "${TURNS[@]}"
sleep 5
AFTER=$(worker_total)
echo "worker memory before:  ${BEFORE} MiB, 0 turns in flight"
echo "worker memory peak:    ${PEAK} MiB, up to ${PEAK_RUNNING} turns in flight"
echo "worker memory after:   ${AFTER} MiB, running actors now: $(running_actors)"
```

**Verified on 2026-11-03:**

```text
worker memory before:  18 MiB, 0 turns in flight
worker memory peak:    166 MiB, up to 4 turns in flight
worker memory after:   20 MiB, running actors now: 0
```

Memory rises with the turns in flight, about 37 MiB for each, and falls back to where it started
when they end. The four sessions are still there, suspended, and hold no memory now. A sample can
miss the very top of the curve, so treat the peak as a lower bound. The companion guide measured
about 50 MiB per running actor on an earlier release, by holding actors running deliberately; the
figure here comes from four short turns.

Those turns also used CPU, which is what actually limits a node. In a reference run for the
companion guide (2026-10-11, an earlier release of the same Substrate source, not repeated here),
with real model calls on one worker, the platform's own share of a turn's latency stayed under
twice its single-turn value up to **4 concurrent turns per 6-vCPU node**, and doubled beyond that.
Model time stayed flat; the growth was restore time, consistent with CPU contention (CPU was not
sampled directly). No turn failed at any concurrency up to 32.

### Step 26: What suspended sessions leave behind

*Runtime: kagent. No model calls.*

A suspended session holds no memory, but it is not free. It leaves data in two places, the
snapshot store and PostgreSQL. Read both, and what the node keeps:

```bash run id=step-26-storage
echo "sessions in Substrate: $(kubectl-ate get actors -a kagent -o json 2>/dev/null | jq '.actors | length')"
PV_PATH=$(kubectl get pv "$(kubectl get pvc rustfs-data -n ate-system -o jsonpath='{.spec.volumeName}')" \
  -o jsonpath='{.spec.hostPath.path}{.spec.local.path}')
on_node() { docker exec "${KIND_CLUSTER_NAME}-control-plane" sh -c "$1"; }
echo "-- snapshot store, on disk: $(on_node "du -sh $PV_PATH" | cut -f1) of $(kubectl get pvc rustfs-data -n ate-system -o jsonpath='{.spec.resources.requests.storage}')"
echo "-- per session in the store, on disk (sessions, KiB each):"
on_node "du -sk $PV_PATH/ate-snapshots/kagent/atespaces/kagent/actors/*" | awk '{print $1}' | sort -n | uniq -c
echo "-- per agent revision in the store, golden snapshots (snapshots, KiB each):"
on_node "du -sk $PV_PATH/ate-snapshots/kagent/atespaces/kagent/tags/*" | awk '{print $1}' | sort -n | uniq -c
echo "-- PostgreSQL, by schema:"
kubectl exec -n kagent deployment/kagent-postgresql -c postgresql -- psql -U postgres -d kagent -At -c \
  "select schemaname || ': ' || pg_size_pretty(sum(pg_total_relation_size(relid))) from pg_statio_user_tables where schemaname in ('kagent','substrate') group by schemaname order by 1"
echo "-- on the node: actor directories $(on_node 'ls /var/lib/ate/actors | wc -l'), snapshot cache $(on_node 'du -sh /var/lib/ate/snapshot-cache' | cut -f1) ($(on_node 'cat /var/lib/ate/snapshot-cache/entries/*/meta.json' | jq -rs '[.[].key | capture("/atespaces/[^/]+/(?<k>actors|tags)/").k] | group_by(.) | map("\(length) \(.[0]) entries") | join(", ")'))"
```

**Verified on 2026-11-03:**

```text
sessions in Substrate: 16
-- snapshot store, on disk: 1.8M of 1Gi
-- per session in the store, on disk (sessions, KiB each):
  13 56
   1 104
   2 128
-- per agent revision in the store, golden snapshots (snapshots, KiB each):
   4 48
-- PostgreSQL, by schema:
kagent: 2840 kB
substrate: 1024 kB
-- on the node: actor directories 16, snapshot cache 344K (20 tags entries)
```

Each session's snapshot takes 56 KiB of the store's volume after a short turn or two, and more as
the session's database grows: the larger entries are the sessions with the most turns. The objects
themselves are a few KB compressed (Step 12); the rest is RustFS's directories and metadata. Each
agent revision adds a 48 KiB golden snapshot, and this cluster has four: two revisions of
`assistant` (Step 10), and one each of the other two agents.

PostgreSQL is the larger part. Its figures include each table's fixed size, so one sample does not
give a share per session. A reference measurement for the companion guide, on an earlier release of
the same kagent source, put kagent's tables at about 14 KB per session after one short turn, 27 KB
after five and 131 KB after twenty, and Substrate's at 3 to 44 KB.

The node keeps a suspended session's directory, about 48 KiB of its own files, for 30 minutes, so
that a session resumed soon after restores from the node rather than the store: the `local/volumes`
restores in Step 9. After that atelet removes it, and the session lives only in the store and
PostgreSQL; deleting a session removes it at once (Step 13). The snapshot cache holds the agents'
golden snapshots only. Neither it nor the sandbox asset cache beside it has a size limit by
default, which atelet logs as a warning when it starts.

| Where | What | Grows |
|---|---|---|
| Snapshot store | The session's volumes, compressed | One snapshot per session, replaced at each turn |
| PostgreSQL | Every task event of every session (`kagent`), and Substrate's actor records (`substrate`) | With every turn; the largest part of a long session |
| The worker node | Golden snapshots of the agents it has started, and container images | Per agent revision, not per session |

**Projected from those measured constants:** 1,000 suspended sessions of short turns need about 56 MB in the store
and, from the reference figures, 17 to 175 MB in PostgreSQL; 10,000 need ten times that, and no
memory at all until someone sends a message. PostgreSQL grows fastest with turns. `kagent install`
gives PostgreSQL a 500Mi volume and the store 1Gi: size both from this.

### Step 27: Size the worker pool for concurrent turns

*No runtime: arithmetic, with one reading from your cluster.*

What a worker can run at once is set by the CPU it advertises, not by how many sessions exist.
Read what your node and workers offer:

```bash run id=step-27-capacity
echo "node CPU: $(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.cpu}'), memory: $(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.memory}')"
kubectl-ate get workers 2>/dev/null
```

**Verified on 2026-11-03:**

```text
node CPU: 6, memory: 12220308Ki
NAME                                   POOL             STATE                 ACTORS   CPU(MILLICORES)   MEMORY         POD                                      AGE
5ddaab8e-6e57-4cbc-afae-21cb4cc5b72d   kagent-default   WORKER_STATE_ACTIVE   0/1000   0/6000            0/12154772Ki   kagent/kagent-default-5ffdf9d9f7-pfqg8   7m38s
```

Each worker advertises the whole node, and every running actor reserves 1 CPU and 1 GiB of it,
released when the actor suspends. A worker on this 6-vCPU node therefore runs at most 6 actors at
once. A turn that finds no room is held for about 5 seconds while Substrate retries placing it, and
is refused if nothing frees up in that time: the caller gets `input was not accepted; retry after
the session becomes available`. With one worker on this node, 12 turns sent together on 2026-11-03
ran at most 6 at once; 10 completed in 3 to 10 seconds, some of them after waiting for a slot, and
2 were refused after 5 seconds.

The limit is per worker, not per node: two workers on one node each advertise all of it, so
together they admit twice what its CPU can serve. Size for turns in flight:

| Input | Reference value |
|---|---|
| Peak turns in flight | Yours to estimate: sessions active at the busiest moment, not sessions open |
| Turns per worker | The CPUs its node advertises: 6 on a 6-vCPU node, measured above |
| Turns per node before latency doubles | 4 per 6-vCPU node, a reference measurement for the companion guide on an earlier release, before actors reserved CPU; not re-measured |
| Memory per turn in flight | about 37 MiB used (Step 25), 1 GiB reserved from the worker's advertised memory |
| Fixed control plane | 290 MiB per cluster (Step 24) |

For 10 turns in flight at the busiest moment: one worker per node, and 10 ÷ 6 rounds up to 2 nodes
of this size before any turn is refused, or 10 ÷ 4 to 3 nodes if you size for the latency figure.
Memory is about 0.28 GiB for the control plane plus 0.36 GiB used by the turns, though each worker sets aside 1 GiB per running actor. Storage is sized
from sessions kept, not turns: see Step 26. **Projected, not measured:** that adding nodes adds
turns at that rate.

Two settings change how the pool behaves under that load. Both are kagent chart values, under
`substrateWorkerPool`:

- **Worker resources** (`substrateWorkerPool.template.resources`). Requests are the only way to
  reserve node capacity for workers; without them, as `kagent install` leaves it, nothing reserves
  room for running actors. Limits become a hard ceiling for everything the worker runs: at about
  37 MiB per running actor, a 2 GiB limit would be reached near 55 running actors
  (**projected**; what happens at the ceiling was not tested).
- **Replicas** (`substrateWorkerPool.replicas`). At least one worker per node you want to use, and
  enough that a worker restarting does not take your only capacity with it. More than one worker
  per node raises the admission limit past what the node's CPU can serve.

### Step 28: Change the worker pool without losing capacity

*Runtime: kagent. One model call.*

The worker Deployment that Substrate creates for a pool rolls out with `maxSurge: 0`: it removes an
old worker before starting its replacement. So any change that makes workers roll, such as new
resources, takes capacity away first, and if the replacement cannot schedule (a request larger than
the node has free), the pool stays a worker short. The WorkerPool's status does not say why; only
the Pending pod's event does.

Check the node's headroom before changing worker resources:

```bash run id=step-28-headroom
kubectl describe node | sed -n '/Allocated resources/,/Events/p' | sed '$d'
```

**Verified on 2026-11-03:**

```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests      Limits
  --------           --------      ------
  cpu                2280m (38%)   17600m (293%)
  memory             3010Mi (25%)  10118Mi (84%)
  ephemeral-storage  0 (0%)        0 (0%)
  hugepages-1Gi      0 (0%)        0 (0%)
  hugepages-2Mi      0 (0%)        0 (0%)
  hugepages-32Mi     0 (0%)        0 (0%)
  hugepages-64Ki     0 (0%)        0 (0%)
```

A worker's new request must fit inside what is not yet requested, or the rollout leaves a worker
Pending. On the node above, 2280m of the 6 CPUs is requested, so about 3.7 CPUs are free.
`kagent install` sets no requests on the worker, so this is the room a first request has: one
larger than that would leave the replacement Pending, with one worker already gone.

The change that never removes capacity is adding a worker. Raise the replica count, wait for the new
worker, and check it serves:

```bash run id=workerpool-scale-up
helm upgrade kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version ${KAGENT_VERSION} \
  --namespace kagent --reuse-values --set substrateWorkerPool.replicas=2 --wait --timeout 10m >/dev/null
for i in $(seq 1 60); do
  [ "$(kubectl get workerpool kagent-default -n kagent -o jsonpath='{.status.readyReplicas}')" = 2 ] && break
  sleep 2
done
kubectl get workerpool -n kagent
kubectl-ate get workers 2>/dev/null
forwards
kagent_send assistant "Reply with exactly: two workers"
```

**Verified on 2026-11-03:**

```text
NAME             DESIRED   REPLICAS   READY   SANDBOXCONFIG    RESOLVED   AGE
kagent-default   2         2          2       gvisor-default   True       7m51s
NAME                                   POOL             STATE                 ACTORS   CPU(MILLICORES)   MEMORY         POD                                      AGE
5ddaab8e-6e57-4cbc-afae-21cb4cc5b72d   kagent-default   WORKER_STATE_ACTIVE   0/1000   0/6000            0/12154772Ki   kagent/kagent-default-5ffdf9d9f7-pfqg8   7m42s
b2f60400-3953-4984-9378-fa9d9357f0c3   kagent-default   WORKER_STATE_ACTIVE   0/1000   0/6000            0/12154772Ki   kagent/kagent-default-5ffdf9d9f7-rbq7n   2s
deployment "kagent-controller" successfully rolled out
deployment "kagent-oauth2-proxy" successfully rolled out
forwards up: controller 18083, ui (through oauth2-proxy) 14000
TASK_STATE_COMPLETED	01a1a036-d7e7-798c-ab99-a84a8de94ad2	two workers
```

`--reuse-values` keeps everything earlier steps set, including the sign-in and the telemetry
endpoints; without it, Helm would reset them to the chart's defaults, and `kagent install` would
too, so make later changes with `helm upgrade` rather than by running the install again. Make
resource changes the same way: add a worker first, so the rollout's missing worker is one you can
spare, then change the resources, then remove the extra worker. Two workers on this one node now
admit 12 turns at once on 6 CPUs, which is the over-admission Step 27 describes: on a real cluster,
add the worker with a node to run it.

**What you now have:** a measured picture of what sessions cost on this platform: memory per turn
in flight, storage per session waiting, a CPU limit per worker, and a pool you can grow without a
gap in service.

## VALIDATE

### Step 29: Drive one session through every control

*Not yet written.*

### Step 30: Map each result to the step that set it

*Not yet written.*

### What you built

*Not yet written.*

## Before production

*Not yet written.*
