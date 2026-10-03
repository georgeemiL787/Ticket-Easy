from pydantic import BaseModel


class Record(BaseModel):
    record_id: str
    status: str


class RequestInput(BaseModel):
    record_id: str
    message: str


class ServiceRequest(BaseModel):
    request_id: str
    record_id: str
