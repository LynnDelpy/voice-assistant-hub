# Voice-Assistant-Hub

A local, self-contained build of the hub from the architecture document. You can
talk or type to it, it decides which tool to call, a policy gate authorizes the
call, and a module carries it out. Everything runs in a disposable Docker Compose
sandbox you can kill and restart clean.

```
voice-assistant-hub/
├── environment/   the disposable sandbox (Docker Compose, real HA, TLS proxy)
└── project/       the hub code (vahub), bind-mounted into the sandbox
```

You edit code in `project/`. The sandbox runs it live (the source is bind-mounted,
so a restart picks up changes, no rebuild unless dependencies change).

## What runs

Every milestone from the build order is implemented and working in the sandbox.

* **Core (M0, M1).** Config validation, an event bus with decided backpressure,
  JSON logging, graceful shutdown, Prometheus metrics, and a supervisor that
  spawns each module as its own subprocess and speaks MCP over stdio.
* **Policy gate + audit (M3).** Default deny, checked on every call at the
  argument level. Destructive actions (unlocking a door) are confirmed out of
  band with frozen arguments, never over voice, never self-confirmed by the
  model. Every call, allowed or denied, is written to a SQLite audit log with the
  principal (agent, scheduler, dev, or the confirming user).
* **Agent (M4).** An LLM tool-calling loop with per-turn and per-day budgets. It
  runs on a real model (OpenRouter by default, configured in the LLM section
  below). A no-key keyword stub is also available (`llm.provider: mock`) for
  testing the loop without credentials.
* **Home Assistant (M5).** A module exposing narrow tools (lights, locks,
  sensors). It runs against a real Home Assistant container that ships in the
  sandbox, configured with virtual (mocked) devices, no physical hardware. It is
  onboarded headlessly on first start. The same module works against your real HA
  by changing one URL and token.
* **Transit.** A module for Swiss public transport (departures and connections)
  over the free transport.opendata.ch API, no key. Ask it when your tram leaves.
* **Scheduler (M6).** Deterministic routines (a morning routine) that run through
  the gate as their own principal, without the agent and without an LLM.
* **Voice (M7).** A microphone in the web console. By default the browser handles
  speech (Web Speech API), so voice works with no credentials. A server side path
  (`/api/voice`) is wired for real STT/TTS.
* **TLS + mTLS auth (M2).** An optional reverse proxy that terminates TLS and
  requires a client certificate. The hub itself has no auth code: the proxy is
  the only way in, and it passes the verified certificate subject to the hub for
  the audit log.

## Quickstart

Requires Docker with the Compose plugin.

```bash
cd environment
./sandbox.sh up          # build and start; hub on http://localhost:8080
```

Open http://localhost:8080. You get a console with a chat box, a microphone
button, a live module status table, pending confirmations, and a log tail.

Type or say things like:

* "what time is it in Tokyo?"
* "turn on the bedroom light"
* "what's the temperature?"
* "unlock the front door" (this asks you to confirm, see below)

The microphone uses your browser's speech recognition (Chrome or Edge), and the
assistant speaks its reply back. Typing works in every browser.

## The policy gate and confirmations

The gate is the security boundary, in code, not in the prompt. It is defined in
[project/config/policy.yaml](project/config/policy.yaml) and checks arguments,
not just tool names. For example `light_turn_on` is allowed only for the four
named lights, and `brightness_pct` only in the range 1 to 100. A Home Assistant
token is admin or nothing, so this file is the real limit on what the assistant
can touch.

Unlocking a lock is classed destructive. When the agent proposes it, the gate
does not execute it. It creates a pending confirmation that appears in the
console with a Confirm button. Confirming runs the frozen arguments, out of band,
so a later change in the conversation cannot alter what actually happens. Over
voice, a destructive action is never executed without going to the display.

See the audit trail at any time:

```bash
curl -s localhost:8080/api/audit | python3 -m json.tool | head -40
```

## The scheduler

Routines are in [project/config/schedules.yaml](project/config/schedules.yaml).
The morning routine turns on the bedroom light and speaks the time on weekday
mornings, through the gate as `principal=scheduler` (which may act without
confirmation but is denied locks). Trigger it now without waiting for the cron
time:

```bash
curl -s -X POST localhost:8080/api/schedules/morgenroutine/run | python3 -m json.tool
```

## TLS and client-certificate auth (optional)

```bash
cd environment
./sandbox.sh tls         # generates dev certs and starts the proxy
```

Then import `environment/proxy/certs/client.p12` (empty password) into your
browser and open https://localhost:8443. Without the client certificate the
connection is refused. The proxy passes the certificate subject to the hub, and
it shows up in the audit log as the principal that confirmed an action. For a
real deployment you issue one client certificate per device and keep the CA key
offline. The mock certificates here are a sandbox convenience, not a production
PKI.

## Clean slate

```bash
./sandbox.sh reset       # down -v (wipe the state volume) then up
```

State (the SQLite database, conversations, the audit log, pending confirmations)
lives only in the state volume, so a reset wipes it. The built images are not
wiped, so a reset is fast.

## The language model

The agent talks to an OpenAI-compatible endpoint, set in the `llm` block of
[project/config/config.yaml](project/config/config.yaml) (`provider`, `base_url`,
`model`). The API key is never in that file: it is read from
`environment/.env` (gitignored) as `VAHUB_LLM__API_KEY`. The adapter speaks the
OpenAI format, so OpenAI, OpenRouter, Groq, Ollama, llama.cpp, and Anthropic's
compatible endpoint all work. For a no-key run, set `llm.provider: mock`.

## Real Home Assistant vs your Home Assistant

The sandbox already runs the real Home Assistant software, just with virtual
devices (template entities defined in
[environment/homeassistant/config/configuration.yaml](environment/homeassistant/config/configuration.yaml)).
To point it at your own instance:

1. In your Home Assistant, create a long-lived access token (profile, then
   Security, then Long-lived access tokens, then Create Token).
2. Add to `environment/.env` (gitignored, never committed):
   ```
   HA_URL=http://<your-ha-ip>:8123
   HA_TOKEN=<the long-lived token>
   ```
   Use an IP or real DNS name, not a `.local` mDNS name (it does not resolve
   inside the container). For a self-signed HTTPS cert add `HA_VERIFY_SSL=false`.
3. Update [project/config/policy.yaml](project/config/policy.yaml) so the gate
   allows your entity ids. The sandbox policy only allows the demo's German names
   (`light.schlafzimmer`, `lock.haustuer`), so control commands against your
   home are denied until you list your real entities. Reading (`list_entities`,
   `get_state`) already works, so the agent can ask about your home immediately;
   only turning things on and off needs the allowlist updated.
4. Run `./sandbox.sh real-ha`. The simulated HA does not start in this mode; the
   hub talks to yours. The module code does not change (the module reads the
   token from `HA_TOKEN` here, the same as prod reads it from a credential file).

The old in-memory mock still lives under `environment/mock-ha/` if you ever want
a faster, lighter stand-in.

Note: with the sandbox's own HA (`./sandbox.sh up`), the first start after a
`reset` takes 30 to 60 seconds while HA boots and is onboarded headlessly.

## What is real and what is mock

Real, not mocked: the whole hub (supervisor, MCP client, gate, audit log,
confirmations, agent loop, scheduler, budgets), the module contract, the SQLite
persistence, the mTLS boundary, the language model (OpenRouter), and Home
Assistant (the real software with virtual devices). Mocked for local testing: the
HA devices themselves (template entities, not hardware) and speech (the browser's
Web Speech, unless you configure server STT/TTS).

## Testing

```bash
./sandbox.sh test        # runs the suite inside the sandbox
```

## A note on production

The Docker sandbox is for building and testing the application logic with fast
iteration and clean teardown. It is not the production security boundary. In
production the hub runs in an unprivileged LXC with a dedicated UID per module,
secrets from systemd credentials, a restricted egress firewall, and the mTLS
proxy in front. The proxy and certificate handling shown here are the template
for that, not a finished deployment.
