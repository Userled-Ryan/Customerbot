from __future__ import annotations

from customerbot.domain.tracking.entities import TrackedConversation
from customerbot.domain.tracking.ports import ConversationRepositoryPort, MessengerPort, UserSettingsRepositoryPort


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
        user_settings_repo: UserSettingsRepositoryPort,
        ryan_user_id: str,
        reminder_hours: int = 24,
    ) -> None:
        self._repo = repo
        self._messenger = messenger
        self._user_settings_repo = user_settings_repo
        self._ryan_user_id = ryan_user_id
        self._default_reminder_hours = reminder_hours

    async def execute(self) -> str:
        settings = await self._user_settings_repo.get(self._ryan_user_id)
        user_default = settings.default_reminder_hours if settings else self._default_reminder_hours

        open_convos = await self._repo.find_open()

        if not open_convos:
            return "✅ *All clear — no open conversations.*"

        overdue: list[TrackedConversation] = []
        active: list[TrackedConversation] = []

        for conv in open_convos:
            interval = conv.effective_reminder_hours(user_default)
            if conv.is_overdue(interval):
                overdue.append(conv)
            else:
                active.append(conv)

        lines = [f"*📋 Open Conversations ({len(open_convos)})*\n"]

        if overdue:
            lines.append("🔴 *Overdue*")
            for conv in overdue:
                link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
                label = conv.channel_name or conv.channel_id
                age = _format_hours(conv.hours_since_last_reply())
                interval = conv.effective_reminder_hours(user_default)
                interval_label = f" · SLA: {interval}h" if conv.reminder_interval_hours is not None else ""
                lines.append(
                    f"  `#{conv.ticket_number}` <{link}|#{label}> · {conv.category.title()} · {age}{interval_label}"
                )

        if active:
            if overdue:
                lines.append("")
            lines.append("🟢 *Active*")
            for conv in active:
                link = self._messenger.build_thread_link(conv.channel_id, conv.thread_ts)
                label = conv.channel_name or conv.channel_id
                age = _format_hours(conv.hours_since_last_reply())
                interval_label = f" · SLA: {conv.reminder_interval_hours}h" if conv.reminder_interval_hours is not None else ""
                lines.append(
                    f"  `#{conv.ticket_number}` <{link}|#{label}> · {conv.category.title()} · {age}{interval_label}"
                )

        lines.append("\n_Close a ticket with `/csbot close <id>` (e.g. `/csbot close 3`)._")
        return "\n".join(lines)
