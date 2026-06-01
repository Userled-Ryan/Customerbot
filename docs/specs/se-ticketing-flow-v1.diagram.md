# SE Ticketing Flow — v1 Diagrams

Mermaid replacement for the v0 Figma diagram. Paste into Notion or any Mermaid renderer.

Companion to `se-ticketing-flow-v1.md` (canonical spec).

---

## 1. End-to-end flow

```mermaid
flowchart TD
    %% Intake sources
    A1[Customer channel msg]
    A2[#tech-assistance /log-ticket]
    A3[DM /log-ticket]
    A4[In-app bug submission]

    %% Trigger
    A1 -->|SE replies with 'log' or 'check'| B[CustomerBot DMs SE pre-filled form]
    A2 --> C[CSM intake form]
    A3 --> B
    A4 --> D[Direct ticket creation, org+user+screenshot+replay auto-attached]
    B -->|SE submits within 30min| E
    B -.->|no submit in 30min| X1[Silently dropped]
    C --> E[Dedupe check]
    D --> E

    %% Dedupe
    E -->|live match| E1[Bot suggests merge → SE confirms]
    E -->|no match| E2[Create new ticket]
    E1 --> F
    E2 --> F[Bot suggests priority from matrix]

    %% Priority + classification
    F --> G{Type?}
    G -->|Bug| H[Bug: SE Action lane, Status: New]
    G -->|Config| I[Config ticket, Status: New]
    G -->|FAQ| J[FAQ ticket, Status: New]

    %% Bug branch
    H --> H1[SE investigates]
    H1 --> H2{Resolvable by SE?}
    H2 -->|Yes - small fix| H3[SE Action: In progress]
    H2 -->|No - dev change needed| H4[Move to Dev Action lane, ping @support]
    H4 --> H5[Dev Action: In progress]
    H3 --> H6{Fix type?}
    H5 --> H6
    H6 -->|Full fix shipped| K
    H6 -->|Hotfix only| H7[Mark 'Resolved via hotfix']
    H7 --> H8[Auto-create linked underlying-bug ticket, Dev Action board]
    H7 --> K

    %% Config branch
    I --> I1[SE allocates time / schedules / starts work]
    I1 --> I2[In progress]
    I2 --> I3{Customer-side blocked?}
    I3 -->|Yes| I4[Awaiting customer - paused]
    I3 -->|No| K
    I4 --> I2

    %% FAQ branch
    J --> J1{Article exists?}
    J1 -->|Yes, sufficient| J2[SE drafts reply, links article]
    J1 -->|Needs update or new| J3[Create linked Article task on Article board]
    J2 --> K
    J3 --> J2

    %% Convergence: customer confirmation
    K[Awaiting customer confirmation]
    K -->|Customer confirms ok| L[Resolved]
    K -->|Customer says still broken + new info| H1
    K -->|7d silence| M[Auto-close with note]
    L --> N[Closed]
    M --> N

    %% Reopen
    N -.->|Customer returns within 30d| H1
    N -.->|Customer returns after 30d| O[New ticket linked to old]

    %% Reclassification can happen any time
    H -.->|Reclassify| G
    I -.->|Reclassify| G
    J -.->|Reclassify| G

    %% Affected-customer additions
    H -.->|Another customer hits same| E1

    classDef intake fill:#fef3c7,stroke:#d97706
    classDef bot fill:#dbeafe,stroke:#2563eb
    classDef bug fill:#fee2e2,stroke:#dc2626
    classDef config fill:#dcfce7,stroke:#16a34a
    classDef faq fill:#f3e8ff,stroke:#9333ea
    classDef terminal fill:#e5e7eb,stroke:#374151

    class A1,A2,A3,A4 intake
    class B,C,D,E,F bot
    class H,H1,H2,H3,H4,H5,H6,H7,H8 bug
    class I,I1,I2,I3,I4 config
    class J,J1,J2,J3 faq
    class N,O,M terminal
```

---

## 2. Ticket lifecycle (state machine)

```mermaid
stateDiagram-v2
    [*] --> New
    New --> InProgress: SE picks up
    InProgress --> AwaitingConfirm: Fix/answer delivered
    InProgress --> AwaitingConfirm: Hotfix applied
    AwaitingConfirm --> InProgress: Customer reports still broken + new info
    AwaitingConfirm --> Resolved: Customer confirms
    AwaitingConfirm --> Closed: 7d silence → auto-close
    Resolved --> Closed
    Closed --> InProgress: Customer returns within 30d (reopen)
    Closed --> [*]: After 30d (new linked ticket)

    state InProgress {
        SEAction --> DevAction: Escalate to @support
        DevAction --> SEAction: @support pushes back / SE picks up
    }
```

---

## 3. Bug-specific: hotfix fork

```mermaid
flowchart LR
    A[Bug ticket: In progress] --> B{Fix type?}
    B -->|Full fix shipped| C[Resolved]
    B -->|Hotfix only - customer unblocked| D[Resolved via hotfix]
    D --> E[Original ticket → Awaiting confirmation → Closed]
    D --> F[Auto-create Underlying bug ticket]
    F --> G[Dev Action lane, prio capped at P2, owner = @support]
    G --> H[New customer reports same? → dedupe into G + bump prio]
    G --> I[Permanent fix shipped → Resolved → Closed]
```

---

## 4. Intake decision tree

```mermaid
flowchart TD
    A[Query surfaces somewhere] --> B{Where?}
    B -->|Customer Slack channel| C{Internal member responds with 'log' or 'check'?}
    B -->|#tech-assistance| D[CSM uses /log-ticket → form]
    B -->|DM to SE| E[SE invokes /log-ticket → form]
    B -->|In-app| F[Webhook fires → auto-create]
    C -->|Yes| G[Bot DMs SE pre-filled form]
    C -->|No| H[Not logged - bot stays silent]
    G --> I[SE submits within 30min]
    G --> J[Silently dropped if no submit]
    D --> K[Ticket created]
    E --> K
    F --> K
    I --> K
```

---

## 5. Reclassification flow

```mermaid
sequenceDiagram
    participant SE as SE or @support
    participant Bot as CustomerBot
    participant Stakeholders as CSM owner + original logger

    SE->>Bot: Click 'Reclassify' on ticket
    Bot->>SE: Open reclassify modal (new type, reason, next step, owner)
    SE->>Bot: Submit
    Bot->>Bot: Update ticket type + subtype
    Bot->>Bot: Write Reclassifications event row
    Bot->>SE: Drafted alert message ready for review
    SE->>Stakeholders: Sends alert (or edits then sends)
    Note over Bot,Stakeholders: Bot never auto-sends. Never to customer.
```

---

## 6. Multi-customer prio bump

```mermaid
flowchart TD
    A[New ticket created] --> B[Dedupe check finds match in existing live ticket]
    B --> C[Bot DMs SE: 'merge into TIC-042?']
    C -->|Confirm| D[Add new org to Affected orgs of TIC-042]
    D --> E{How many affected orgs now?}
    E -->|2| F[Bot suggests +1 tier bump]
    E -->|3+| G[Bot suggests P1 minimum]
    E -->|5+ on critical-path| H[Bot suggests P0 candidate]
    F --> I[SE confirms or skips]
    G --> I
    H --> J[SE or CTO manually sets P0]
    I --> K[Prio updated + Prio changes event logged]
    J --> K
```

---

## 7. Comparison with v0 (what changed)

| v0 | v1 | Why |
|---|---|---|
| 3 linear branches | 3 types + lanes + statuses + loopbacks | Real flow has cycles (customer disputes, reclass, multi-customer) |
| Triage at intake fixes type | Reclassify any time | Initial classification is often wrong |
| "Resolved" = closed | Resolved → Awaiting confirmation → Closed (with 7d auto-close, 30d reopen) | Customer confirmation matters; silence shouldn't block close forever |
| Bug: SE writeup → Dev → SE → close | One board, lanes (SE Action / Dev Action), button-driven | Cleaner ownership signal for CSMs |
| No hotfix concept | Hotfix forks into linked underlying bug | Captures tech-debt backlog |
| No prio | P0–P4 + matrix + soft SLAs + multi-customer bumps | Tier-and-time discipline |
| Implicit assignment to SE | Explicit lane, fallback to Tristan for P0/P1 OOO | Single-point-of-failure mitigation |
| No metadata capture | Event logs (status, prio, reclass, comms) | Reporting from day one |
| Customer comms freeform | Bot drafts at every key moment; SE/CSM always sends | Consistency + speed without bot autonomy on customer messaging |
