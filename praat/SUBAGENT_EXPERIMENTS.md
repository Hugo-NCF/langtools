# Project Subagent Experiments

**Project:** LangTools / Praat  
**Experiment date:** November 6, 2026 (America/New_York; timestamps below are UTC)  
**Client tested:** `codex-cli 0.166.0-alpha.6` plus this desktop session's native subagent tools  
**Status:** experiments completed; external-provider inference and local-model inference were not demonstrated.

## 1. Questions and results

| Requirement | What was actually done | Result |
|---|---|---|
| Create a project subagent using the everyday model | Added a project role and defaults; launched two real native subagents with the configured model/effort and project-file tasks | **Passed:** real subagents completed their checks |
| Run multiple agents in parallel | Started two independent agents, then synchronized their command probes with a shared release signal | **Passed:** 7.985848 seconds of measured overlap |
| Run agents with other providers | Checked provider inheritance; configured a separate custom-provider CLI session and observed its request at a controlled endpoint | **Transport passed; real third-party model inference not tested** |
| Run local agents/models | Executed the installed CLI's Ollama mode and checked local service availability | **Attempt failed:** no Ollama server; local inference remains unverified |

The practical answer is that project subagents and concurrent work run in this environment. Changing a child's model is different from changing its provider. Local tool execution is also different from local inference.

## 2. Project configuration actually created

The everyday user configuration contained `model = "gpt-6.1-sol"` and `model_reasoning_effort = "high"`. The project previously had no `.codex/config.toml`. No global settings were changed.

Created [`.codex/config.toml`](../.codex/config.toml):

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 2
default_subagent_model = "gpt-6.1-sol"
default_subagent_reasoning_effort = "high"
```

Created [`.codex/agents/project-reviewer.toml`](../.codex/agents/project-reviewer.toml):

```toml
name = "project_reviewer"
description = "Checks project documents and configuration using evidence from local files."
model = "gpt-6.1-sol"
model_reasoning_effort = "high"
developer_instructions = """
Read the assigned project files and report concrete evidence.
Do not edit project content. Write experiment evidence only when explicitly requested.
Distinguish observed results from documentation and assumptions.
Do not inspect credentials or print secrets.
"""
```

Both files were parsed with Python `tomllib`. The project defaults are nested under `[agents]`; they do not set or change the primary agent's top-level model. The role specifies the same model and reasoning effort explicitly. The configured concurrency limit is two spawned threads per session; this test exercised two, not a maximum-capacity sweep.

Official documentation describes standalone project agents, model inheritance and provider inheritance. A custom agent cannot select a different provider from its parent. The role's instruction to avoid edits is a behavioral instruction, not an independent read-only permission boundary. [OpenAI subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)

## 3. Real subagent execution

The desktop coordinator invoked its native spawn tool twice with:

```text
task_name: project_review_a / project_review_b
model: gpt-6.1-sol
reasoning_effort: high
fork_turns: none
project: langtools
```

These were actual agents with separate task contexts. Each was directed to read the project reviewer role before performing its assignment. The tool requests explicitly selected the everyday model; model identity was not guessed from the agent's prose.

| Agent | Assignment | Observed output |
|---|---|---|
| `/root/project_review_a` | Parse the project and role TOML; inspect nested subagent defaults | Both TOML files valid; model `gpt-6.1-sol`, reasoning `high` |
| `/root/project_review_b` | Inspect the local Praat master document's structure | File exists, 140,491 bytes, expected title, 19 numbered main chapters |

Agent B inspected the existing local research snapshot. Its result is not a statement that the remote repository still has that exact document revision, and it does not validate the scientific contents.

The native spawn interface in this session takes tasks and explicit model parameters; it does not expose a named custom-role selector. Therefore these runs establish real delegation and application of the assigned role instructions, rather than independently proving automatic discovery of that filename by the desktop tool.

### Additional CLI named-role check

A separate CLI session used `--strict-config`, `--ephemeral`, `--skip-git-repo-check`, `-m gpt-6.1-sol`, high reasoning, and the LangTools working directory. It was asked to discover `project_reviewer`, delegate a configuration check, and return `CONFIGURED_ROLE_OK`.

The process exited **0**, read the project files, and reported the marker. However, the captured JSONL contained no explicit spawn record; it exposed a wait item with an empty receiver list and a final success statement. I do **not** count that self-report as independent proof of named-role dispatch. The two native spawn calls and their returned evidence provide the stronger execution evidence. See [CLI check summary](subagent-evidence/cli-custom-agent-summary.json).

This distinction prevents a generated success sentence from being treated as a verified orchestration trace.

## 4. Parallel-agent experiment

The first attempt showed why simply launching two agents is insufficient evidence. The coordinator briefly listed both as running, but their measured command windows were sequential:

- A: 23:43:14.430275–23:43:26.436249 UTC.
- B: 23:43:36.848435–23:43:48.858983 UTC.

That attempt had **zero measured command overlap**. It was retained rather than reported as a concurrency success.

For the second attempt, each agent wrote its own ready marker and waited for a shared `parallel-go.json`. The coordinator released the barrier only after both markers existed. Each agent then checked its assigned files, deliberately waited eight seconds, and recorded start/end times using both UTC and the same host's monotonic clock.

| Agent | UTC start | UTC end | Elapsed |
|---|---|---|---:|
| A | 2026-11-06T23:45:06.642038+00:00 | 2026-11-06T23:45:14.649658+00:00 | 8.007524 s |
| B | 2026-11-06T23:45:06.620682+00:00 | 2026-11-06T23:45:14.627930+00:00 | 8.007215 s |

Overlap was computed from monotonic timestamps:

```text
overlap = max(0, min(end_A, end_B) - max(start_A, start_B))
        = 7.985847875 seconds
```

This confirms overlapping execution in two real subagent tasks. The deliberate wait creates a clear concurrency window; it is not evidence of faster document analysis, simultaneous token generation, or parallel GPU inference. Separate output files avoided write conflicts. Both tasks finished successfully.

Evidence: [parallel summary](subagent-evidence/parallel-summary.json), [agent A](subagent-evidence/agent-a-parallel.json), [agent B](subagent-evidence/agent-b-parallel.json).

## 5. Other providers: what is supported and what was tested

### Provider selection has a session boundary

The native spawn tool available here accepts a model override but has no provider argument. Its model list contains OpenAI model identifiers. Entering another vendor's model name does not establish a connection to that vendor.

The documented custom-provider route belongs to the parent session's configuration. For a different provider, start a separate parent session using its endpoint, credentials and compatible model; any children inherit that provider. Coordinating such separate sessions is different from selecting providers independently for children in this session. [Advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)

The configuration reference exposes `model_provider` and `model_providers.<id>` entries including `base_url`, `env_key` and `wire_api`. The current reference lists `responses` as the supported wire API. An endpoint advertising only generic Chat Completions compatibility is therefore not sufficient evidence of Codex compatibility. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)

### Executed custom-provider transport probe

I started a temporary HTTP server on loopback and launched a separate Codex CLI process with a custom provider. Equivalent configuration:

```toml
model = "transport-probe"
model_provider = "loopback_probe"

[model_providers.loopback_probe]
name = "Loopback transport probe"
base_url = "http://127.0.0.1:PORT/v1"
wire_api = "responses"
request_max_retries = 0
```

`PORT` was replaced with the server's actual temporary port. The CLI sent a POST to **`/v1/responses`**, with model **`transport-probe`** and streaming enabled. The server deliberately returned HTTP 400 with `LOCAL_TRANSPORT_PROBE_ONLY_NO_INFERENCE`. The CLI exited 1 as expected; the temporary server was then shut down.

This is a successful configuration/routing check and an intentional inference failure. The server was a stub, not an LLM, not an external vendor, and not a successful agent conversation. [Transport evidence](subagent-evidence/custom-provider-summary.json)

### What remains unverified

No custom provider was configured in the user's global config. The checked process environment did not contain `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `OPENROUTER_API_KEY`. These checks inspected boolean presence only; they do not establish whether credentials exist elsewhere.

No real non-OpenAI provider call was attempted without a configured endpoint and credentials. A complete test would require an available account/server, supported protocol, authenticated inference, successful tool calls and a child task that inherits that parent provider. This report does not claim Anthropic, Gemini or another specific vendor works through this Codex installation.

## 6. Local agents and local inference

There are three distinct arrangements:

| Arrangement | Where agent tools run | Where inference runs | Status here |
|---|---|---|---|
| Local Codex using the signed-in OpenAI model | Local project/computer | Remote model service | Real subagent experiment completed |
| Locally written program calling OpenAI's hosted API | Local program | OpenAI service | Conceptually separate; no API-key inference test performed |
| Codex or an SDK client using a local model server | Local computer | Local inference server | CLI attempted; missing server prevented inference |

An OpenAI-compatible local API is a protocol interface, not a requirement to send inference to OpenAI. Conversely, running a tool locally while it calls a hosted model does not make that model local.

### Executed local-provider attempt

The installed CLI help includes `--oss` and `--local-provider` with `ollama` or `lmstudio`. I ran:

```sh
codex exec --ignore-user-config --ephemeral --skip-git-repo-check \
  --oss --local-provider ollama -m gpt-oss:20b \
  -C /path/to/langtools \
  "Reply exactly LOCAL_AGENT_OK. Do not use tools."
```

The process exited **1** before inference. Its request to `http://localhost:11434/v1/models` was refused, and the error stated that no running Ollama server was detected. Neither `ollama` nor `lms` was on PATH; loopback ports 11434 and 1234 were unreachable. This only checks those common locations, not every possible local server.

No runtime or model weights were installed or downloaded. There is no successful local-model completion or local-subagent result to report. [Local-provider evidence](subagent-evidence/local-provider-summary.json)

The documented Codex route is `--oss --local-provider ollama` or `lmstudio`. [CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

### OpenAI SDK route for local inference

OpenAI's Ollama cookbook demonstrates using the OpenAI Python SDK with a loopback `base_url` and a local model. An illustrative **untested** example is:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
answer = client.chat.completions.create(
    model="gpt-oss:20b",
    messages=[{"role": "user", "content": "Check this short project description."}],
)
print(answer.choices[0].message.content)
```

This is a model-completion example, not a complete autonomous agent. An agent also needs a tool loop, state, stopping conditions and error handling. The cookbook's Chat Completions example does not establish Responses compatibility for the installed server version; verify that separately before connecting Codex. [Official local-model cookbook](https://developers.openai.com/cookbook/articles/gpt-oss/run-locally-ollama)

To finish local validation later: install/start an appropriate runtime, obtain a model that fits the machine, verify the supported API and tool behavior, rerun the CLI completion, then run the same two-task experiment under the local parent session. The current negative result must remain in the record until that succeeds.

## 7. Reproduction and limitations

1. Open the LangTools project containing the submitted `.codex` files.
2. Verify the installed CLI/version and that project configuration is accepted. A separate CLI session is useful for fresh configuration discovery.
3. Request two subagents explicitly using `gpt-6.1-sol` and high reasoning. Assign separate read-only tasks and distinct evidence outputs.
4. Use a common start barrier and record UTC plus monotonic timestamps if measuring overlap; listing two running agents alone is insufficient.
5. For another provider, configure a separate compatible parent session and test real inference/tool execution before testing its children.
6. For local inference, verify a real local model server first. A loopback stub proves routing only.

The experiment used a local project snapshot and this session's available tools. It did not benchmark scientific software, measure maximum parallel capacity, certify backend model routing, test every provider, or establish offline model inference. Raw CLI output was not submitted because it includes unrelated inherited context; the included summaries retain the relevant outcomes without credential contents.

The submission consists of this report, the actual project config/role, and compact JSON evidence. Existing application code and the master reimplementation specification are outside this change.
