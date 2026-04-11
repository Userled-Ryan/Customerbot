# Commands

prbot exposes a `/prbot` slash command in Slack for managing configuration. All commands operate on the **current channel's scope** — exclusions and settings are scoped to the channel where the command is run.

## Available commands

### `exclude`

Exclude a GitHub username from triggering PR status emoji updates in this channel.

```
/prbot exclude <github-username>
```

**Examples:**

```
/prbot exclude Cursor
/prbot exclude dependabot[bot]
```

Events from excluded users are silently skipped — no emoji reactions are added or updated for their PR activity.

---

### `include`

Re-include a previously excluded GitHub username.

```
/prbot include <github-username>
```

**Example:**

```
/prbot include Cursor
```

---

### `list-exclusions`

Show all excluded GitHub usernames for this channel.

```
/prbot list-exclusions
```

Alias: `/prbot exclusions`

---

### `config`

Show the full configuration for this channel, including emoji settings and excluded users.

```
/prbot config
```

**Example output:**

```
Scope: slack/T123ABC/C456DEF
Excluded users: `Cursor`, `dependabot[bot]`
Emoji config:
  merged: git-merged
  closed: headstone
  approved: git-approved
  changes requested: git-changes-requested
  commented: speech_balloon
```

---

### `help`

Show the list of available commands. This is also shown when an unrecognised command is entered.

```
/prbot help
```

## Scope resolution

Commands always operate on the **most-specific scope** for the channel they're run in. The scope key format is:

```
<integration>/<workspace_or_guild>/<channel>
```

For example, running `/prbot exclude Cursor` in the `#deploys` channel of Slack workspace `T123ABC` stores the exclusion at scope `slack/T123ABC/C456DEF`.

When checking whether a user is excluded, prbot walks from most-specific to least-specific:

```mermaid
flowchart TD
    A["slack/T123ABC/C456DEF"] -->|not found| B["slack/T123ABC"]
    B -->|not found| C["slack"]
    C -->|not found| D["Not excluded"]
    A -->|found| E["Excluded ✓"]
    B -->|found| E
    C -->|found| E
```

This means a workspace-level exclusion (e.g. `slack/T123ABC`) applies to all channels within that workspace.

## Slack app setup

To use slash commands, your Slack app needs the `commands` scope and a configured slash command:

1. Go to your app settings at [api.slack.com/apps](https://api.slack.com/apps)
2. Navigate to **Slash Commands** and click **Create New Command**
3. Set the command to `/prbot`
4. Set the Request URL to `https://your-domain.com/slack/events`
5. Add a description (e.g. "Manage prbot configuration")
6. Save and reinstall the app to your workspace

!!! note
    The slash command shares the same `/slack/events` endpoint as the event subscriptions — Slack bolt routes them internally.
