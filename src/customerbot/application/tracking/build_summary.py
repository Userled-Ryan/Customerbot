from __future__ import annotations

from customerbot.domain.tracking.entities import TrackedConversation
from customerbot.domain.tracking.ports import ConversationRepositoryPort, MessengerPort


def _format_hours(hours: float) -> str:
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{int(hours)}h ago"
    days = int(hours / 24)
    remaining = int(hours % 24)
    return f"{days}d {remaining}h ago" if remaining else f"{days}d ago"


class BuildSummary:
    """Use case: generate a summary of open conversations for @mention responses."""

    def __init__(
        self,
        repo: ConversationRepositoryPort,
        messenger: MessengerPort,
        reminder_hours: int = 24,
    ) -> None:
        self._repo = repo
        self._messenger = messenger
        self._reminder_hours = reminder_hours

    async def execute(self) -> str:
        open_convos = await self._repo.find_open()

        if not open_convos:
            return "✅ *All clear — no open conversations.*"

        overdue: list[TrackedConversation] = []
        active: list[TrackedConversation] = []

        for conv in open_convos:
            if conv.is_overdue(self._reminder_hours):
                overdue.append(conv)
            else:
                active.append(conv)

        lines = [f"*📋 Open Conversations ({len(open_convos)})*\n"]

        if overdue:
            lines.append(f"🔴 *Overdue — no reply in {self._reminder_hours}h+*")
            for conv in overdue:
                link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
                label = conv.channel_name or conv.channel_id
                age = _format_hours(conv.hours_since_last_reply())
                lines.append(
                    f"  `#{conv.ticket_number}` <{link}|#{label}> · {conv.category.title()} · {age}"
                )

        if active:
            if overdue:
                lines.append("")
            lines.append("🟢 *Active*")
            for conv in active:
                link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
                label = conv.channel_name or conv.channel_id
                age = _format_hours(conv.hours_since_last_reply())
                lines.append(
                    f"  `#{conv.ticket_number}` <{link}|#{label}> · {conv.category.title()} · {age}"
                )

        lines.append("\n_Close a ticket with `/csbot close <id>` (e.g. `/csbot close 3`)._")
        return "\n".join(lines)
