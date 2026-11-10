"""Data source connectors for Deep Research."""

from nivos.connectors._stubs import (
    Attachment,
    BaseConnector,
    Document,
    SyncStatus,
)
from nivos.connectors.store import KnowledgeStore

__all__ = ["Attachment", "BaseConnector", "Document", "KnowledgeStore", "SyncStatus"]

# Auto-register built-in connectors
import nivos.connectors.obsidian  # noqa: F401

try:
    import nivos.connectors.gmail  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.gmail_imap  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.gdrive  # noqa: F401
except ImportError:
    pass  # httpx may not be installed

try:
    import nivos.connectors.notion  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.granola  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.gcontacts  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.apple_notes  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.apple_music  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.apple_contacts  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.apple_calendar  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.slack_connector  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.outlook  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.imap  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.gcalendar  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.dropbox  # noqa: F401
except ImportError:
    pass  # httpx may not be installed

try:
    import nivos.connectors.whatsapp  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.oura  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.apple_health  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.strava  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.spotify  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.google_tasks  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.weather  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.github_notifications  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.hackernews  # noqa: F401
except ImportError:
    pass

try:
    import nivos.connectors.news_rss  # noqa: F401
except ImportError:
    pass
