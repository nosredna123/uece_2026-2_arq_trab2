import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from demo_web.models import Activity, ActivityInput, Event, EventInput, Money
from demo_web.presentation import ServiceFailure


@dataclass
class DemoPreparation:
    run_id: UUID
    created_at: datetime
    event_input: EventInput
    paid_activity_input: ActivityInput
    free_activity_input: ActivityInput
    command_keys: dict[str, str] = field(
        default_factory=lambda: {
            step: secrets.token_urlsafe(32)
            for step in ("event", "paid_activity", "free_activity", "publish")
        }
    )
    event: Event | None = None
    paid_activity: Activity | None = None
    free_activity: Activity | None = None
    published_event: Event | None = None
    failure: ServiceFailure | None = None
    failure_step: str | None = None
    failure_retryable: bool = False

    @property
    def complete(self) -> bool:
        return self.published_event is not None


def create_preparation(
    *,
    run_id: UUID,
    organization_id: UUID,
    organization_label: str,
    settings,
    now: datetime,
) -> DemoPreparation:
    started_at = now.astimezone(timezone.utc)
    event_start = started_at + timedelta(seconds=min(30, settings.paid_start_delay_seconds // 2))
    paid_start = started_at + timedelta(seconds=settings.paid_start_delay_seconds)
    paid_end = paid_start + timedelta(seconds=settings.activity_duration_seconds)
    free_start = started_at + timedelta(seconds=settings.free_start_delay_seconds)
    free_end = free_start + timedelta(seconds=settings.activity_duration_seconds)
    event_end = free_end + timedelta(seconds=60)

    return DemoPreparation(
        run_id=run_id,
        created_at=started_at,
        event_input=EventInput(
            organizationId=organization_id,
            title=(
                f"[APRESENTAÇÃO][SIMULADO][{organization_label}] "
                f"Jornada de Engenharia de Software · {str(run_id)[:8]}"
            ),
            description=(
                "Evento acadêmico fictício para demonstração. "
                "Os registros de execuções anteriores são mantidos."
            ),
            startsAt=event_start,
            endsAt=event_end,
        ),
        paid_activity_input=ActivityInput(
            title=(
                f"[FLUXO PAGO][SIMULADO][{organization_label}] "
                f"Oficina de Engenharia de Software · {str(run_id)[:8]}"
            ),
            startsAt=paid_start,
            endsAt=paid_end,
            location="Sala de demonstração A",
            responsible=f"Equipe acadêmica simulada — {organization_label}",
            capacity=10,
            price=Money(amountMinor=2500, currency="BRL"),
        ),
        free_activity_input=ActivityInput(
            title=(
                f"[LISTA DE ESPERA][SIMULADO][{organization_label}] "
                f"Oficina de Sistemas Autoadaptativos · {str(run_id)[:8]}"
            ),
            startsAt=free_start,
            endsAt=free_end,
            location="Sala de demonstração B",
            responsible=f"Equipe acadêmica simulada — {organization_label}",
            capacity=1,
            price=Money(amountMinor=0, currency="BRL"),
        ),
    )


def countdown_label(starts_at: datetime, ends_at: datetime, *, now: datetime) -> str:
    current_time = now.astimezone(timezone.utc)
    if current_time < starts_at:
        seconds = int((starts_at - current_time).total_seconds())
        minutes, remaining_seconds = divmod(seconds, 60)
        return f"starts in {minutes}m {remaining_seconds}s"
    if current_time < ends_at:
        seconds = int((ends_at - current_time).total_seconds())
        minutes, remaining_seconds = divmod(seconds, 60)
        return f"in progress by schedule; ends in {minutes}m {remaining_seconds}s"
    return "scheduled window has closed"
