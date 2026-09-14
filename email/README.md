# headlong-email-bridge

A Resend inbound-webhook bridge that connects Headlong identities to
email addresses — one bridge process can serve several identities at
once, each with its own address, admin, and allowlist. Approved senders
email an identity, their messages land in that identity's mind log as
`message` steps, and its replies come back threaded in the same
conversation.

## How it works

```
Resend <=(HTTPS POST, svix-signed)=> headlong-email-bridge
    inbound:  email.received event -> GET full email from Resend
              -> match `to` against configured agents -> from_name "email-<slug>-<hash>"
              -> POST <web>/api/identities/<agent's id>/chat
    outbound: one thread per agent, each tailing its own trajectory.jsonl
              -> message steps where from=<that identity> and to=email-*
              -> POST <resend>/emails (In-Reply-To, References)
```

The sender's address can't be embedded directly in the chat `from` name
(the web API's `CHAT_FROM_RE` doesn't allow `@`), so the name is a
deterministic hash-suffixed slug of the address, and the address plus
current threading headers are kept in `threads.json` in the bridge's state
dir, looked up by that same id on the way out.

**This is the one bridge in this repo that listens.** Slack dials out over
Socket Mode, Telegram long-polls outbound — both keep the zero-ingress
design described in `deploy/SECURITY.md`. Resend pushes inbound mail in
over HTTP, so something has to accept a connection. Every request is
verified against the webhook's signing secret before anything else
happens (see `src/headlong_email/verify.py`), and the recommended setup
below still keeps no port open on the host — Cloudflare Tunnel dials out
to Cloudflare, and Cloudflare relays the (still-signed) webhook request in.

**The identity itself never talks to Resend, and never sees a Resend key.**
The only two places `api.Client` is constructed are `inbound.py` (to fetch
a received email's body after the webhook fires) and `outbound.py` (to
send a reply that `bin/chat` already wrote to the mind log) — both run in
the bridge process, not in the agent's own shell. An identity's only way
to "send email" is to write `chat reply <conv_id> ...` like it would for
any other channel; it cannot construct a new conversation id for an
address that never emailed it (`outbound.py` drops replies to unknown
conversations), and it has no code path that calls the Resend API
directly. See **Keeping the Resend key away from the agent** below for
how that holds up even though this box has no process sandboxing.

## Multiple agents, one bridge

One bridge process, one Resend webhook, one Cloudflare Tunnel hostname
can serve several identities, each on their own address. List them in a
JSON registry file — by default `.headlong-email-agents.json` at the
serve root (next to `.identities/`), or point `HEADLONG_EMAIL_AGENTS_FILE`
at a different path:

```json
[
  {"identity": "aeon", "address": "aeon@axon.mirrorphysics.com", "admin": "sabrina@mirrorphysics.com"},
  {"identity": "juno", "address": "juno@axon.mirrorphysics.com"}
]
```

`admin` is optional per entry — omit it and it falls back to the shared
`HEADLONG_EMAIL_ADMIN` env var. Each agent gets its own allowlist and
thread store (under `<identity>/run/email-bridge/`, as always), so
approving a sender for `aeon` does not approve them for `juno` — they're
separate trust circles even though they share infrastructure. Inbound
routing picks the agent by matching the webhook's `to` (and
`received_for`) address against this list; mail to an address that isn't
in the registry is logged and dropped, not delivered to anyone.

If you only run one identity, you don't need this file at all — set
`AXON_EMAIL_ADDRESS`/`HEADLONG_EMAIL_IDENTITY`/`HEADLONG_EMAIL_ADMIN` as
before and the bridge builds a single-agent registry from them.

Adding a new agent later: add an entry to the registry, give that
identity its own verified address on the same Resend domain (or a new
one), and restart the bridge — no new webhook or tunnel needed as long as
the address is on a domain Resend already delivers to this bridge.

## 1. Give each identity its own address

Resend's inbound receiving needs its own MX record, and **cannot safely
share a domain with an existing mail provider** — mail only delivers to
whichever MX record has the lowest priority, so pointing Resend's MX at a
domain already served by, e.g., Microsoft 365/Outlook either does nothing
or breaks the existing mailbox. Resend's own docs recommend a dedicated
subdomain for exactly this reason.

This setup uses `axon.mirrorphysics.com` as that dedicated subdomain, with
each identity getting its own address on it (`aeon@axon.mirrorphysics.com`,
`juno@axon.mirrorphysics.com`, ...). These are different addresses from
`axon@mirrorphysics.com` (the existing Outlook mailbox) on purpose —
nothing about that mailbox changes, and there's no forwarding rule to
maintain.

In the Resend dashboard:

1. **Domains → Add Domain** → add `axon.mirrorphysics.com`, not the root
   `mirrorphysics.com` domain.
2. Add the DNS records Resend shows you (MX + the usual sending records:
   SPF/DKIM) at your DNS host, scoped to that subdomain only — this must
   not touch the root domain's existing MX records (Outlook).
3. Once verified, **Webhooks → Add Webhook**:
   - Endpoint URL: `https://<your-tunnel-hostname>/resend/webhook` (set up
     in step 2 below; you can save the webhook with a placeholder and
     update the URL after the tunnel exists).
   - Event: `email.received`.
   - Copy the signing secret (`whsec_...`) — this is `RESEND_WEBHOOK_SECRET`.
4. Every address you're going to hand an identity (`aeon@...`, `juno@...`)
   just needs to exist under this verified domain — Resend doesn't
   require pre-declaring individual mailboxes, any address `@axon.mirrorphysics.com`
   will route to the webhook.

Mail to any address on `axon.mirrorphysics.com` now routes to Resend,
which POSTs the `email.received` event to your webhook URL; the bridge
decides which identity it belongs to from the registry above.

## 2. Create a Resend API key

Reading a received email's body (`GET /emails/receiving/{id}`) appears to
require a `full_access` key — a `sending_access` key can be scoped to
send-only and restricted to one domain, but doesn't have read access to
receiving. That makes splitting into a send-only key and a receive-only
key pointless for now: since the receive side needs `full_access`
regardless, that one key is already the most-privileged kind Resend
issues, so there's nothing gained by having a second, narrower key next
to it. Use one key, `RESEND_API_KEY`, for both:

- **API Keys → Create API Key**, permission **`full_access`**.

Know what that means: this key can act on every domain in the Resend
account it belongs to, not just `axon.mirrorphysics.com` — manage
domains, send from any verified address in the account, read anything.
Consider a Resend account (or sub-organization, if your plan supports
one) dedicated to this identity's domain if that blast radius matters to
you, since there's no way to scope the key itself down further. Whether
this is acceptable is exactly what "Keeping the Resend key away from the
agent" below walks through — the key's own scope isn't the control here,
so the file/process hygiene below is doing all the real work for now.

This key does not belong in `<checkout>/.env` or any file the identity's
own `activate` script sources — see that section.

## 3. Expose the bridge with a Cloudflare Tunnel

The bridge listens on a local port (default `127.0.0.1:8090`); the tunnel
is what gives Resend a public HTTPS URL to call, without opening any
inbound port on the machine running the Docker container.

```bash
# once, on the machine (or inside the container) that will run the tunnel
cloudflared tunnel login                       # picks the Cloudflare zone for mirrorphysics.com
cloudflared tunnel create headlong-email
cloudflared tunnel route dns headlong-email webhook.axon.mirrorphysics.com
```

Write a tunnel config pointing at the bridge's local port:

```yaml
# ~/.cloudflared/config.yml
tunnel: headlong-email
credentials-file: /root/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: webhook.axon.mirrorphysics.com
    service: http://127.0.0.1:8090
  - service: http_status:404
```

```bash
cloudflared tunnel run headlong-email
```

Set the Resend webhook's endpoint URL to
`https://webhook.axon.mirrorphysics.com/resend/webhook` (matching
`HEADLONG_EMAIL_WEBHOOK_PATH`, default `/resend/webhook`).

If you'd rather not stand up Cloudflare for this, any tool that turns a
local port into a public HTTPS URL works the same way (ngrok, a reverse
proxy you already run, etc.) — the bridge doesn't care how it's reached,
only that requests arrive signed.

## Running locally

Single identity, everything from env vars:

```bash
export RESEND_API_KEY=re_...                     # full_access — see "Create a Resend API key" above
export RESEND_WEBHOOK_SECRET=whsec_...           # from the webhook's page in Resend
export AXON_EMAIL_ADDRESS=aeon@axon.mirrorphysics.com
export HEADLONG_EMAIL_ADMIN=sabrina@mirrorphysics.com  # your address; auto-approved on first start
tools/headlong-email-bridge [ROOT]               # ROOT = serve root, default repo root
```

Multiple identities: skip `AXON_EMAIL_ADDRESS`/`HEADLONG_EMAIL_IDENTITY`
and create `.headlong-email-agents.json` instead (see **Multiple agents,
one bridge** above) — everything else is the same.

The launcher loads `<checkout>/.env` and then `$HEADLONG_HOME/.env`
(default `~/.headlong/.env`), the same two files `persona` and `llm`
read, so these can live in an env file instead of the calling shell —
**but use a dedicated file for this bridge, not the shared one**, so the
keys never end up somewhere the identity's own `activate` script loads
them from. See the next section.

The identity must exist (`identity new <name>`) with a running dispatcher
(`thinkers start monolith responder`), and headlong-web must be serving
the same root (default `http://127.0.0.1:8080`, override with
`HEADLONG_WEB_URL`).

Other settings: `HEADLONG_EMAIL_LISTEN_HOST` / `HEADLONG_EMAIL_LISTEN_PORT`
(default `127.0.0.1:8090`), `HEADLONG_EMAIL_WEBHOOK_PATH` (default
`/resend/webhook`), `HEADLONG_EMAIL_AGENTS_FILE` (default
`<serve root>/.headlong-email-agents.json`).

### In a Docker container

Run `tools/headlong-email-bridge` as its own process (or systemd unit)
inside the same container/network as `headlong-web` and the `cloudflared`
tunnel, with the port it listens on (`HEADLONG_EMAIL_LISTEN_PORT`)
reachable from wherever `cloudflared` runs but not published to the host
or the internet — the tunnel is the only path in.

## Keeping the Resend key away from the agent

The key is `full_access` (see above — receiving-read leaves no scoped
alternative for now), so unlike a domain-restricted send-only key, its
own permissions don't cap what happens if the agent ever got hold of it:
a leaked copy could send from any domain in the account, manage domains,
read anything. **File/process hygiene is therefore the only real control
in place right now, and it is not absolute** — worth being clear-eyed
about, not just doing the checklist below and considering it solved.

Structurally, the agent should never need to go looking: an identity's
only way to "send email" is already `chat reply <conv_id> ...`, the same
as Slack or Telegram, handled entirely by the bridge process
(`inbound.py`/`outbound.py`, both in `src/headlong_email/`) — the key is
never in its context, its trajectory, or its environment during normal
operation. What the hygiene below defends against is that the agent's own
bash commands and this bridge's process currently share the same
filesystem and, in a "whole agent in a container" install (as opposed to
the `shellm-docker`-sandboxed option), the same Linux user — so there is
no OS wall stopping a *deliberate* search for the key file if the agent
(via prompt injection or otherwise) went looking. Do this anyway, because
it closes off the accidental/incidental paths entirely, even though it
can't promise more than that:

- Put `RESEND_API_KEY` and `RESEND_WEBHOOK_SECRET` in their own file
  (e.g. `/root/.headlong-email.env`, `chmod 600`), never in
  `<checkout>/.env` or `$HEADLONG_HOME/.env` — those are exactly the
  files the identity's `activate` script, `persona`, and `bin/llm` load
  into the agent's own environment.
- If you can run the bridge as its own OS user (distinct from whatever
  user runs the agent's commands), do that — it's what the Telegram
  bridge does with its bot token, for the same reason. It only helps if
  the agent's own commands *aren't* also running as that same user (root,
  in the "whole agent in a container" install), so check that before
  relying on it.
- Never give the identity a skill or credential that lets it call the
  Resend API itself. It doesn't need one.
- If you later want a real, OS-enforced boundary rather than hygiene,
  the options are: run the agent's own commands sandboxed
  (`shellm-docker`) so this bridge's secrets file is simply outside
  anything mounted into that sandbox, or split this bridge into its own
  Resend account/sub-org dedicated to this domain so even a `full_access`
  key there can't reach anything else.

## The allowlist

Anyone who can guess or find an address can email it, so — like the
Telegram bridge — unknown senders are silently dropped before their text
reaches the mind log, and outbound replies are only sent to approved
addresses. The admin for a given agent gets one notification email per
unknown sender per day, and manages that agent's allowlist by emailing it
back with one of these as the first line of the message body:

- `/approve <address>` lets the sender talk to the identity
- `/deny <address>` silences the request for good
- `/revoke <address>` removes an approved sender
- `/list` shows approved and pending senders

The admin address is approved automatically on first start, for each
agent. Any message from an agent's admin that isn't one of these commands
is delivered to that identity normally.

## Tests

```bash
uv run --project email pytest email/tests
```

## Security notes

Read this before pointing it at an address anyone outside your household
can reach.

- **The webhook signature is the perimeter against forged mail.** Every
  request is verified against `RESEND_WEBHOOK_SECRET` before the body is
  even parsed as JSON; an unsigned or mis-signed request never reaches
  `inbound.py`.
- **The Resend key is `full_access` and not domain-restricted** (see
  "Create a Resend API key" above — receiving-read left no scoped
  alternative). File/process hygiene, not key scope, is carrying the
  weight against it being misused right now — see "Keeping the Resend key
  away from the agent," including the honest limit of what that hygiene
  can and can't promise on a box with no process sandboxing.
- **The allowlist is the perimeter against unwanted senders**, per agent,
  and it gates both directions. Unknown senders never reach the mind log,
  and replies addressed to unapproved or unrecognized conversations are
  dropped, so an injected agent can't use the bridge to send mail to an
  arbitrary address it invents — even with a valid conversation id, it
  can only reply to a real inbound thread, never originate a new one.
- **This bridge listens**, unlike Slack/Telegram. Put a tunnel (Cloudflare
  or otherwise) in front of it rather than a forwarded port, so nothing on
  the host accepts a raw inbound connection — the tunnel client dials out.
- Prompt injection from approved senders is still possible: anything an
  approved person emails, including quoted history a mail client didn't
  strip, goes into the agent's context. `emailfmt.clean_inbound` trims
  common quote markers but isn't a security boundary. The mitigations are
  the same as for Slack/Telegram: a burnable box, spend-capped keys, and
  not telling the identity anything over email you wouldn't post publicly.
- All conversations share one mind log **per identity** — agents
  configured in the same registry do not share a mind log with each
  other, but within one identity, anything one person emails it may
  surface in replies to others on any channel that identity is connected to.
