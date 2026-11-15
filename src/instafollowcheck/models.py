"""Comparações por ID estável, sem consultas adicionais por contato."""

from dataclasses import dataclass


class MonitorError(Exception):
    """Erro cuja mensagem pode ser apresentada sem expor cookies ou respostas HTTP."""


@dataclass(frozen=True)
class Snapshot:
    owner_id: str
    username: str
    captured_at: str
    followers: dict[str, str]
    following: dict[str, str]


def compare(current: Snapshot, previous: Snapshot | None) -> dict[str, set[str]]:
    """Perdas referem-se ao último snapshot completo, não necessariamente a ontem."""
    followers, following = set(current.followers), set(current.following)
    if previous is not None and previous.owner_id != current.owner_id:
        raise MonitorError("Não é possível comparar históricos de contas diferentes.")
    lost = set(previous.followers) - followers if previous else set()
    stopped = set(previous.following) - following if previous else set()
    return {
        "followers": followers,
        "following": following,
        "mutual": followers & following,
        "not_following_back": following - followers,
        "not_followed_back": followers - following,
        "lost_followers": lost,
        "stopped_following": stopped,
        "possible_soft_blocks": lost & stopped,
    }
