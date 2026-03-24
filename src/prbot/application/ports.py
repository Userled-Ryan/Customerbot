# Re-export from domain for backwards compatibility.
from prbot.domain.ports import PRRepositoryPort, PRSourcePort, ReactionPort

__all__ = ["PRRepositoryPort", "PRSourcePort", "ReactionPort"]
