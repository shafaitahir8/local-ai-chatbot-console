"""
Migrate scattered bot storage into portable models/<bot_id>/ folders.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from app.models.database import Bot, ConversationMemory, SessionLocal, init_db  # noqa: E402
from app.services.standalone_model_service import standalone_model_service  # noqa: E402


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        imported = standalone_model_service.sync_db_from_folders(db)
        bots = db.query(Bot).order_by(Bot.created_at.asc()).all()
        summary: dict[str, object] = {
            "project_root": str(PROJECT_ROOT),
            "models_root": str(standalone_model_service.models_root.relative_to(PROJECT_ROOT)),
            "imported_from_folders": imported,
            "bots_processed": 0,
            "memory_sessions_migrated": 0,
            "bots": [],
        }

        for bot in bots:
            migration = standalone_model_service.migrate_legacy_bot_storage(bot.id)
            standalone_model_service.write_bot_config(bot)

            session_count = 0
            memories = db.query(ConversationMemory).filter(ConversationMemory.bot_id == bot.id).all()
            for memory in memories:
                standalone_model_service.persist_memory_messages(
                    bot.id,
                    memory.session_id,
                    list(memory.messages or []),
                )
                session_count += 1

            summary["bots"].append(
                {
                    "bot_id": bot.id,
                    "name": bot.name,
                    "moved": migration.get("moved", []),
                    "folder": str(standalone_model_service.bot_root(bot.id).relative_to(PROJECT_ROOT)),
                    "memory_sessions": session_count,
                }
            )
            summary["bots_processed"] = int(summary["bots_processed"]) + 1
            summary["memory_sessions_migrated"] = int(summary["memory_sessions_migrated"]) + session_count

        print(json.dumps(summary, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
