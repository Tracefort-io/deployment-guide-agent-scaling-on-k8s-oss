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
