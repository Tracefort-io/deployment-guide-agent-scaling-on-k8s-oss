# Operating, Isolating, and Scaling Agent Workloads on Kubernetes (Open Source)

A validated quick start for running agents on **Agent Substrate** under **kagent**, with every
model and tool call they make going through **agentgateway**. Every component is open source.

> [!NOTE]
> This guide is being written. The steps below are its outline; a step is complete when it has
> commands and output captured from a live cluster.

---

## The problem this solves

Running one agent is a deployment. Running a platform of them is a different problem. Run as
ordinary pods, each agent carries its own copy of everything:
- an idle agent holds its pod's memory while it waits for the next message;
- the provider key sits in the agent's environment, readable by anything that can read the process;
- every agent calls the provider directly, so there is no single place to authenticate agents,
  cap their spend or see what they called;
- agents from different teams share the node's kernel.

kagent and Agent Substrate answer the first and last: each session is its own actor in a gVisor
sandbox, restored from a snapshot when a message arrives and suspended when the turn ends.
agentgateway answers the rest: one place where agents authenticate and every call is capped and
recorded.

---

## What you get

A cluster built with `kagent install`, then each part of it shown working:

| Section | What it establishes |
|---|---|
| **Up and Running** | Substrate, PostgreSQL and kagent from one command, agentgateway in front of the model, and a first turn proved to have gone through it |
| **Operate** | How an agent becomes actors, one session followed through suspension, its trace, inspecting actors, where context is kept, deleting a session, and a second runtime |
| **Isolate** | The gVisor sandbox, what a suspended session leaves, credential injection, the egress allowlist, MCP tools through the gateway, token caps, the snapshot store, and single sign-on |
| **Scale** | Memory per turn in flight, storage per suspended session, sizing the worker pool and changing it without losing capacity |
| **Validate** | One session driven through every control, each result mapped to the step that set it |

Every command, manifest and output in the guide is captured from a live cluster, not written from
documentation. The guide is certified by running it end to end on a fresh cluster, under bash and
under zsh, both as written and with every block pasted in document order. The product versions are
stated at the top of the guide.

---

## Before you start

| Requirement | Notes |
|---|---|
| Docker with 6 CPU and 12 GiB | The reference environment is Colima on macOS |
| `kind` 0.32.0, `kubectl`, `helm`, `jq`, `curl`, `openssl`, `zstd` | Standard tooling |
| An Anthropic API key, or an OpenAI key with Attachment D | Stored once, in the gateway; no agent holds it |
| Network access to `ghcr.io`, `docker.io`, `github.com` and `api.anthropic.com` (`api.openai.com` with Attachment D) | Images, pinned downloads and the model calls |

The `kagent` CLI and `kubectl-ate` are downloaded by the guide at pinned versions.

---

## Getting started

Read [`docs/deployment-guide.md`](docs/deployment-guide.md) from the top. It is written to be run
in order, and each step verifies before the next depends on it. Its first block sets up the shell,
bash or zsh, so the blocks after it paste cleanly. The guide needs nothing from this repository:
every file a step uses is in the guide or downloaded at a pinned version.

### Or build it automatically

The same guide can be executed end to end, verbatim. With `ANTHROPIC_API_KEY` exported, or in
`auto-build/.env.local`:

```bash
auto-build/run.sh
```

`auto-build/run.sh --dry-run` prints the script it would run. `--shell zsh` runs it under zsh and
`--in-order` runs every block, manual ones included, as a reader pasting top to bottom would. See
[`auto-build/README.md`](auto-build/README.md) for resuming and how it works.

---

## Repository layout

```
docs/
  deployment-guide.md   The guide. Read this first.
  attachments.md        Attachments A (sign-in), B (clusters without the beta certificates API),
                        C (troubleshooting) and D (OpenAI in place of Anthropic)
auto-build/
  run.sh                Runs the guide's blocks verbatim against the current cluster
  extract.py            Pulls the runnable blocks out of the guide, in document order
  check-paste.py        Pastes every block into interactive zsh and bash and reports breaks
  README.md             Credentials, flags and how it works
```

Product versions are not duplicated here. They are pinned in the environment setup block at the
top of the guide, which is itself a runnable block, so the guide and the automation cannot
disagree about what is being deployed.

---

## Releases

Each certified state of the guide is tagged:

| Tag | Certified against |
|---|---|
| `certified-rc1` | kagent 1.0.0-rc1, Agent Substrate v1.0.0-rc1 and agentgateway v1.7.0, in four modes, with OpenAI (Attachment D) verified through Step 14 |

The guide documents a validated deployment at a point in time. It does not cover upgrade paths
between product versions; a fresh install of the pinned set is the only path it verifies.

---

## Contributing

Corrections are welcome, particularly from anyone who has run the guide and found that reality
disagrees with it. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for what to check before opening a pull
request and the conventions the guide follows.

---

## Support

This repository is reference material. It is not a supported product and carries no SLA. For
problems with the guide itself, open an issue here. For the projects it deploys, use their own
communities: [kagent](https://github.com/kagent-dev/kagent),
[Agent Substrate](https://github.com/kagent-dev/substrate) and
[agentgateway](https://github.com/agentgateway/agentgateway).
