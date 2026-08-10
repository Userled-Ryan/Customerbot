---
name: open-customer-questions
description: Scan all customer Slack channels (+ #userled-support) for genuinely UNANSWERED customer questions an SE needs to action, and return a ranked to-do list with links. Use when someone asks "what do we need to look at / reply to", "any unanswered customer questions", "triage the customer channels", "what's outstanding across customers", or wants a to-do list of open customer issues across Slack. Optional args: a time window (default last 3 weeks) and/or a specific customer/channel to narrow to.
---

# Find unanswered customer questions across the Slack channels

Goal: as Solutions Engineers we want to reply to every customer **fast and on
point**. This skill sweeps every customer Slack Connect channel (plus
`#userled-support`) and returns a short, ranked list of **what actually needs a
reply** — nothing that's already been answered.

It runs on request. If you'd rather have it **pushed** on a schedule, see
[Turning this into a Slack alert](#turning-this-into-a-slack-alert) at the end.

## The one rule that matters (read this first)

A question is **UNANSWERED only if a customer asked something substantive AND no
Userled reply actually addresses it.** Everything else follows from this.

The easy mistake — and the reason a naive scan is wrong — is to flag a thread just
because the **last message is from the customer**. It usually isn't unanswered: the
trailing message is a *"thanks"*, *"will do"*, *"confirm on your end?"*, or a new
sub-point on something already handled. So:

- ❌ Do **not** decide from the last-message author or a channel skim.
- ✅ **Open the thread** (`slack_read_thread`) and read it end-to-end before judging.
- **Questions live inside threads, not just at the top.** A customer often raises a
  *new* question as a thread reply — sometimes buried under an earlier item that
  *was* resolved. Scan every reply, not only the parent. If a customer question
  anywhere in the thread never got a Userled reply that addresses it, it's open —
  even if later messages in the same thread moved on to something else.
- A customer *"thanks / confirm on your end / sounds good"* after Userled already
  answered → **answered**, don't flag.
- A genuinely **new** question raised after prior answers, with no Userled reply →
  🔴 **unanswered**.
- A real but soft/advisory follow-up that got no reply (*"do you also recommend
  X?"*) → 🟡 **open follow-up** (list separately, lower priority).
- Reactions-only, greetings, resolved scheduling, internal Userled chatter → ignore.

### ⭐ Top priority: direct tags to an SE

If an unanswered question **@-mentions Ryan or Elizaveta** (or whoever is running
the scan), it's a direct ask to us and jumps to **number one priority** — mark it
⭐ and put it at the very top of the 🔴 list, regardless of age or channel. The
Slack IDs to watch for in message text (`<@U…>`):

- **Ryan Hennessy** — `U08AL6BAAQN`
- **Elizaveta Makarina** — `U0BEZCALK0E`

(A tag counts even if it sits mid-thread. If the tagged SE already replied and
addressed it, it's answered like anything else — the tag only bumps *unanswered*
items.)

### Who is "the customer" vs "Userled"

These are Slack Connect channels, so attribution is subtle:

- **Userled staff** render with a name **and `@userled.io` email**, e.g.
  `Ryan Hennessy <ryan@userled.io>`.
- **Customer** messages usually render with a **blank sender/From** (external names
  are hidden by Slack Connect). Treat blank-sender messages as the customer.
- `UserledSupport` (the customerbot bot) is Userled. A `:ticket:` reaction means it
  was logged as a ticket; `:white_check_mark:` means resolved — but a ticket being
  *logged* is **not** the same as the customer's question being *answered in-thread*.
  Judge by whether a human actually addressed the ask.

Known Userled staff names (helper, not exhaustive): Oliver Grogan, Marcus, Jennifer
McLeod, Yann, Emily, Ryan Hennessy, Liam O'Cuinn, Charlotte Stone, Tristan Saunders,
Alexa, Ayse, Geneviève, Clare Labron, Olivia Fossali, Mikkel Nielsen, Vincent, Shane
Thomas, Elizaveta Makarina, Lucy, Rich Catley, Archie Croft, Isla Theobold, David,
Elliot Roberts, Yves Adam.

## Inputs (optional)

- **Window** — how far back to look. Default **21 days**. Anything older than the
  window that's still unanswered is usually stale, but the user can widen it.
- **Scope** — a single customer/channel (e.g. "just Didomi") to narrow the sweep.

## Steps

### 1. Load the Slack tools

They're deferred MCP tools — load the schemas before calling:

```
ToolSearch: select:mcp__claude_ai_Slack__slack_search_channels,mcp__claude_ai_Slack__slack_read_channel,mcp__claude_ai_Slack__slack_read_thread,mcp__claude_ai_Slack__slack_read_user_profile
```

### 2. Build the channel list

Customer channels follow the naming `#userled-<customer>`, `#ext-userled-<customer>`,
`#ext-<customer>-userled`, and older `#userled-io-<customer>`. New ones are created
often, so **rediscover each run** rather than trusting a stale list:

```
slack_search_channels query="userled"  (paginate ALL pages via the cursor)
slack_search_channels query="ext"       (catches #ext-… Connect channels)
```

Then filter to real **customer** channels and **exclude** internal / sales / partner
/ ops channels: `#customer-*` (internal), `#userled-techmunity`, `#userled-community`,
`#userled-goodpath-sales`, `#assistant-userled`, `#userled-alerts` (bot noise),
`#userled-ml`, `#userled-surveys`, `#userled-pgai`, `#userled-1600`,
`#userled-jarryd-seo`, `#userled-partner_vista`, `#partnership-userled-fullenrich`,
`#dealfront-userled-partnership`, `#csdr-userled`, `#gleap-support-channel`,
`#customerbot_testing`. Also skip `#userled-contentpath` and `#userled-mastra`
(reversed relationships — Userled is the customer there). A cached list of the
current customer channels is in the [appendix](#appendix-cached-customer-channels)
to speed things up, but confirm it against fresh search results.

Always include **`#userled-support`** (`C0ARYPD3E5A`) — see step 5.

### 3. Fan out the scan in parallel

There are ~120 channels; a serial sweep is slow and burns context. Split them into
batches of ~15 and dispatch one `general-purpose` subagent per batch **in a single
message** so they run concurrently. Give every subagent:

- the **one rule** and the **customer-vs-Userled** guidance from above (verbatim —
  it's the whole point),
- its batch as a `name -> channel_id` list,
- the window date,
- instruction: `slack_read_channel limit=25` per channel; if the newest message is
  older than the window, mark **"no recent activity"** and skip; otherwise **open
  every thread with `slack_read_thread` and read all replies** — a customer question
  can be raised mid-thread, not just at the top — and verify whether Userled
  addressed each one; minimize `slack_read_user_profile`,
- instruction: if a message text contains `<@U08AL6BAAQN>` (Ryan) or
  `<@U0BEZCALK0E>` (Elizaveta) and it's unanswered, mark it **⭐ TOP** so it can be
  ranked first,
- the output format from step 6.

Have each subagent return **🔴 unanswered** and **🟡 open follow-ups** sections plus
a one-line status (which channels were dormant / active-but-answered).

### 4. Reconcile the batch results

Subagents occasionally miss a fresh trailing message or misread a thread. Before
reporting:

- **Spot-check every 🔴** by opening the thread yourself — this is cheap and it's
  where a wrong call hurts (either nagging an SE about a resolved thread, or a
  confident "all clear" that misses a real one).
- If a batch **died mid-run** (API error / partial output), re-dispatch just that
  batch. Don't silently drop it.

### 5. Check `#userled-support`

This is the internal intake channel where SEs/staff log customer bugs and friction;
customerbot (`UserledSupport`) triages them. Read it (`slack_read_channel limit=30`)
and flag messages in-window that have **no thread replies and no `:ticket:` /
`:white_check_mark:` reaction** — i.e. logged but not yet triaged/acknowledged.
Note these are staff-logged, so present them separately from customer-channel items.

### 6. Report — a tight, actionable to-do list

Rank most-urgent first. Ordering: **⭐ items that tag Ryan or Elizaveta come first**
(direct asks to us), then the rest of 🔴 by recency + stated urgency + customer tier.
For each item:

> **#channel** — who asked · date — one-line summary of the *still-open* question —
> [link]

Build permalinks from the **parent-thread ts**:
`https://userled-io.slack.com/archives/<channel_id>/p<ts-with-the-dot-removed>`
(e.g. ts `1785861707.034129` → `.../p1785861707034129`).

Sections: **🔴 Unanswered** (needs a reply), **🟡 Open follow-ups** (loose ends),
**#userled-support** (untriaged logs), then a one-line coverage note (window used,
how many channels were dormant, anything you couldn't verify). Keep it short — an SE
should be able to act off it in seconds. Offer to draft replies for the 🔴 items.

## Notes & caveats

- **Precision over recall for 🔴.** A false "unanswered" makes the tool noisy and
  SEs stop trusting it. When unsure whether Userled addressed something, open the
  thread; if still genuinely ambiguous, put it in 🟡, not 🔴.
- **Blank-sender = customer** is the single most important attribution cue in Connect
  channels; the staff-name list is only a backstop.
- **Window default 21 days.** Genuinely unanswered questions older than that are
  rare and usually stale; mention any that sit just outside the window rather than
  silently dropping them.
- **Ticket logged ≠ answered.** A `:ticket:` reaction means it entered the pipeline,
  not that the customer got a reply. Judge the in-thread conversation.

## Turning this into a Slack alert

If you want this **pushed** (e.g. a daily 9am digest in an SE channel) instead of
on-request, the same methodology can run as a scheduled job:

- **Fastest path:** use the `/schedule` skill (or `/loop`) to run this skill on a
  cron and post the report to a channel with `slack_send_message`. No code changes.
- **In customerbot proper:** add a scheduled task in the app that reuses the Slack
  client to sweep the customer channels and post a digest. Heavier, but it survives
  without a Claude session running. Keep the "answered only if Userled addressed it"
  rule — a push alert that cries wolf is worse than none.

Recommendation: start with the on-request skill (this file). Add the scheduled
digest only once the signal quality feels right, so the alert doesn't become noise.

## Appendix: cached customer channels

Snapshot as of **2026-08-10** — a starting point; re-run the search in step 2 to pick
up new channels and drop churned ones.

```
userled-bolt C0BPASB9CSC | userled-gocardless C0BLEULRVGC | userled-switchtt C07VB66UD8B
userled-pace C0B5A09A4M8 | userled-alertmedia C09R09YSC79 | userled-commerce C0AJXPESKKN
userled-8am C085Q5K0PCZ | userled-starburst C0AUXSCGJ2G | userled--unframe C0BF2U6EJBA
userled-cognite C0AD4CZ3X25 | userled-popp C0BMYSU9QSD | userled-spring-health C0B7CGMEJL8
workos-userled C0746PTL6JZ | userled-synthesia C0A9SJTD6AZ | userled-chainguard C0BM2275D36
userled-parloa C0BJN7ZTTT6 | userled-elastic-professional-services C0AC0Q2QBEC
userled-ironclad C0AEXTX1S1E | userled-salesloft C0B089REB3Q | userled-beavr C089N0B4SE4
userled-siteminder C0APH4NEC3D | userled-arbor C0A25HWV40N | userled-tipalti C0B1BPJ5X39
ext-userled-stripe C0A27PU5MMW | userled-commercetools C09T9MPGHMZ | userled-vibe_co C0ARNS387CH
userled-docebo C0AK6KTDASD | userled-ramp C09UL7V93T2 | userled-secfense C08L22CT3PS
userled-fingerprint C0A72RN2GFR | userled-adquick C084QEK6T40 | userled-elliptic C09F39KFHEU
userled-didomi C0ASPAN8A3F | userled-zylo C0A926YURTJ | userled-pinpoint C09SSLFL2L8
userled-onsecurity C079HV9V2MP | userled-concirrus C09UA3UMF16 | userled-telgorithm C087BJKHGR5
userled-labelbox C07KP9UFPM5 | userled-qobra C070VGAV6HE | userled-goodpath C09PNQ4U2BX
userled-resonate C0BLL4ZRGMT | userled-elastic C07RDMC3UGY | userled-kontor C04BXRR84PR
ext-userled-datadog C0A80G68BJM | userled-kpler C094MN2B0AK | userled-yoursitehub C06GW6JKVU1
userled-hootsuite C0AM4DSCW9L | userled-maze C058A8VUNS1 | userled-gupshup C07E0H5AYBU
userled-tailscale C0BA8DQKEM6 | userled-veremark C06Q1NR3C7M | userled-advendio C09001MCGD9
userled-maki C0991V9NPBL | userled-deepl C0AKZUY30US | userled-lightspark C08L7P1EZB7
userled-moss C07L3953GP8 | userled-nplan C07C1D4NZPU | userled-redgate C0A4JE6B0FP
userled-liberis C0ADHE1R5QQ | userled-omnea C04D5REUF08 | userled-xelix C061VLL4K5H
userled-metronome C0ARD9WPXGS | userled-pigment C077YRLC362 | userled-corporate_vision C0A43Q0UCNA
userled-seon C04BKFGGGDQ | userled-adlib C08KCM753DJ | userled-commercetools-tech C09U4LD5LG4
userled-accountable C05DF1JB04A | userled-wayflyer C06L3HGMW9Z | userled-teamed C072Y7LS7N3
userled-encord C073D8XQNA2 | userled-kong C0AV9JRKB5F | userled-affinity C07LLC2D9NF
userled-cloudtalk C053QU44EPR | userled-incognia C07TYTD7Z4J | userled-bloomreach C09J23HQUUV
userled-wilsonbrown C0A9AGT29TN | ext-userled-google C0APP78RBMJ | ext-demandbase-userled C09E5UX7FDE
ext-userled-vercel C0ASGGZ3Q2W | userled-hubifi C0BGAJM3U48 | userled-findr C07L71VGCDS
userled-zoominfo C0A7RNCQXD3 | userled-wisq C089TL2BB2A | userled-moda C058Y3FAG4R
userled-netnut C08Q7QX0JKS | userled-captaindata C09RU7STR6V
# older userled-io-* customers: wearedragonfly C04AZ510A5V, comtura C0428S2RGQY,
# futurehead C048RUJNVEK, rosieland C04845FRE6L, cord C03L94HHL2C, paradime C06UUNB95E0,
# kana C03MKC882QP, gitguardian C046CDB9Q5C, hero C03JDUC832A, attest C05U4PE2R2S,
# balance C03LV6H1CHM, zencargo C044CGWD8PN, talent-io C03NX9XDGKF
# #userled-support (internal intake): C0ARYPD3E5A
```
