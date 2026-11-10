from datetime import UTC, datetime
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from attendance_service.database import (
    activity_projection,
    certificate,
    check_in,
    message_outbox,
    processed_event,
    registration_projection,
)
from attendance_service.domain import (
    require_activity_ended,
    require_check_in,
    require_confirmed_registration,
    require_published_activity,
)
from attendance_service.errors import ApiError, InvalidEvent
from attendance_service.events import ActivityChanged, RegistrationChanged, parse_event


def insert_for(connection: Connection, table):
    if connection.dialect.name == "postgresql":
        return pg_insert(table)
    if connection.dialect.name == "sqlite":
        return sqlite_insert(table)
    raise RuntimeError(f"unsupported Attendance database: {connection.dialect.name}")


def normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def isoformat(value: datetime | str) -> str:
    return normalize_datetime(value).isoformat().replace("+00:00", "Z")


class AttendanceRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def apply_event(
        self,
        event: RegistrationChanged | ActivityChanged | dict,
    ) -> bool:
        if isinstance(event, dict):
            event = parse_event(json.dumps(event).encode("utf-8"))
        serialized = event.model_dump_json(by_alias=True)
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

        with self.engine.begin() as connection:
            result = connection.execute(
                insert_for(connection, processed_event)
                .values(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    payload_hash=payload_hash,
                    processed_at=func.current_timestamp(),
                )
                .on_conflict_do_nothing(index_elements=["event_id"])
            )
            if result.rowcount == 0:
                existing_hash = connection.scalar(
                    select(processed_event.c.payload_hash).where(
                        processed_event.c.event_id == event.event_id
                    )
                )
                if existing_hash != payload_hash:
                    raise InvalidEvent("event ID was reused with a different payload")
                return False

            if isinstance(event, RegistrationChanged):
                self._apply_registration(connection, event)
            else:
                self._apply_activity(connection, event)
        return True

    def record_check_in(
        self,
        registration_id: UUID,
        participant_id: UUID,
        correlation_id: UUID,
    ) -> dict[str, str]:
        with self.engine.begin() as connection:
            registration = self._registration_for_update(connection, registration_id)
            self._require_registration(registration, participant_id)
            existing = connection.execute(
                select(check_in.c.checked_in_at).where(
                    check_in.c.registration_id == registration_id
                )
            ).first()
            if existing is not None:
                return {
                    "registrationId": str(registration_id),
                    "participantId": str(participant_id),
                    "checkedInAt": isoformat(existing.checked_in_at),
                }

            activity = self._activity_for_update(
                connection, registration["activity_id"]
            )
            require_confirmed_registration(
                registration["snapshot"].get("status"),
                "check-in",
            )
            require_published_activity(
                None if activity is None else activity["snapshot"].get("status"),
                "check-in",
            )

            checked_in_at = normalize_datetime(
                connection.scalar(select(func.current_timestamp()))
            )
            connection.execute(
                insert(check_in).values(
                    registration_id=registration_id,
                    checked_in_at=checked_in_at,
                )
            )
            self._enqueue_event(
                connection,
                event_type="attendance.recorded",
                registration_id=registration_id,
                activity_id=registration["activity_id"],
                organization_id=registration["organization_id"],
                participant_id=participant_id,
                occurred_at=checked_in_at,
                correlation_id=correlation_id,
                data={"checkedInAt": isoformat(checked_in_at)},
            )
            return {
                "registrationId": str(registration_id),
                "participantId": str(participant_id),
                "checkedInAt": isoformat(checked_in_at),
            }

    def issue_certificate(
        self,
        registration_id: UUID,
        participant_id: UUID,
        correlation_id: UUID,
    ) -> dict[str, str]:
        with self.engine.begin() as connection:
            registration = self._registration_for_update(connection, registration_id)
            self._require_registration(registration, participant_id)
            existing = connection.execute(
                select(certificate.c.id, certificate.c.issued_at).where(
                    certificate.c.registration_id == registration_id
                )
            ).first()
            if existing is not None:
                return self._certificate_response(
                    existing.id,
                    registration_id,
                    participant_id,
                    registration["activity_id"],
                    existing.issued_at,
                )

            require_confirmed_registration(
                registration["snapshot"].get("status"),
                "certificate",
            )

            activity = self._activity_for_update(
                connection, registration["activity_id"]
            )
            require_published_activity(
                None if activity is None else activity["snapshot"].get("status"),
                "certificate",
            )

            checked_in_at = connection.scalar(
                select(check_in.c.checked_in_at).where(
                    check_in.c.registration_id == registration_id
                )
            )
            require_check_in(checked_in_at)

            ends_at = activity["snapshot"].get("endsAt")
            database_now = normalize_datetime(
                connection.scalar(select(func.current_timestamp()))
            )
            require_activity_ended(
                None if ends_at is None else normalize_datetime(ends_at),
                database_now,
            )

            certificate_id = uuid4()
            issued_at = database_now
            connection.execute(
                insert(certificate).values(
                    id=certificate_id,
                    registration_id=registration_id,
                    issued_at=issued_at,
                )
            )
            self._enqueue_event(
                connection,
                event_type="certificate.issued",
                registration_id=registration_id,
                activity_id=registration["activity_id"],
                organization_id=registration["organization_id"],
                participant_id=participant_id,
                occurred_at=issued_at,
                correlation_id=correlation_id,
                data={
                    "certificateId": str(certificate_id),
                    "issuedAt": isoformat(issued_at),
                },
            )
            return self._certificate_response(
                certificate_id,
                registration_id,
                participant_id,
                registration["activity_id"],
                issued_at,
            )

    def get_certificate(
        self,
        registration_id: UUID,
        participant_id: UUID,
    ) -> dict[str, str]:
        with self.engine.connect() as connection:
            registration = connection.execute(
                select(registration_projection).where(
                    registration_projection.c.registration_id == registration_id
                )
            ).mappings().one_or_none()
            if (
                registration is None
                or registration["participant_id"] != participant_id
            ):
                raise ApiError(404, "NOT_FOUND", "Certificate not found.")
            issued = connection.execute(
                select(certificate.c.id, certificate.c.issued_at).where(
                    certificate.c.registration_id == registration_id
                )
            ).first()
            if issued is None:
                raise ApiError(404, "NOT_FOUND", "Certificate not found.")
            return self._certificate_response(
                issued.id,
                registration_id,
                participant_id,
                registration["activity_id"],
                issued.issued_at,
            )

    @staticmethod
    def _apply_registration(
        connection: Connection,
        event: RegistrationChanged,
    ) -> None:
        data = event.data
        statement = insert_for(connection, registration_projection).values(
            registration_id=data.registration_id,
            participant_id=data.participant_id,
            activity_id=data.activity_id,
            organization_id=data.organization_id,
            version=event.aggregate_version,
            snapshot=data.model_dump(mode="json", by_alias=True),
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["registration_id"],
                set_={
                    "participant_id": statement.excluded.participant_id,
                    "activity_id": statement.excluded.activity_id,
                    "organization_id": statement.excluded.organization_id,
                    "version": statement.excluded.version,
                    "snapshot": statement.excluded.snapshot,
                },
                where=statement.excluded.version > registration_projection.c.version,
            )
        )

    @staticmethod
    def _apply_activity(connection: Connection, event: ActivityChanged) -> None:
        data = event.data
        statement = insert_for(connection, activity_projection).values(
            activity_id=data.activity_id,
            version=event.aggregate_version,
            snapshot=data.model_dump(mode="json", by_alias=True),
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["activity_id"],
                set_={
                    "version": statement.excluded.version,
                    "snapshot": statement.excluded.snapshot,
                },
                where=statement.excluded.version > activity_projection.c.version,
            )
        )

    @staticmethod
    def _registration_for_update(
        connection: Connection,
        registration_id: UUID,
    ):
        return connection.execute(
            select(registration_projection)
            .where(registration_projection.c.registration_id == registration_id)
            .with_for_update()
        ).mappings().one_or_none()

    @staticmethod
    def _activity_for_update(connection: Connection, activity_id: UUID):
        return connection.execute(
            select(activity_projection)
            .where(activity_projection.c.activity_id == activity_id)
            .with_for_update()
        ).mappings().one_or_none()

    @staticmethod
    def _require_registration(registration, participant_id: UUID) -> None:
        if registration is None:
            raise ApiError(
                409,
                "ELIGIBILITY_NOT_READY",
                "Registration eligibility facts have not been projected.",
            )
        if registration["participant_id"] != participant_id:
            raise ApiError(404, "NOT_FOUND", "Registration not found.")

    @staticmethod
    def _certificate_response(
        certificate_id: UUID,
        registration_id: UUID,
        participant_id: UUID,
        activity_id: UUID,
        issued_at: datetime | str,
    ) -> dict[str, str]:
        return {
            "id": str(certificate_id),
            "registrationId": str(registration_id),
            "participantId": str(participant_id),
            "activityId": str(activity_id),
            "issuedAt": isoformat(issued_at),
        }

    @staticmethod
    def _enqueue_event(
        connection: Connection,
        *,
        event_type: str,
        registration_id: UUID,
        activity_id: UUID,
        organization_id: UUID,
        participant_id: UUID,
        occurred_at: datetime,
        correlation_id: UUID,
        data: dict,
    ) -> None:
        event_id = uuid4()
        if event_type == "attendance.recorded":
            data = {
                "registrationId": str(registration_id),
                "activityId": str(activity_id),
                "organizationId": str(organization_id),
                "participantId": str(participant_id),
                **data,
            }
        else:
            data = {
                "certificateId": data["certificateId"],
                "registrationId": str(registration_id),
                "activityId": str(activity_id),
                "organizationId": str(organization_id),
                "participantId": str(participant_id),
                "issuedAt": data["issuedAt"],
            }

        payload = {
            "eventId": str(event_id),
            "eventType": event_type,
            "schemaVersion": "1.0.0",
            "aggregateId": str(registration_id),
            "aggregateVersion": 1 if event_type == "attendance.recorded" else 2,
            "correlationId": str(correlation_id),
            "occurredAt": isoformat(occurred_at),
            "producer": "attendance-certification",
            "data": data,
        }
        connection.execute(
            insert(message_outbox).values(
                event_id=event_id,
                event_type=event_type,
                routing_key=event_type,
                correlation_id=correlation_id,
                payload=payload,
                created_at=occurred_at,
            )
        )
