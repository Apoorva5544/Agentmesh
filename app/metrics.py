# Thin compatibility module: usage summaries now live in app.db.Database.
# Kept so that anything importing app.metrics keeps working after the switch
# from SQLite to PostgreSQL.

from app import db as _db


async def usage_summary(database: _db.Database) -> dict:
    return await database.summary()