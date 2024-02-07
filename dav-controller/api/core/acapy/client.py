import json
from typing import List, Optional, Union
from uuid import UUID

import requests
import structlog

from ..config import settings
from .config import AgentConfig, MultiTenantAcapy, SingleTenantAcapy
from .models import CreatePresentationResponse, WalletDid

_client = None
logger = structlog.getLogger(__name__)

WALLET_DID_URI = "/wallet/did"
PUBLIC_WALLET_DID_URI = "/wallet/did/public"
CREATE_PRESENTATION_REQUEST_URL = "/present-proof/create-request"
PRESENT_PROOF_RECORDS = "/present-proof/records"


class AcapyClient:
    acapy_host = settings.ACAPY_ADMIN_URL
    service_endpoint = settings.ACAPY_AGENT_URL

    wallet_token: Optional[str] = None
    agent_config: AgentConfig

    def __init__(self):
        if settings.ACAPY_TENANCY == "multi":
            self.agent_config = MultiTenantAcapy()
        elif settings.ACAPY_TENANCY == "single":
            self.agent_config = SingleTenantAcapy()
        else:
            logger.warning("ACAPY_TENANCY not set, assuming SingleTenantAcapy")
            self.agent_config = SingleTenantAcapy()

        if _client:
            return _client
        super().__init__()

    def generate_verification_proof_request(
        self,
        name: str = "age-verification",
        nonce: str = "1234567890",
        age: int = 18,
        attrib_names_list: List[str] = ["picture"],
        revocation: bool = True,
        attrib_ident: str = "picture",
        predicate_ident: str = "birthdate_GE",
        cred_def_id: str = None,
        schema_name: str = None,
    ):
        result = {
            "name": name,
            "nonce": nonce,
            "version": "1.0",
            "requested_attributes": {},
            "requested_predicates": {},
        }
        d = datetime.date.today()
        birth_date = datetime.date(d.year - age, d.month, d.day)
        birth_date_format = "%Y%m%d"
        if revocation:
            result["requested_attributes"]["non_revoked"] = {"to": int(time.time() - 1)}

            result["requested_predicates"]["non_revoked"] = {"to": int(time.time() - 1)}
        req_attrib_ident = f"0_{attrib_ident}_uuid"
        req_predicate_ident = f"0_{predicate_ident}_uuid"
        if schema_name:
            result["requested_attributes"][req_attrib_ident] = {
                "names": attrib_names_list,
                "restrictions": [
                    {
                        "schema_name": schema_name,
                    }
                ],
            }
            result["requested_predicates"][req_predicate_ident] = {
                "name": "birthdate_dateint",
                "p_type": "<=",
                "p_value": int(birth_date.strftime(birth_date_format)),
                "restrictions": [
                    {
                        "schema_name": schema_name,
                    }
                ],
            }
        else:
            result["requested_attributes"][req_attrib_ident] = {
                "names": attrib_names_list,
                "restrictions": [
                    {
                        "cred_def_id": cred_def_id,
                    }
                ],
            }
            result["requested_predicates"][req_predicate_ident] = {
                "name": "birthdate_dateint",
                "p_type": "<=",
                "p_value": int(birth_date.strftime(birth_date_format)),
                "restrictions": [
                    {
                        "cred_def_id": cred_def_id,
                    }
                ],
            }
        return result

    def create_presentation_request(
        self, presentation_request_configuration: dict = None
    ) -> CreatePresentationResponse:
        logger.debug(">>> create_presentation_request")
        if presentation_request_configuration:
            present_proof_payload = {
                "proof_request": presentation_request_configuration
            }
        else:
            d = datetime.date.today()
            birth_date = datetime.date(d.year - age, d.month, d.day)
            time_now = int(time.time())
            present_proof_payload = {
                "proof_request": self.generate_verification_proof_request()
            }

        resp_raw = requests.post(
            self.acapy_host + CREATE_PRESENTATION_REQUEST_URL,
            headers=self.agent_config.get_headers(),
            json=present_proof_payload,
        )

        # TODO: Determine if this should assert it received a json object
        assert resp_raw.status_code == 200, resp_raw.content

        resp = json.loads(resp_raw.content)
        result = CreatePresentationResponse.parse_obj(resp)

        logger.debug("<<< create_presenation_request")
        return result

    def get_presentation_request(self, presentation_exchange_id: Union[UUID, str]):
        logger.debug(">>> get_presentation_request")

        resp_raw = requests.get(
            self.acapy_host
            + PRESENT_PROOF_RECORDS
            + "/"
            + str(presentation_exchange_id),
            headers=self.agent_config.get_headers(),
        )

        # TODO: Determine if this should assert it received a json object
        assert resp_raw.status_code == 200, resp_raw.content

        resp = json.loads(resp_raw.content)

        logger.debug(f"<<< get_presentation_request -> {resp}")
        return resp

    def verify_presentation(self, presentation_exchange_id: Union[UUID, str]):
        logger.debug(">>> verify_presentation")

        resp_raw = requests.post(
            self.acapy_host
            + PRESENT_PROOF_RECORDS
            + "/"
            + str(presentation_exchange_id)
            + "/verify-presentation",
            headers=self.agent_config.get_headers(),
        )
        assert resp_raw.status_code == 200, resp_raw.content

        resp = json.loads(resp_raw.content)

        logger.debug(f"<<< verify_presentation -> {resp}")
        return resp

    def get_wallet_did(self, public=False) -> WalletDid:
        logger.debug(">>> get_wallet_did")
        url = None
        if public:
            url = self.acapy_host + PUBLIC_WALLET_DID_URI
        else:
            url = self.acapy_host + WALLET_DID_URI

        resp_raw = requests.get(
            url,
            headers=self.agent_config.get_headers(),
        )

        # TODO: Determine if this should assert it received a json object
        assert (
            resp_raw.status_code == 200
        ), f"{resp_raw.status_code}::{resp_raw.content}"

        resp = json.loads(resp_raw.content)

        if public:
            resp_payload = resp["result"]
        else:
            resp_payload = resp["results"][0]

        did = WalletDid.parse_obj(resp_payload)

        logger.debug(f"<<< get_wallet_did -> {did}")
        return did
