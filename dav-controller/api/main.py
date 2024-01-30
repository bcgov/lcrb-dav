import traceback
import structlog
import os
import time
import uuid
from pathlib import Path

import uvicorn
from api.core.config import settings
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi import status as http_status
from fastapi.responses import JSONResponse

from .db.session import get_db, init_db
from .routers import (
    acapy_handler,
    age_verification,
    presentation_request,
    well_known_oid_config,
)
from .db.session import init_db, get_db
from .routers.socketio import sio_app

logger: structlog.typing.FilteringBoundLogger = structlog.getLogger(__name__)

# Setup loggers
logging_file_path = os.environ.get(
    "LOG_CONFIG_PATH", (Path(__file__).parent / "logging.conf").resolve()
)


os.environ["TZ"] = settings.TIMEZONE
time.tzset()


def get_application() -> FastAPI:
    application = FastAPI(
        title=settings.TITLE,
        description=settings.DESCRIPTION,
        debug=settings.DEBUG,
        # middleware=None,
    )
    return application


app = get_application()
app.include_router(acapy_handler.router, prefix="/webhooks", include_in_schema=False)
app.include_router(presentation_request.router, include_in_schema=False)

# Connect the websocket server to run within the FastAPI app
app.mount("/ws", sio_app)

origins = ["*"]

if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def logging_middleware(request: Request, call_next) -> Response:
    structlog.threadlocal.clear_threadlocal()
    structlog.threadlocal.bind_threadlocal(
        logger="uvicorn.access",
        request_id=str(uuid.uuid4()),
        cookies=request.cookies,
        scope=request.scope,
        url=str(request.url),
    )
    start_time = time.time()
    try:
        response: Response = await call_next(request)
        return response
    finally:
        process_time = time.time() - start_time
        # If we have a response object, log the details
        if "response" in locals():
            logger.info(
                "processed a request",
                status_code=response.status_code,
                process_time=process_time,
            )
        # Otherwise, extract the exception from traceback, log and return a 500 response
        else:
            logger.info(
                "failed to process a request",
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                process_time=process_time,
            )

            # Need to explicitly log the traceback
            logger.error(traceback.format_exc())

            return JSONResponse(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "status": "error",
                    "message": "Internal Server Error",
                    "process_time": process_time,
                },
            )


@app.on_event("startup")
async def on_tenant_startup():
    """Register any events we need to respond to."""
    await init_db()
    # await init_provider(await get_db())
    logger.info(">>> Starting up app new ...")


@app.on_event("shutdown")
def on_tenant_shutdown():
    """TODO no-op for now."""
    logger.warning(">>> Shutting down app ...")


@app.get("/", tags=["liveness", "readiness"])
@app.get("/health", tags=["liveness", "readiness"])
def main():
    return {"status": "ok", "health": "ok"}


if __name__ == "__main__":
    logger.info("main.")
    uvicorn.run(app, host="0.0.0.0", port=5100)
