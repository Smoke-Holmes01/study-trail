"""MySQL storage types; API values remain UUIDs and UTC-aware datetimes."""
from datetime import UTC
from uuid import UUID as PythonUUID

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME


class UTCDateTime(sa.TypeDecorator):
    impl = DATETIME
    cache_ok = True

    def __init__(self, **kwargs):
        super().__init__(fsp=6)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTC-aware datetime required")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


JSON = sa.JSON


class UUID(sa.TypeDecorator):
    impl = sa.Uuid
    cache_ok = True

    def __init__(self, as_uuid=True):
        super().__init__(as_uuid=True, native_uuid=False)

    def process_bind_param(self, value, dialect):
        return PythonUUID(value) if isinstance(value, str) else value


class UUIDList(sa.TypeDecorator):
    impl = sa.JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return [str(item) for item in value] if value is not None else None

    def process_result_value(self, value, dialect):
        return [PythonUUID(item) for item in value] if value is not None else None
