# Attachments

Attachments to [the deployment guide](deployment-guide.md). Each one branches from a step in the
guide and returns to it.

## Attachment A: Single sign-on in detail

Step 22 puts oauth2-proxy and Keycloak in front of kagent and switches the controller to
`trusted-proxy` mode. This attachment explains what passes between them, which claim names the
user, and what to change to use your own identity provider.

### A.1 Who checks what

A request passes three components, and only the first one checks the token:

| Component | What it checks | What it passes on |
|---|---|---|
| oauth2-proxy (`kagent-oauth2-proxy`) | A browser's session cookie, or a script's bearer token: the signature against Keycloak's keys, the issuer (`iss`) against its configured issuer byte for byte, the audience (`aud`) against its client, `kagent-ui`, and expiry | The request, with the user's token in the `Authorization` header (`pass-authorization-header`, `set-authorization-header`) |
| The UI (`kagent-ui`) | Nothing | The request, header included, to the controller |
| The controller, `trusted-proxy` mode | That there is exactly one `Authorization: Bearer` header holding a JWT that has not expired. **Not its signature, issuer or audience** | Nothing: it reads the user from the token |

That is why Step 23 fences the controller: it trusts whatever reaches it to have passed
oauth2-proxy. Scripts in this guide reach the controller through its port-forward, which passes no
NetworkPolicy, with real tokens from Keycloak (`kagent_token`).

### A.2 Which claim names the user

The controller takes the user from the claim `config.server.auth.userIdClaim` names. Step 22 sets
`preferred_username`, the Keycloak username. The default is `sub`, and the controller falls back to
`sub` when the named claim is missing. It records the owner of every session as two parts: the
token's issuer, prefixed `trusted-proxy:`, and that user ID, each base64url-encoded and joined by a
dot. So:

- **A session belongs to an issuer and a user together.** Change the issuer URL, and every existing
  session belongs to no one who can sign in, as the sessions from before Step 22 do.
- **Choose a claim no user can change, and no two users share.** In Keycloak, `preferred_username` is
  unique in a realm and users cannot change it unless the realm allows them to edit their username.
  `sub` is a fixed ID, and is the safer choice where usernames can be reassigned. In this guide
  `preferred_username` makes sessions and logs readable as `alice` and `bob`.

Look at what alice's token carries, the owner the controller derives from it, and the owners of the
sessions in kagent's database:

```bash run id=a-token-claims when=manual
CLAIMS=$(kagent_token alice | cut -d. -f2 | tr '_-' '/+' \
  | awk '{l=length($0)%4; if (l) $0=$0 substr("===", 1, 4-l); print}' | base64 --decode 2>/dev/null)
echo "$CLAIMS" | jq -c '{iss, aud, azp, sub, preferred_username, exp}'
b64url() { printf '%s' "$1" | base64 | tr '+/' '-_' | tr -d '=\n'; }
echo "owner: $(b64url "trusted-proxy:$(echo "$CLAIMS" | jq -r .iss)").$(b64url "$(echo "$CLAIMS" | jq -r .preferred_username)")"
echo "-- session owners in kagent's database:"
kubectl exec -n kagent deployment/kagent-postgresql -c postgresql -- psql -U postgres -d kagent -At -F ' ' -c \
  "select user_id, count(*) from kagent.runtime_instance where kind = 'agent' and state <> 'RUNTIME_STATE_DELETED' group by 1 order by 1"
```

**Verified on 2026-11-03:**

```text
{"iss":"http://keycloak.localhost:18080/realms/kagent","aud":"kagent-ui","azp":"kagent-cli","sub":"18eab7bb-3f66-44c5-a332-4b8e6521f11e","preferred_username":"alice","exp":1793691945}
owner: dHJ1c3RlZC1wcm94eTpodHRwOi8va2V5Y2xvYWsubG9jYWxob3N0OjE4MDgwL3JlYWxtcy9rYWdlbnQ.YWxpY2U
-- session owners in kagent's database:
admin@kagent.dev 10
dHJ1c3RlZC1wcm94eTpodHRwOi8va2V5Y2xvYWsubG9jYWxob3N0OjE4MDgwL3JlYWxtcy9rYWdlbnQ.YWxpY2U 9
dHJ1c3RlZC1wcm94eTpodHRwOi8va2V5Y2xvYWsubG9jYWxob3N0OjE4MDgwL3JlYWxtcy9rYWdlbnQ.Ym9i 2
```

`aud` holds `kagent-ui` because the `kagent-cli` client's audience mapper puts it there: without
it, oauth2-proxy refuses a script's token as meant for another client. `azp` says which client the
token was issued to.

### A.3 What Step 22 deploys

| Resource | Purpose |
|---|---|
| Secret `keycloak/keycloak-admin` | The bootstrap admin password, generated in your shell |
| Secret `keycloak/keycloak-realm` | The realm, rendered with generated secrets and passwords |
| Deployment and Service `keycloak/keycloak` | Keycloak in development mode, importing the realm at start; the Service port equals the issuer's port |
| CoreDNS rewrite | Makes `keycloak.localhost` resolve to the Keycloak Service inside the cluster |
| kagent values `config.server.auth` | `trusted-proxy` mode and the user ID claim |
| kagent values `oauth2-proxy.*` | The chart's bundled oauth2-proxy, with this realm's issuer, client and redirect |

The realm has two clients and two users:

| Client | Type | Flows | Used by |
|---|---|---|---|
| `kagent-ui` | Confidential, with a secret | Authorization code; redirect `http://localhost:${KAGENT_UI_PORT}/oauth2/callback` | oauth2-proxy, for the browser |
| `kagent-cli` | Public | Password grant only; audience `kagent-ui` added to its tokens | Scripts in this guide |

| User | What they can do |
|---|---|
| `alice`, `bob` | Sign in, see and change every agent, and use only their own sessions |

There are no groups: open-source kagent has no roles to give them.

### A.4 Using your own identity provider

Register one confidential client for oauth2-proxy, with its redirect URI at your UI's address, and
set its issuer, client ID and secret in the `oauth2-proxy` values as Step 22 does. Set
`config.server.auth.userIdClaim` to the claim that names your users (A.2). Scripts that call the API
need tokens whose `aud` includes that client, and whose issuer is byte for byte the one oauth2-proxy
is configured with. With a provider on a public hostname, the CoreDNS rewrite and the Keycloak
port-forward are not needed: your browser and the cluster already reach the same URL. Only
Keycloak was verified with this release.

### A.5 Why this is lab-only

- Keycloak in development mode, over HTTP, with its database inside the pod: every restart
  re-imports the realm from its Secret, and a change made since is lost.
- Cookies not marked secure, because the UI is served over plain HTTP on localhost.
- Generated passwords that live only in your shell, the credentials file and the realm Secret.
- `kagent-cli`, a public client with the password grant. Disable it when you are done (Before
  production has the block).

## Attachment B: Clusters without the beta certificates API

The guide's main path runs on a cluster where `certificates.k8s.io/v1beta1` and the
`ClusterTrustBundle`, `ClusterTrustBundleProjection` and `PodCertificateRequest` feature gates are
on (Step 1). **The open-source release in this guide needs them.** Substrate's pod certificate
controller signs every certificate through PodCertificateRequest and publishes its trust roots as
ClusterTrustBundles, and it has no other way to do either: on a cluster without them it stops at
start, and nothing that waits for a certificate, PostgreSQL and every Substrate component, starts.

So the choice is the cluster, not the install:

- **kind and other clusters you create:** turn the API and the gates on, as Step 1 does. They are
  beta on Kubernetes 1.35 and off by default.
- **A managed Kubernetes service:** use it only if it lets you serve `certificates.k8s.io/v1beta1`
  with those three gates on. None was tested with this guide.

This attachment shows what happens on a cluster without them, so you can recognise it.

**Verified on:** a throwaway kind cluster `compat` (kind 0.32.0, `kindest/node:v1.35.0`),
2026-11-03 07:20 UTC: Up and Running run verbatim through Step 3 with the two blocks below in place
of the ones they name (`auto-build/run.sh --attachment docs/attachments.md --to install-kagent`).

### B.1 The cluster (in place of `kind-cluster` and `step-1-certificates-api`)

The same node image, with the gates and the beta API left at their defaults, which are off on 1.35:

```bash run id=b-kind-cluster replaces=kind-cluster
kind create cluster --name "${KIND_CLUSTER_NAME}" --config - <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
runtimeConfig:
  "api/alpha": "false"
nodes:
  - role: control-plane
    image: ${KIND_NODE_IMAGE}
EOF
```

```bash run id=b-certificates-api replaces=step-1-certificates-api
kubectl api-resources --api-group=certificates.k8s.io
kubectl get --raw /metrics | grep -E 'kubernetes_feature_enabled\{name="(ClusterTrustBundle|ClusterTrustBundleProjection|PodCertificateRequest)"'
```

**Verified on 2026-11-03:**

```text
NAME                         SHORTNAMES   APIVERSION               NAMESPACED   KIND
certificatesigningrequests   csr          certificates.k8s.io/v1   false        CertificateSigningRequest
kubernetes_feature_enabled{name="ClusterTrustBundle",stage="BETA"} 0
kubernetes_feature_enabled{name="ClusterTrustBundleProjection",stage="BETA"} 0
kubernetes_feature_enabled{name="PodCertificateRequest",stage="BETA"} 0
```

Only `certificatesigningrequests` at `v1`, and all three gates at 0.

### B.2 What `kagent install` does there

Step 2 is unaffected: agentgateway needs no pod certificates. Step 3's `install-kagent` installs
the pod certificate controller first, and waits for it. It never becomes ready; after five minutes
the install gives up:

```text
Error: failed to install substrate-podcert: failed to run helm: exit status 1: Pulled: ghcr.io/kagent-dev/substrate/helm/substrate-podcert:1.0.0-rc1
Digest: sha256:e126ed36bba44823b1948a0d72c22eacbc6f880c3a7e0598bbf6ebc19361a59f
Error: resource Deployment/podcertificate-controller-system/podcertificate-controller not ready. status: InProgress, message: Available: 0/1
context deadline exceeded: Release "substrate-podcert" does not exist. Installing it now.
```

The controller's own log says why, at every restart:

```text
{"level":"ERROR","msg":"Error discovering PodCertificateRequest API","err":"neither v1 nor v1beta1 PodCertificateRequest is served"}
```

Attachment C.1 has a block that checks for both. Delete the cluster and create it as Step 1 does:
kind sets the API and the gates only when it creates a cluster.

## Attachment C: Troubleshooting

Each entry gives the symptom, the cause and the fix. **Reproduced** means the symptom was produced
on this stack, on purpose or while verifying this guide. Entries that rest on the release's design
rather than a reproduction say so. The check and fix blocks are written to be run as they are, in
your guide shell. Every one was run on 2026-11-03 on a cluster built by the guide, after Validate
passed there (`auto-build/run.sh --attachment docs/attachments.md --only ...`); on that healthy
cluster, C.1's and C.2's checks report nothing wrong.

### Install

**C.1 `kagent install` fails after five minutes with `failed to install substrate-podcert`, and the
pod certificate controller crash-loops.** *Reproduced (Attachment B).* Its log says `neither v1 nor
v1beta1 PodCertificateRequest is served`. Cause:
the cluster does not serve `certificates.k8s.io/v1beta1` with the three feature gates on, which this
release requires (Attachment B). Fix: create the cluster as Step 1 does. Check what it serves:

```bash run id=c-certificates-check when=manual
kubectl api-resources --api-group=certificates.k8s.io
kubectl get --raw /metrics | grep -E 'kubernetes_feature_enabled\{name="(ClusterTrustBundle|ClusterTrustBundleProjection|PodCertificateRequest)"'
kubectl logs -n podcertificate-controller-system deployment/podcertificate-controller --tail=20 2>/dev/null \
  | awk '/is served/ && !f {print; f=1} END {if (!f) print "the pod certificate controller reports no missing API"}'
```

**C.2 Every pod times out reaching the API server; `kube-proxy` crash-loops with `too many open
files`.** *Reproduced, with a second kind cluster on the same Docker host.* Cause: each kind node
uses inotify instances from the Docker host's shared limit, and a second cluster can exhaust it.
Fix: delete the other cluster, or raise the limit on the Docker host. On Colima,
`colima ssh -- sudo sysctl -w fs.inotify.max_user_instances=512` raises it until the VM restarts;
for this guide the other cluster was deleted instead, and the sysctl was not run. Check `kube-proxy`:

```bash run id=c-kube-proxy-check when=manual
kubectl get pods -n kube-system -l k8s-app=kube-proxy
kubectl logs -n kube-system -l k8s-app=kube-proxy --tail=50 \
  | awk '/too many open files/ && !f {print; f=1} END {if (!f) print "kube-proxy reports no open-file limit"}'
```

**C.3 A pasted block seems to do nothing, and the blocks pasted after it never run.** *How `kubectl
run -i` works.* It reads standard input, and takes everything pasted after it. Blocks in this guide
that use it end with `< /dev/null`; keep it if you adapt them.

### Agents and Substrate

**C.4 Every `kubectl-ate` command starts with `Warning: no --token-file given`.** *Reproduced (every
call).* Cause: kubectl-ate acts as the shared `ate-client` identity `kagent install` set up, not as
you (Step 7). Fix: none needed; the guide sends kubectl-ate's warnings to `/dev/null` from Step 8
on.

**C.5 A turn is refused with `input was not accepted; retry after the session becomes
available`.** *Reproduced (12 turns sent at once to one worker).* Cause: every worker that could
run the session's actor already runs as many actors as its node has CPUs; Substrate holds the turn
for about 5 seconds and then refuses it (Step 27). Fix: retry, add workers with nodes to run them
(Step 28), or suspend actors that hold a slot without a turn in flight (C.9). Check the workers'
reservations:

```bash run id=c-worker-capacity when=manual
kubectl-ate get workers 2>/dev/null
echo "running actors: $(kubectl-ate get actors -a kagent -o json 2>/dev/null | jq '[.actors[] | select(.status.state == "ACTOR_STATE_RUNNING")] | length')"
```

**C.6 After a configuration change, turns still behave as before and are not traced.**
*Reproduced (Step 10).* Cause: a change to an agent's runtime configuration is a new revision, with
its own actor template and golden snapshot; the `Agent` stays `Ready` on the old revision while the
new one is built, and turns go to the old one. Sessions started before the change stay on their
revision. Fix: wait for the new revision, and start a new session to use it:

```bash run id=c-agent-revision when=manual
AGENT=${AGENT:-assistant}
kubectl get agent "$AGENT" -n kagent -o jsonpath='{.status.latestSuccessfulRevision}{"  "}{range .status.conditions[*]}{.type}={.status} {end}{"\n"}'
kubectl-ate get actor-template -a kagent 2>/dev/null
```

**C.7 The worker pool is a worker short after a change.** *Follows from the design (Step 28); not
reproduced on this stack.* Cause: the worker rollout removes a worker before starting its
replacement, and the replacement cannot schedule; only the Pending pod's event says why. Fix: find
the reason, then undo the resource change or add a worker first (Step 28):

```bash run id=c-worker-pending when=manual
kubectl get pods -n kagent -o wide | grep -E '^NAME|kagent-default-'
kubectl get events -n kagent --field-selector reason=FailedScheduling --sort-by=.lastTimestamp | tail -3
kubectl get workerpool -n kagent
```

**C.8 The chart's `kagent-api` `RemoteMCPServer` reports `DiscoveryFailed`.** *Reproduced (every
run of this guide).* Cause: the chart creates it pointing at the controller's own MCP endpoint,
`http://kagent-controller.kagent.svc.cluster.local:8083/mcp`, and the controller cannot discover its
tools; after Step 23, the controller's NetworkPolicy does not admit the controller itself, and
discovery times out. This guide does not use it. Fix: none needed; do not give agents tools from it.
Check its state:

```bash run id=c-kagent-api-state when=manual
kubectl get remotemcpserver kagent-api -n kagent -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}: {.message}{"\n"}{end}' 2>/dev/null || echo "no kagent-api RemoteMCPServer"
```

**Verified on 2026-11-03:**

```text
Accepted=False DiscoveryFailed: Failed to connect to MCP server: failed to connect MCP client: context deadline exceeded
```

### Gateway and model

**C.9 A turn never ends after the team's token cap runs out.** *Reproduced (Step 20).* agentgateway
refuses the model call with `429` and `Retry-After` set to the time left in the cap's window, up to
an hour, and the kagent runtime waits for it: the turn stays `TASK_STATE_WORKING`, its actor keeps
running and holds a worker slot, and its session refuses another message. Fix: raise the cap or let
its window pass, then cancel each stuck turn with A2A's `CancelTask`, as the user who started it:
anyone else is answered `task not found`. This finds turns working for more than `STUCK_MINUTES`
(10 by default), reads who started each from the session's owner in kagent's database (Attachment
A.2), and cancels it with that user's token:

```bash run id=c-cancel-stuck-turns when=manual
STUCK_MINUTES=${STUCK_MINUTES:-10}
kubectl exec -n kagent deployment/kagent-postgresql -c postgresql -- psql -U postgres -d kagent -At -F ' ' -c \
  "select ar.agent_name, t.id, ri.user_id from kagent.session_task t
     join kagent.session s on s.history_id = t.history_id
     join kagent.runtime_instance ri on ri.id = s.id
     join kagent.agent_revision ar on ar.revision = ri.prepared_revision
   where t.state = 'TASK_STATE_WORKING' and t.status_timestamp < now() - make_interval(mins => ${STUCK_MINUTES})" \
  | while read -r agent task owner; do
      user=$(printf '%s' "${owner#*.}" | tr '_-' '/+' | awk '{l=length($0)%4; if (l) $0=$0 substr("===", 1, 4-l); print}' | base64 --decode)
      printf '%s %s, started by %s: ' "$agent" "$task" "$user"
      token=$(kagent_token "$user" 2>/dev/null) || { echo "no password for ${user} in this shell"; continue; }
      curl -sS --max-time 30 -X POST "http://127.0.0.1:${KAGENT_API_PORT}/agents/kagent/${agent}" \
        -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
        -d "{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"CancelTask\",\"params\":{\"id\":\"${task}\"}}" \
        | jq -r '.result.status.state // .error.message'
    done
```

**Verified on 2026-11-03:**

```text
assistant 01a1a059-586b-75d0-8acf-db35747b831a, started by bob: TASK_STATE_CANCELED
```

Verified on this release: with the cap set to 1 token, a turn of bob's stayed working. The block,
run with `STUCK_MINUTES=0`, found it and cancelled it as bob, and his next turn completed once the
cap was restored. A cancelled turn's session takes new messages again.

**C.10 A check through a port-forward reads something else, or nothing.** *How port-forwards fail.*
Cause: another process already holds the local port, so the new port-forward failed in the
background and `curl` reached whatever holds it; a port-forward also dies with the pod it was
attached to. `forwards` reports a port it cannot take. Fix: see what holds each port, then set the
port variables in Environment setup to free ones, or close the stale forward and run `forwards`:

```bash run id=c-port-check when=manual
for port in "${KAGENT_API_PORT}" "${KAGENT_UI_PORT}" "${KEYCLOAK_PORT}" "${JAEGER_PORT}" 18082; do
  holder=$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR == 2 {print $1 " pid " $2}' || true)
  echo "${port}: ${holder:-free}"
done
```

### Sign-in

**C.11 oauth2-proxy restarts after Step 22, and the UI answers nothing.** *Reproduced (every run
of this guide, until the rewrite reaches CoreDNS).* Cause: oauth2-proxy fetches the issuer's discovery document when it starts,
and exits if it cannot; until the CoreDNS rewrite reaches CoreDNS, `keycloak.localhost` does not
resolve inside the cluster. Fix: wait until Step 22's `keycloak-forward` block prints the same
issuer from both sides; oauth2-proxy then starts on its next restart. Check it, and why its last
container stopped:

```bash run id=c-oauth2-proxy-check when=manual
kubectl get pods -n kagent -l app.kubernetes.io/name=oauth2-proxy
kubectl logs -n kagent deployment/kagent-oauth2-proxy --previous 2>/dev/null \
  | awk '/Failed to initialise/ && !f {print substr($0, 1, 160) "..."; f=1} END {if (!f) print "no failed start in the previous container"}'
```

**Verified on 2026-11-03:**

```text
NAME                                   READY   STATUS    RESTARTS        AGE
kagent-oauth2-proxy-84f7d89d6c-kmjdt   1/1     Running   2 (4m44s ago)   4m47s
[2026/11/03 07:36:01] [main.go:73] ERROR: Failed to initialise OAuth2 Proxy: error initialising provider: could not create provider data: error building OIDC Pr...
```

**C.12 The `kagent` CLI is refused after Step 22.** *Reproduced.* Cause: it sends a user ID rather
than a token, and `trusted-proxy` mode accepts only a token (Step 22, Attachment A). Fix: use the UI,
or a Keycloak token as `kagent_send` does.

**C.13 Sessions from before Step 22 are missing from the UI and the API.** *Reproduced.* Cause: they
belong to `admin@kagent.dev`, the user every request was before sign-in, and no one who signs in is
that user (Attachment A.2). Fix: none through the API or the UI. They stay in Substrate and
PostgreSQL.

**C.14 After Keycloak restarts, a change made in its admin console or API is gone, and scripts get
`connection refused`.** *Reproduced (Before production).* Cause: Keycloak re-imports the realm from
its Secret when it starts (Attachment A.5), and the port-forward died with the old pod. Fix: re-run
Step 22's `keycloak-forward` block, and make lasting changes in the realm Secret.
