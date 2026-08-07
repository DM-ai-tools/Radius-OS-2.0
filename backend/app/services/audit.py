from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditTrail


async def log_event(
    db: AsyncSession,
    *,
    client_id: UUID,
    actor_type: str,
    event_type: str,
    event_detail: dict | None = None,
    actor_id: UUID | None = None,
) -> AuditTrail:
    row = AuditTrail(
        client_id=client_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type=event_type,
        event_detail=event_detail or {},
    )
    db.add(row)
    await db.flush()
    return row
