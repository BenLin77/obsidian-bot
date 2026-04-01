from __future__ import annotations

import logging

from telegram.ext import Application, PersistenceInput, PicklePersistence

from .ai_classifier import AIClassifier
from .config import load_settings
from .daily_note import DailyNoteWriter
from .handlers import AppServices, register_handlers, store_services
from .media_handler import MediaHandler
from .note_writer import NoteWriter
from .url_extractor import URLExtractor
from .vault_adapter import VaultAdapter
from .web_lookup import OfficialWebLookup


def configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )


def build_application() -> Application:
    settings = load_settings()
    settings.inbox_path.mkdir(parents=True, exist_ok=True)
    settings.common_path.mkdir(parents=True, exist_ok=True)
    settings.daily_path.mkdir(parents=True, exist_ok=True)
    settings.attachments_path.mkdir(parents=True, exist_ok=True)
    settings.state_path.parent.mkdir(parents=True, exist_ok=True)

    persistence = PicklePersistence(
        filepath=settings.state_path,
        store_data=PersistenceInput(
            bot_data=False,
            chat_data=False,
            user_data=True,
            callback_data=False,
        ),
    )
    vault = VaultAdapter(settings)
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .persistence(persistence)
        .build()
    )
    services = AppServices(
        settings=settings,
        writer=NoteWriter(settings, vault=vault),
        daily=DailyNoteWriter(settings),
        media=MediaHandler(settings, vault=vault),
        url=URLExtractor(settings, vault=vault),
        ai=AIClassifier(settings, vault=vault),
        vault=vault,
        web=OfficialWebLookup(),
    )
    store_services(application, services)
    register_handlers(application)
    return application


def main() -> None:
    configure_logging()
    application = build_application()
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
