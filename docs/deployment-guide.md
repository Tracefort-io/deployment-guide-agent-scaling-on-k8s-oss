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

# Where the kagent CLI and kubectl-ate are installed in Step 3. Must be on your PATH.
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
ate-api-server-74f8b87bf9-5ndxb           2/2     Running     0          98s
ate-api-server-74f8b87bf9-xqbgk           2/2     Running     0          98s
ate-controller-c994f4b54-qjkmq            1/1     Running     0          98s
ate-idp-server-85748d8dd6-2w5zr           1/1     Running     0          98s
ate-idp-server-85748d8dd6-rk4z2           1/1     Running     0          98s
atelet-xjhg7                              2/2     Running     0          98s
atenet-egress-654675ddfb-cm4z6            1/1     Running     0          98s
atenet-router-57d98fd5bf-t797b            1/1     Running     0          98s
k8s-credential-provider-fdbb6987b-4qxlm   1/1     Running     0          98s
rustfs-5d695f5fd-2zd86                    1/1     Running     0          98s
rustfs-bucket-init-g8cf4                  0/1     Completed   0          98s
NAME                                 READY   STATUS    RESTARTS   AGE
kagent-controller-875d9c8-lvc2v      1/1     Running   0          45s
kagent-default-5ffdf9d9f7-42gfj      1/1     Running   0          45s
kagent-postgresql-8466db8889-gzdgc   2/2     Running   0          2m6s
kagent-tools-787df48f55-psd9l        1/1     Running   0          45s
kagent-ui-6f5995c9b9-5nff6           1/1     Running   0          45s
NAME             DESIRED   REPLICAS   READY   SANDBOXCONFIG    RESOLVED   AGE
kagent-default   1         1          1       gvisor-default   True       45s
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
TASK_STATE_COMPLETED	01a19e63-dc01-7b0b-aa76-ca58c7aa1532	up and running
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
atenet-egress: 10.244.0.18
route=agentgateway-system/anthropic-messages
endpoint=api.anthropic.com:443
src.addr=10.244.0.18:41332
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
