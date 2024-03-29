import base64
import io
import json
import os
import uuid
from datetime import datetime
from typing import Mapping, cast
from urllib.parse import urlencode

import qrcode
import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Template
from pymongo.database import Database
from pyop.exceptions import InvalidAuthenticationRequest

from ..authSessions.crud import AuthSessionCreate, AuthSessionCRUD
from ..authSessions.models import AuthSessionPatch, AuthSessionState
from ..core.acapy.client import AcapyClient, PresExProofConfig
from ..core.auth import get_api_key
from ..core.config import settings
from ..core.logger_util import log_debug
from ..core.models import (
    AgeVerificationModelCreate,
    AgeVerificationModelRead,
    AgeVerificationModelCreateRead,
    GenericErrorMessage,
)
from ..db.collections import COLLECTION_NAMES
from ..db.session import get_db

# Access to the websocket
from ..routers.socketio import connections_reload, sio
from ..routers.webhook_deliverer import deliver_notification

# This allows the templates to insert assets like css, js or svg.
from ..templates.helpers import add_asset

logger: structlog.typing.FilteringBoundLogger = structlog.getLogger(__name__)

router = APIRouter()


@log_debug
@router.get(
    f"/age-verification/{{pid}}",
    response_description="Get the specified age verification record",
    status_code=http_status.HTTP_200_OK,
    response_model=AgeVerificationModelRead,
    responses={http_status.HTTP_409_CONFLICT: {"model": GenericErrorMessage}},
    response_model_exclude_unset=True,
    dependencies=[Depends(get_api_key)],
)
async def get_dav_request(pid: str, db: Database = Depends(get_db)):
    """Called by authorize webpage to see if request is verified."""
    auth_session = await AuthSessionCRUD(db).get(pid)

    pid = str(auth_session.id)
    connections = connections_reload()
    sid = connections.get(pid)

    """
     Check if proof is expired. But only if the proof has not been started.
     NOTE: This should eventually be moved to a background task.
    """
    if (
        auth_session.expired_timestamp < datetime.now()
        and auth_session.proof_status == AuthSessionState.INITIATED
    ):
        logger.info("PROOF EXPIRED")
        auth_session.proof_status = AuthSessionState.EXPIRED
        await AuthSessionCRUD(db).patch(
            str(auth_session.id), AuthSessionPatch(**auth_session.dict())
        )
        # Send message through the websocket.
        await sio.emit("status", {"status": "expired"}, to=sid)
        if auth_session.notify_endpoint:
            deliver_notification(
                "status", {"status": "expired"}, auth_session.notify_endpoint
            )
    if auth_session.proof_status == AuthSessionState.SUCCESS:
        pres_exch = auth_session.presentation_exchange
        logger.debug(f"PRES_EXCH: {pres_exch}")
        col = db.get_collection(COLLECTION_NAMES.PRES_EX_ID_TO_PROOF_REQ_CONFIG_ID)
        pres_ex_proof_req_id_dict = col.find_one(
            {"pres_exch_id": auth_session.pres_exch_id}
        )
        pres_ex_proof_req_id = PresExProofConfig(**pres_ex_proof_req_id_dict)
        proof_req_id = pres_ex_proof_req_id.proof_req_config_id
        resp_incl_revealed_attibs = {}
        proof_revealed_attr_group_dict = pres_exch["presentation"]["requested_proof"][
            "revealed_attr_groups"
        ]
        for req_attr in proof_revealed_attr_group_dict:
            revealed_attr_value_dict = proof_revealed_attr_group_dict[req_attr][
                "values"
            ]
            for key, value in revealed_attr_value_dict.items():
                resp_incl_revealed_attibs[key] = value["raw"]

        metadata = auth_session.metadata or {}
        metadata["revealed_attributes"] = resp_incl_revealed_attibs

        # Needs to be made flexible for different proof requests
        response = AgeVerificationModelRead(
            status=auth_session.proof_status,
            id=str(auth_session.id),
            notify_endpoint=auth_session.notify_endpoint,
            metadata=metadata,
        )
        # Testing
        logger.error(f" --- {str(response)}")
        return response

    return AgeVerificationModelRead(
        id=str(auth_session.id),
        status=auth_session.proof_status,
        notify_endpoint=auth_session.notify_endpoint,
        metadata=auth_session.metadata,
    )


# HTMLResponse
@log_debug
@router.post(
    "/age-verification",
    response_description="Get the specified age verification record",
    status_code=http_status.HTTP_201_CREATED,
    response_model=AgeVerificationModelCreateRead,
    responses={http_status.HTTP_409_CONFLICT: {"model": GenericErrorMessage}},
    response_model_exclude_unset=True,
    dependencies=[Depends(get_api_key)],
)
async def new_dav_request(
    request: AgeVerificationModelCreate, db: Database = Depends(get_db)
):
    logger.debug(">>> new_dav_request")

    # retrieve presentation_request config.
    client = AcapyClient(db=db)

    # Create presentation_request to show on screen
    response = client.create_presentation_request()

    new_auth_session = AuthSessionCreate(
        metadata=request.metadata,
        pres_exch_id=response.presentation_exchange_id,
        presentation_exchange=response.dict(),
        notify_endpoint=request.notify_endpoint,
    )

    # save AuthSession
    auth_session = await AuthSessionCRUD(db).create(new_auth_session)

    # QR CONTENTS
    controller_host = settings.CONTROLLER_URL
    url_to_message = (
        controller_host + "/url/pres_exch/" + str(auth_session.pres_exch_id)
    )

    return AgeVerificationModelCreateRead(
        id=str(auth_session.id),
        status=AuthSessionState.INITIATED,
        url=url_to_message,
        notify_endpoint=request.notify_endpoint,
        metadata=request.metadata,
    )


@log_debug
@router.get("/", response_class=HTMLResponse)
async def render_new_dav_request(request: Request, db: Database = Depends(get_db)):
    logger.debug(">>> render new_dav_request HTML page")

    req_query_params = request.query_params._dict

    #  create proof for this request
    new_user_id = str(uuid.uuid4())

    # retrieve presentation_request config.
    client = AcapyClient(db=db)

    # Create presentation_request to show on screen
    response = client.create_presentation_request()

    new_auth_session = AuthSessionCreate(
        metadata=req_query_params.get("metadata"),
        pres_exch_id=response.presentation_exchange_id,
        presentation_exchange=response.dict(),
        notify_endpoint=req_query_params.get("notify_endpoint"),
    )

    # save AuthSession
    auth_session = await AuthSessionCRUD(db).create(new_auth_session)

    # QR CONTENTS
    controller_host = settings.CONTROLLER_URL
    url_to_message = (
        controller_host + "/url/pres_exch/" + str(auth_session.pres_exch_id)
    )
    # CREATE the image
    buff = io.BytesIO()
    qrcode.make(url_to_message).save(buff, format="PNG")
    image_contents = base64.b64encode(buff.getvalue()).decode("utf-8")

    # This is the payload to send to the template
    deep_link_proof_url = f"bcwallet://aries_connection_invitation?{url_to_message}"
    with open("/app/api/proof_config.yaml", "r") as stream:
        config_dict = yaml.safe_load(stream)
    proof_config_ident = os.environ.get(
        "DAV_PROOF_CONFIG_ID", "age-verification-bc-person-credential"
    )
    display_msg = config_dict[proof_config_ident]["display-text"]
    data = {
        "image_contents": image_contents,
        "url": url_to_message,
        "add_asset": add_asset,
        "pres_exch_id": auth_session.pres_exch_id,
        "pid": auth_session.id,
        "controller_host": controller_host,
        "deep_link_url": deep_link_proof_url,
        "display_msg": display_msg,
    }

    # Prepare the template
    template_file = open("api/templates/verified_credentials.html", "r").read()
    template = Template(template_file)
    # Render and return the template
    return template.render(data)
