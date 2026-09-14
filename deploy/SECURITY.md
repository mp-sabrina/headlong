# Security posture of the chat integrations

This doc summarizes how each way of talking to a Headlong identity is
secured. It covers the Slack bridge (`slack/`), the phone chat PWA
(`web/`, served behind Cloudflare Access), the Telegram bridge
(`telegram/`), and the email bridge (`email/`). Read
this before widening access to any of them.

## What they all share

The identity is an agent that runs arbitrary bash on its box with its
API keys, and every message a person sends goes straight into the
agent's context. Prompt injection is therefore always possible, from any
channel, and the defenses are the same everywhere:

- The box is dedicated and burnable. Slack, Telegram, and the phone chat
  PWA keep zero inbound network access — they dial out (a Socket Mode
  websocket, Telegram long polling, and a Cloudflare tunnel). The email
  bridge is the one exception: Resend pushes inbound mail in over HTTP,
  so it listens — see its own section below for how that's contained.
- The LLM key is dedicated to the box and spend capped.
- The box holds only the secrets it needs, because it allows all
  outbound traffic and an injected agent could send those secrets out.
- All conversations share one mind log. Anything one person tells the
  identity may surface in replies to anyone else, on any channel. Do
  not tell it secrets you would not post in a public channel.

Each channel is one message namespace in the mind log (`slack-*`,
`pwa-*`, `telegram-*`, `email-*`). Each bridge only forwards replies
addressed to its own namespace, so the channels cannot leak into each
other's transport, though the shared mind means content can still cross.

Each bridge has a kill switch that mutes the channel without touching
the agent. For Slack it is `systemctl stop headlong-slack-bridge`, for the
phone chat it is disabling the Cloudflare Access app, for Telegram it is
`systemctl stop headlong-telegram-bridge`, and for email it is
`systemctl stop headlong-email-bridge` (or pausing the Cloudflare Tunnel
in front of it, which achieves the same thing without touching the
bridge's own state).

## Slack

Who can talk to the identity. Anyone in the Slack workspace the app is
installed in, by DM or by mention. Workspace membership is the only gate, and the
bridge does not have its own allowlist.

How it connects. The bridge holds two long-lived Slack tokens and opens
an outbound Socket Mode websocket. The tokens live in the box's root
`.env`, which comes from an SSM parameter and survives rebuilds.

Known gaps. The root `.env` is readable by the `shellm` user, which is
the user the agent runs as, so an injected agent can read the Slack
tokens and post as the bot anywhere the bot is installed. The Telegram
bridge avoids the same gap by design (see below), and moving the Slack
tokens out of the shared `.env` the same way would close it.

## Phone chat PWA

Who can talk to the identity. Only people who pass the Cloudflare Access
app in front of the chat domain. Access requires Google SSO or a one
time code, and the allowlist is the operator's email plus, optionally,
an email domain (`allowed_emails` / `allowed_email_domains` in the terraform
stack). A second, path-scoped Access app bypasses login only for the app
manifest and icons, which Android fetches without cookies during
install.

How it connects. The box reaches Cloudflare through an outbound tunnel.
Messages arrive over the same web API the bridges use, under `pwa-*`
sender names that the Slack bridge never forwards.

Push notifications. The push subscription store and the VAPID keys live
on the box, and only `pwa-*` names can subscribe. The keys die with the
box, and phones resubscribe on the next launch.

Known gaps. If an email domain is allowed, anyone with an account in
that domain can reach the chat; that is the intended trust circle. The push files are readable by the
agent's user, but they only allow sending notifications to subscribed
phones, not reading anything.

## Telegram

Who can talk to the identity. Only users on the bridge's allowlist.
Anyone on Earth can message a Telegram bot, so the bridge drops unknown
senders before their text reaches the mind log, and it stays silent
toward them so probing the bot confirms nothing. The admin approves or
denies senders from their own Telegram chat, and gets at most one prompt
per unknown sender per day.

The allowlist gates both directions. Replies addressed to unapproved
users are dropped, so an injected agent cannot use the bridge to carry
data out to an arbitrary chat.

Scope limits. The bridge is DM only. It leaves any group it is added
to, because the allowlist cannot control who is in a group. Inbound
media is dropped, so nothing gets downloaded onto the box. Outbound
file steps (`chat send-file`) are uploaded as Telegram documents or
photos; that is agent-to-user, not a download path.

How it connects. The bridge long polls the Telegram API outbound. The
bot token lives in `/etc/shellm/telegram.env`, which is root owned with
mode 600, and only systemd reads it. The bridge runs as its own user
(`shellm-telegram`) rather than as the agent's user, because processes
with the same uid can read each other's environment. The allowlist and
cursors live in `/var/lib/shellm-telegram`, which the agent's user
cannot write, so an injected agent cannot approve an attacker.

Known gaps. The env file does not survive an instance rebuild and must
be recreated by hand. A leaked bot token would let an attacker
impersonate the bot to approved users and race the bridge for incoming
messages. Telegram bot chats are not end to end encrypted, so Telegram's
servers see all content. There is also a `skills/telegram` skill that
teaches the agent to drive the Telegram API with curl. Giving the agent
the bridge's bot token would undo the token isolation, so if the agent
should have Telegram access of its own, use a separate bot and token.

## Email (Resend)

One bridge process can serve several identities, each with its own
address, admin, and allowlist (a registry file maps addresses to
identities — see `email/README.md`). Who can talk to a given identity:
only senders on *that identity's* allowlist, managed the same way as
Telegram's: unknown senders are dropped silently before their text
reaches the mind log, and the admin gets at most one notification per
unknown sender per day, approving or denying by emailing the bridge back
with `/approve <address>` or `/deny <address>`. Allowlists (and admins)
are per identity — being approved to talk to one doesn't approve you for
another, even though they share infrastructure. Mail to an address that
isn't in the registry at all is dropped before it's associated with any
identity.

The allowlist gates both directions. Replies addressed to an unapproved
or unrecognized conversation are dropped, so an injected agent can't use
the bridge to mail an arbitrary address it invents — it can only reply
within a thread that a real inbound email already started.

**The identity never touches Resend.** The bridge process is the only
thing that constructs a Resend API call, in exactly two places (fetch a
received email's body; send a reply `bin/chat` already wrote to the mind
log). An identity's only way to "send email" is the same as any other
channel: write `chat reply <conv_id> ...`. It has no skill or credential
that calls Resend directly, and no path to invent a new conversation to
mail out to.

How it connects — the one bridge here that listens. Resend delivers
inbound mail as an HTTP POST (`email.received` webhook), not a pull, so
unlike the other three this bridge must accept a connection. Two layers
contain that:

- Every request is verified against `RESEND_WEBHOOK_SECRET` (Svix-style
  HMAC over the raw body, with a timestamp tolerance against replay)
  before the body is even parsed as JSON. An unsigned or mis-signed
  request is rejected with no effect. This is the bridge's real
  perimeter against forged "mail."
- The listener itself should never be a port forwarded to the internet.
  The recommended setup fronts it with a Cloudflare Tunnel (the same
  outbound-dial primitive the phone chat PWA uses) or an equivalent, so
  the box still makes no inbound network commitment — the tunnel client
  dials out, and the box only ever talks to the tunnel.

The identity's mail address lives on its own subdomain (e.g.
`axon.mirrorphysics.com`), deliberately separate from any existing mail
provider on the root domain — Resend's inbound MX can't safely coexist
with, e.g., an Outlook-hosted mailbox on the same domain, and using a
distinct subdomain means no other mail flow on the domain is touched.

Credential scope — a known, accepted gap for now. A Resend account can
hold many domains, and the bridge's key is `full_access` and can act on
all of them — far more than "this identity can reply to people who email
it." A domain-restricted, send-only key would cap that, but reading
received-email bodies appears to require `full_access`, which leaves no
scoped alternative to fall back to today; splitting into two keys would
just mean the second one is the same unscoped risk. The control this
bridge relies on instead is file/process hygiene — the key lives in its
own root-owned file, never in the agent's own env — which is real but not
absolute: a box that runs the agent's own commands unsandboxed (the
"whole agent in a container" install path, as opposed to `shellm-docker`)
has no OS boundary stopping a deliberate, adversarial search for that key
file by a process with the same privileges. See `email/README.md`'s
"Keeping the Resend key away from the agent" for the full reasoning and
the options for closing this gap later (a sandboxed agent, or a
Resend account dedicated to this domain).

Known gaps. The webhook secret and the Resend API key are bridge secrets
the same way the Telegram bot token is; keep them out of the agent's own
environment (root-owned env file, separate service user if the
deployment allows one), not the shared `.env`. Email is not end-to-end
encrypted and passes through Resend's and the tunnel provider's
infrastructure. Quoted reply history a mail client didn't strip is only
trimmed heuristically (`emailfmt.clean_inbound`), not filtered as a
security boundary — treat everything in an approved
sender's message as untrusted input, same as any other channel.

## Comparison

| | Slack | Phone chat PWA | Telegram | Email |
|---|---|---|---|---|
| Gate | Workspace membership | Cloudflare Access (SSO) | Bridge allowlist | Bridge allowlist |
| Who holds the gate | Slack admins | Cloudflare config | Admin over Telegram | Admin over email |
| Secrets on box | Bot + app tokens | VAPID keys | Bot token | Resend key (full_access) + webhook secret |
| Agent can read them | Yes (shared `.env`) | Yes (push files) | No (root-owned env, separate user) | No (root-owned env, separate user) |
| Outbound reply check | None | Not needed (pull) | Allowlist | Allowlist |
| Survives rebuild | Yes (SSM parameter) | Keys regenerate | No (recreate env file) | No (recreate env file) |
| Kill switch | Stop bridge unit | Disable Access app | Stop bridge unit | Stop bridge unit / pause tunnel |
| Listens for inbound | No (Socket Mode) | No (tunnel, pull) | No (long poll) | Yes (webhook, behind tunnel + signature check) |
