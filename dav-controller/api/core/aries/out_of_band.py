from typing import Dict, List, Union
from pydantic import BaseModel, Field
from .service_decorator import OOBServiceDecorator


class OutOfBandPresentProofAttachment(BaseModel):
    id: str = Field(alias="@id")
    mime_type: str = Field(default="application/json", alias="mime-type")
    data: Dict

    class Config:
        allow_population_by_field_name = True


class OutOfBandMessage(BaseModel):
    # https://github.com/hyperledger/aries-rfcs/blob/main/features/0434-outofband
    id: str = Field(alias="@id")
    type: str = Field(
        default="https://didcomm.org/out-of-band/1.1/invitation",
        alias="@type",
    )
    goal_code: str = Field(default="request-proof")
    label: str = Field(default="Out-of-Band present-proof authorization request")
    request_attachments: List[OutOfBandPresentProofAttachment] = Field(
        alias="requests~attach"
    )
    services: List[Union[OOBServiceDecorator, str]] = Field(alias="services")

    class Config:
        allow_population_by_field_name = True
