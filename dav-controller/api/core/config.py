import json
import logging
import logging.config
import os
import sys
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

import structlog
from pydantic import BaseSettings


# Removed in later versions of python
def strtobool(val: str | bool) -> bool:
    """Convert a string representation of truth to a boolean (True or False).
    True values are 'y', 'yes', 't', 'true', 'on', and '1'; False
    values are 'n', 'no', 'f', 'false', 'off', and '0'. If val is
    already a boolean it is simply returned.  Raises ValueError if
    'val' is anything else.
    """
    if isinstance(val, bool):
        return val

    val = val.lower()
    if val in ("y", "yes", "t", "true", "on", "1"):
        return True
    elif val in ("n", "no", "f", "false", "off", "0"):
        return False
    else:
        raise ValueError(f"invalid truth value {val}")


# Use environment variable to determine logging format
# default to True
# strtobool will convert the results of the environment variable to a bool
use_json_logs: bool = strtobool(
    os.environ.get("LOG_WITH_JSON", True)
    if os.environ.get("LOG_WITH_JSON", True) != ""
    else True
)

time_stamp_format: str = os.environ.get("LOG_TIMESTAMP_FORMAT", "iso")

with open((Path(__file__).parent.parent / "logconf.json").resolve()) as user_file:
    file_contents: dict = json.loads(user_file.read())
    logging.config.dictConfig(file_contents["logger"])


def determin_log_level():
    log_level = os.environ.get("LOG_LEVEL")
    if log_level == "DEBUG":
        return logging.DEBUG
    elif log_level == "INFO":
        return logging.INFO
    elif log_level == "WARNING":
        return logging.WARNING
    elif log_level == "ERROR":
        return logging.ERROR
    else:
        return logging.DEBUG


logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=determin_log_level(),
)

shared_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.stdlib.ExtraAdder(),
    structlog.processors.StackInfoRenderer(),
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt=time_stamp_format),
]

renderer = (
    structlog.processors.JSONRenderer()
    if use_json_logs
    else structlog.dev.ConsoleRenderer()
)

# override uvicorn logging to use logstruct
formatter = structlog.stdlib.ProcessorFormatter(
    # These run ONLY on `logging` entries that do NOT originate within
    # structlog.
    foreign_pre_chain=shared_processors,
    # These run on ALL entries after the pre_chain is done.
    processors=[
        # Remove _record & _from_structlog.
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        renderer,
    ],
)

handler = logging.StreamHandler()
handler.setFormatter(formatter)

for _log in ["uvicorn", "uvicorn.error"]:
    # Clear the log handlers for uvicorn loggers, and enable propagation
    # so the messages are caught by our root logger and formatted correctly
    # by structlog
    logging.getLogger(_log).handlers.clear()
    logging.getLogger(_log).addHandler(handler)
    logging.getLogger(_log).propagate = False

# This is already handled by our middleware
logging.getLogger("uvicorn.access").handlers.clear()
logging.getLogger("uvicorn.access").propagate = False

# Configure structlog
structlog.configure(
    processors=[structlog.stdlib.filter_by_level] + shared_processors + [renderer],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLogger().getEffectiveLevel()
    ),
    cache_logger_on_first_use=True,
)

# Setup logger for config
logger: structlog.typing.FilteringBoundLogger = structlog.getLogger(__name__)


class EnvironmentEnum(str, Enum):
    PRODUCTION = "production"
    LOCAL = "local"


class GlobalConfig(BaseSettings):
    TITLE: str = os.environ.get("CONTROLLER_APP_TITLE", "DAV Controller")
    DESCRIPTION: str = os.environ.get(
        "CONTROLLER_APP_DESCRIPTION",
        "Digital Age Verification for verification of age and identity.",
    )

    ENVIRONMENT: EnvironmentEnum
    DEBUG: bool = False
    TESTING: bool = False
    TIMEZONE: str = "UTC"

    # the following defaults match up with default values in scripts/.env.example
    # these MUST be all set in non-local environments.
    DB_HOST: str = os.environ.get("DB_HOST", "localhost")
    DB_PORT: Union[int, str] = os.environ.get("DB_PORT", "27017")
    DB_NAME: str = os.environ.get("DB_NAME", "dav-controller")
    DB_USER: str = os.environ.get("DAV_CONTROLLER_DB_USER", "davcontrolleruser")
    DB_PASS: str = os.environ.get("DAV_CONTROLLER_DB_USER_PWD", "davcontrollerpass")

    MONGODB_URL: str = f"""mongodb://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}?retryWrites=true&w=majority"""  # noqa: E501

    CONTROLLER_URL: Optional[str] = os.environ.get("CONTROLLER_URL")
    # Where to send users when trying to scan with their mobile camera (not a wallet)
    CONTROLLER_CAMERA_REDIRECT_URL: Optional[str] = os.environ.get(
        "CONTROLLER_CAMERA_REDIRECT_URL"
    )
    # The number of seconds to wait for a presentation to be verified, Default: 10
    CONTROLLER_PRESENTATION_EXPIRE_TIME: Union[int, str] = os.environ.get(
        "CONTROLLER_PRESENTATION_EXPIRE_TIME", 20
    )

    ACAPY_AGENT_URL: Optional[str] = os.environ.get("ACAPY_AGENT_URL")
    if not ACAPY_AGENT_URL:
        logger.warning("ACAPY_AGENT_URL was not provided, agent will not be accessible")

    ACAPY_TENANCY: str = os.environ.get(
        "ACAPY_TENANCY", "single"
    )  # valid options are "multi" and "single"

    ACAPY_ADMIN_URL: str = os.environ.get("ACAPY_ADMIN_URL", "http://localhost:8031")

    MT_ACAPY_WALLET_ID: Optional[str] = os.environ.get("MT_ACAPY_WALLET_ID")
    MT_ACAPY_WALLET_KEY: str = os.environ.get("MT_ACAPY_WALLET_KEY", "random-key")

    ST_ACAPY_ADMIN_API_KEY_NAME: Optional[str] = os.environ.get(
        "ST_ACAPY_ADMIN_API_KEY_NAME"
    )
    ST_ACAPY_ADMIN_API_KEY: Optional[str] = os.environ.get("ST_ACAPY_ADMIN_API_KEY")
    DB_ECHO_LOG: bool = False

    # DAV Controller Settings
    CONTROLLER_API_KEY: str = os.environ.get("CONTROLLER_API_KEY", "")
    USE_OOB_PRESENT_PROOF: bool = strtobool(
        os.environ.get("USE_OOB_PRESENT_PROOF", False)
    )
    USE_OOB_LOCAL_DID_SERVICE: bool = strtobool(
        os.environ.get("USE_OOB_LOCAL_DID_SERVICE", False)
    )
    SET_NON_REVOKED: bool = strtobool(os.environ.get("SET_NON_REVOKED", True))

    class Config:
        case_sensitive = True


class LocalConfig(GlobalConfig):
    """Local configurations."""

    DEBUG: bool = True
    DB_ECHO_LOG = True
    ENVIRONMENT: EnvironmentEnum = EnvironmentEnum.LOCAL


class ProdConfig(GlobalConfig):
    """Production configurations."""

    DEBUG: bool = False
    ENVIRONMENT: EnvironmentEnum = EnvironmentEnum.PRODUCTION


class FactoryConfig:
    def __init__(self, environment: Optional[str]):
        self.environment = environment

    def __call__(self) -> GlobalConfig:
        if self.environment == EnvironmentEnum.LOCAL.value:
            return LocalConfig()
        return ProdConfig()


@lru_cache()
def get_configuration() -> GlobalConfig:
    return FactoryConfig(os.environ.get("ENVIRONMENT"))()


settings = get_configuration()
