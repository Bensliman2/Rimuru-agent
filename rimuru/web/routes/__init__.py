"""The app's API, one file per feature. Each has add_routes(app, web)."""
from . import chat, connections, first_run, folders, hood, memories, models, skills, work

ALL = (chat, work, folders, connections, memories, skills, models, first_run, hood)
