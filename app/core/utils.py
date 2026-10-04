from datetime import datetime, timezone


def utc_now() -> datetime:
    """The one way to get 'now' in this project: timezone-aware, always UTC."""
    return datetime.now(timezone.utc)
