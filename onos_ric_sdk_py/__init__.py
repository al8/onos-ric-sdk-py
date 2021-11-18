# SPDX-FileCopyrightText: © 2021 Open Networking Foundation <support@opennetworking.org>
# SPDX-License-Identifier: Apache-2.0

from __future__ import absolute_import

import asyncio
import logging
import os
import signal
import traceback
from typing import Coroutine, Optional

from aiohttp import web
from aiohttp_swagger import setup_swagger  # type: ignore

from .e2 import E2Client
from .exceptions import DuplicateRouteError
from .sdl import SDLClient
from .server import error_middleware, routes


def run(
    main: Coroutine,
    path: str,
    app: Optional[web.Application] = None,
    **server_kwargs,
) -> None:
    """Start the webserver and the entrypoint logic passed in as ``main``.

    Args:
        main: The entrypoint for the service's logic, in the form of a coroutine.
        path: The path to the service's configuration file on disk.
        app: An existing web application object, if available.
        server_kwargs: Variable number of ``kwargs`` to pass to
                       :func:`aiohttp.web.run_app`.
    Raises:
        DuplicateRouteError: A user-supplied route conflicts with one of the default
                             :doc:`routes<./routes>`.
        ValueError: ``main`` is not a proper coroutine.
    """
    logging.basicConfig(
        format="%(levelname)s %(asctime)s %(filename)s:%(lineno)d] %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not asyncio.coroutines.iscoroutine(main):
        raise ValueError(f"A coroutine was expected, got {main}")

    # Create web application object and shutdown event
    if app is None:
        app = web.Application(middlewares=[error_middleware])
    app["main"] = main
    app["path"] = path

    # Initialize the endpoints for the HTTP server
    try:
        app.add_routes(routes)
    except RuntimeError:
        app["main"].close()
        resources = [(r.method, r.path) for r in routes if isinstance(r, web.RouteDef)]
        raise DuplicateRouteError(
            f"A user-supplied route conflicts with a pre-registered route: {resources}"
        )

    # Document the endpoints with OpenAPI
    setup_swagger(app, ui_version=3)

    # Add background tasks
    app.cleanup_ctx.append(run_main)
    app.cleanup_ctx.append(run_listener)
    web.run_app(app, **server_kwargs)

async def run_main(app):
    task = asyncio.create_task(app["main"])

    yield

    task.cancel()
    try:
        await task  # Ensure any exceptions etc. are raised.
    except asyncio.CancelledError:
        pass

async def run_listener(app):
    app["shutdown_event"] = asyncio.Event()
    task = asyncio.create_task(shutdown_listener(app))

    yield

    task.cancel()
    try:
        await task  # Ensure any exceptions etc. are raised.
    except asyncio.CancelledError:
        pass

async def shutdown_listener(app: web.Application) -> None:
    """Wait for the 'shutdown_event' notification to kill the process."""
    await app["shutdown_event"].wait()
    logging.warning("Shutting down!")

    # Wait before shutting down
    await asyncio.sleep(1)

    os.kill(os.getpid(), signal.SIGTERM)


__all__ = ["E2Client", "SDLClient", "run"]
