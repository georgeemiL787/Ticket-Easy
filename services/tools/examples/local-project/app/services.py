from .models import Record


def lookup_record(record_id: str):
    return Record(record_id=record_id, status="example")
