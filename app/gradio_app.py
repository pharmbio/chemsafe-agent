"""Application entry point: mount the Gradio UI on FastAPI and serve it.

The pieces live next door:
  * `app.ui.layout`          — the widget tree and its event wiring
  * `app.ui.projection`      — UIState to component updates
  * `app.run_controller`     — driving one agent run
  * `app.session`            — sign-in, conversation selection, files
  * `app.downloads`          — signed download links
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager

import gradio as gr
import uvicorn
from fastapi import FastAPI

from app.config import APP_TITLE, GRADIO_SERVER_NAME, GRADIO_SERVER_PORT
from app.downloads import FILES_ROUTER
from app.session import AUTH_SERVICE
from app.ui.layout import build_demo
from backend.db import (
    close_async_pool,
    close_postgres_checkpointer,
    get_async_pool,
    get_postgres_checkpointer,
)

__all__ = ["build_demo", "create_fastapi_app", "launch"]


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    await get_async_pool()
    await get_postgres_checkpointer()
    await AUTH_SERVICE.repo.ensure_schema()
    try:
        yield
    finally:
        await close_postgres_checkpointer()
        await close_async_pool()


def create_fastapi_app() -> FastAPI:
    demo = build_demo()
    fastapi_app = FastAPI(title=APP_TITLE, lifespan=_app_lifespan)
    fastapi_app.include_router(FILES_ROUTER)
    mount_kwargs = {"path": "/"}
    if "footer_links" in inspect.signature(gr.mount_gradio_app).parameters:
        mount_kwargs["footer_links"] = ["api", "gradio"]
    return gr.mount_gradio_app(fastapi_app, demo, **mount_kwargs)


def launch() -> None:
    uvicorn.run(
        create_fastapi_app(),
        host=GRADIO_SERVER_NAME,
        port=GRADIO_SERVER_PORT,
    )
