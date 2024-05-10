import json
import structlog
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pymongo.database import Database

from ..authSessions.crud import AuthSessionCRUD
from ..authSessions.models import AuthSession, AuthSessionPatch, AuthSessionState
from ..core.acapy.client import AcapyClient, PresExProofConfig
from ..db.session import get_db

from ..db.collections import COLLECTION_NAMES
from ..core.config import settings

logger = structlog.getLogger(__name__)
from ..routers.socketio import sio, connections_reload
from ..routers.webhook_deliverer import deliver_notification
from ..ttl_cache import cache_manager

router = APIRouter()


async def _parse_webhook_body(request: Request):
    return json.loads((await request.body()).decode("ascii"))


@router.post("/topic/{topic}/")
async def post_topic(request: Request, topic: str, db: Database = Depends(get_db)):
    """Called by aca-py agent."""
    logger.info(f">>> post_topic : topic={topic}")

    client = AcapyClient(db=db)
    if topic == "present_proof":
        webhook_body = await _parse_webhook_body(request)
        logger.info(f">>>> pres_exch_id: {webhook_body['presentation_exchange_id']}")

        auth_session: AuthSession = await AuthSessionCRUD(db).get_by_pres_exch_id(
            webhook_body["presentation_exchange_id"]
        )

        # Get the saved websocket session
        pid = str(auth_session.id)
        connections = connections_reload()
        sid = connections.get(pid)

        if webhook_body["state"] == "presentation_received":
            logger.info("GOT A PRESENTATION, TIME TO VERIFY")
            # client.verify_presentation(auth_session.pres_exch_id)
            # This state is the default on the front end.. So don't send a status

        if webhook_body["state"] == "verified":
            logger.info("VERIFIED")
            if webhook_body["verified"] == "true":
                auth_session.proof_status = AuthSessionState.SUCCESS
                pres_exch = auth_session.presentation_exchange
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

                # Save metadata to TTL cache
                cache_manager.set(str(auth_session.id), {"metadata": metadata})

                # conditionally save metadata to db
                if auth_session.retain_attributes:
                    auth_session.metadata = metadata
                
                await sio.emit("status", {"status": "success"}, to=sid)
                if auth_session.notify_endpoint:
                    deliver_notification(
                        "status", {"status": "success"}, auth_session.notify_endpoint
                    )
            else:
                auth_session.proof_status = AuthSessionState.FAILURE
                await sio.emit("status", {"status": "failure"}, to=sid)
                if auth_session.notify_endpoint:
                    deliver_notification(
                        "status", {"status": "failure"}, auth_session.notify_endpoint
                    )
            await AuthSessionCRUD(db).patch(
                str(auth_session.id), AuthSessionPatch(**auth_session.dict())
            )

        # Calcuate the expiration time of the proof
        now_time = datetime.now()
        expired_time = now_time + timedelta(
            seconds=settings.CONTROLLER_PRESENTATION_EXPIRE_TIME
        )

        # Update the expiration time of the proof
        auth_session.expired_timestamp = expired_time
        await AuthSessionCRUD(db).patch(
            str(auth_session.id), AuthSessionPatch(**auth_session.dict())
        )

        # Check if expired. But only if the proof has not been started.
        if (
            expired_time < now_time
            and auth_session.proof_status == AuthSessionState.INITIATED
        ):
            logger.info("EXPIRED")
            auth_session.proof_status = AuthSessionState.EXPIRED
            await sio.emit("status", {"status": "expired"}, to=sid)
            if auth_session.notify_endpoint:
                deliver_notification(
                    "status", {"status": "expired"}, auth_session.notify_endpoint
                )
            await AuthSessionCRUD(db).patch(
                str(auth_session.id), AuthSessionPatch(**auth_session.dict())
            )

        pass
    else:
        logger.debug("skipping webhook")

    return {}
