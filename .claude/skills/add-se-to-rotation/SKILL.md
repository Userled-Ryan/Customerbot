---
name: add-se-to-rotation
description: Add a Solutions Engineer to customerbot's ticket round-robin. Use when a new SE joins and should start receiving auto-assigned tickets. Takes the SE's Slack user ID (and optionally their Linear user UUID), then appends them to the CUSTOMERBOT_SE_OWNER_USER_IDS Fly secret (and the Linear user map) on the live app.
---

# Add an SE to the ticket rotation

New tickets are auto-assigned to an SE by a **balanced round-robin** — each ticket
goes to the pool member with the fewest currently-open tickets (see
`SubmitTicketForm._pick_se_owner`). The pool is the Slack user IDs in the env var
**`CUSTOMERBOT_SE_OWNER_USER_IDS`** (a JSON list), which also drives the *SE owner*
dropdown on every ticket card.

So adding an SE to the rotation = appending their Slack `U…` id to that env var.
On Fly it's a **secret**, so this restarts the machine (a few seconds of downtime).
The rotation only kicks in with **≥2** members; with 0 or 1 it falls back to the
single default SE (`CUSTOMERBOT_SE_USER_ID`, Ryan).

For the new SE's Linear issues to be assigned (not left unassigned), they must also
appear in **`CUSTOMERBOT_LINEAR__USER_MAP`** (Slack id → Linear user UUID).

The Fly app is **`customerbot-userled`**. `fly` must be authenticated locally
(`fly auth whoami`; else `fly auth login`).

## Inputs

Required (ask the user if missing):

- **Slack user ID** — the SE's `U…` id (not `@name`). If given a name, resolve it
  with `slack_search_users`.

Optional but recommended:

- **Linear user UUID** — the SE's Linear user id, so tickets assigned to them mirror
  onto the Linear issue. Without it they still rotate in Slack, but their Linear
  issues land unassigned. (Find it in Linear: Settings → Members, or the GraphQL
  `users` query.)

## Why read-before-write

`fly secrets list` shows only a **digest**, never the value — so you cannot see the
current list that way. Read the live value from inside the running machine instead:

```bash
fly ssh console -a customerbot-userled -C "printenv CUSTOMERBOT_SE_OWNER_USER_IDS"
```

This prints the current JSON list (e.g. `["U08AL6BAAQN","U0BEZCALK0E"]`). If it
prints nothing, the var is unset and the pool is just the single default SE — in
that case seed it with **both** the default SE and the new one.

## Steps

1. **Collect inputs.** You need the Slack `U…` id. Resolve from a name with
   `slack_search_users` if needed. Ask for the Linear UUID too (recommended).

2. **Read the current list** with the `printenv` command above. Parse the JSON.

3. **Append the new id** if not already present (idempotent — if it's already
   there, report that and stop). Keep existing members, including the default SE.

4. **Validate**: the resulting list has ≥2 members and still contains the default
   SE id (`CUSTOMERBOT_SE_USER_ID` — read it the same way via `printenv` if unsure).
   If the previous list was empty, seed with `[<default SE>, <new>]`.

5. **Confirm with the user** — show the before/after list. Note this writes to the
   **live production** app and restarts it.

6. **Set the secret** (this restarts the machine):

   ```bash
   fly secrets set -a customerbot-userled \
     CUSTOMERBOT_SE_OWNER_USER_IDS='["U08AL6BAAQN","U0BEZCALK0E","<new>"]'
   ```

7. **If a Linear UUID was given**, do the same read/extend/set for the user map:

   ```bash
   fly ssh console -a customerbot-userled -C "printenv CUSTOMERBOT_LINEAR__USER_MAP"
   # merge {"<slack U…>": "<linear uuid>"} into the JSON object, then:
   fly secrets set -a customerbot-userled \
     CUSTOMERBOT_LINEAR__USER_MAP='{"U08AL6BAAQN":"<uuid>","<new>":"<uuid>"}'
   ```

   (Setting both secrets in one `fly secrets set` call — pass multiple `KEY=val`
   pairs — avoids two restarts.)

8. **Verify.** After the restart, `fly secrets list -a customerbot-userled` shows a
   new digest/timestamp for the changed secret(s). Confirm the app came back
   healthy (`fly status -a customerbot-userled`). The new SE now appears in the
   *SE owner* dropdown and starts receiving round-robin assignments.

## Notes & caveats

- **Restart, not just a config reload.** Unlike the customer-org dropdown (DB-driven,
  no restart), the SE pool is an env var, so `fly secrets set` bounces the machine.
- **Balanced, not strict order.** New SEs with zero open tickets get assigned first
  until they catch up to the rest of the pool — that's expected.
- **Removing an SE** is the same flow in reverse: drop their id from the list and
  `fly secrets set`. Existing tickets they own are untouched; reassign those from
  the card dropdown if needed.
- **Linear-only vs Slack-only.** `SE_OWNER_USER_IDS` controls the rotation and the
  Slack dropdown; `LINEAR__USER_MAP` only controls whether the Linear issue gets an
  assignee. Keep them in sync so a rotated SE isn't unassigned in Linear.
