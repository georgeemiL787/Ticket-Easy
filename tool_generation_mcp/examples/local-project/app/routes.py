from fastapi import APIRouter, Depends
from .models import Record, RequestInput, ServiceRequest
from .services import lookup_record

router = APIRouter(prefix="/records")


def check_owner():
    raise NotImplementedError("The owner-verification process has not been defined")


@router.get("/{record_id}", response_model=Record, dependencies=[Depends(check_owner)])
def get_record(record_id: str):
    return lookup_record(record_id)


@router.post("/requests", response_model=ServiceRequest, status_code=201, dependencies=[Depends(check_owner)])
def create_request(payload: RequestInput):
    return ServiceRequest(request_id="example", record_id=payload.record_id)
