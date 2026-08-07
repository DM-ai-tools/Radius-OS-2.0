"""Seed roles, permissions, and demo users — idempotent upsert for role catalog."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, ClientDigitalProfile, Role, RolePermission, User
from app.security import hash_password
from app.services.role_skills import ROLE_PHASE_PERMISSIONS, SEO_ROLES

USERS = [
    ("hod@trafficradius.com", "Harper Head of Dept", "head_of_department", "password123"),
    ("csm@trafficradius.com", "Alex CSM", "client_success_manager", "password123"),
    ("tech@trafficradius.com", "Sam Tech SEO", "technical_seo_specialist", "password123"),
    ("strategist@trafficradius.com", "Jordan Strategist", "seo_strategist", "password123"),
    ("content@trafficradius.com", "Casey Content", "content_seo_specialist", "password123"),
    ("onpage@trafficradius.com", "Oak On-Page", "on_page_seo_specialist", "password123"),
    ("schema@trafficradius.com", "Sky Schema", "structured_data_specialist", "password123"),
    ("qa@trafficradius.com", "Riley QA Lead", "seo_qa_lead", "password123"),
]


async def seed_all(db: AsyncSession, *, seed_demo_data: bool = True) -> None:
    """Ensure full role catalog + permissions always exist.

    Demo users (password123) and the demo client are only created when
    seed_demo_data is True — callers should pass False in any environment
    where this could run against a real/shared database.
    """
    role_map: dict[str, Role] = {}
    existing_roles = (await db.execute(select(Role))).scalars().all()
    for role in existing_roles:
        role_map[role.name] = role

    for meta in SEO_ROLES:
        if meta["name"] in role_map:
            role_map[meta["name"]].description = meta["description"]
            continue
        role = Role(name=meta["name"], description=meta["description"])
        db.add(role)
        role_map[meta["name"]] = role
    await db.flush()

    # Sync Phase 1–4 permissions from catalog
    for role_name, perms in ROLE_PHASE_PERMISSIONS.items():
        role = role_map.get(role_name)
        if not role:
            continue
        for agent_key, can_trigger, can_approve in perms:
            row = (
                await db.execute(
                    select(RolePermission).where(
                        RolePermission.role_id == role.id,
                        RolePermission.agent_key == agent_key,
                    )
                )
            ).scalar_one_or_none()
            if row:
                row.can_trigger = can_trigger
                row.can_approve = can_approve
            else:
                db.add(
                    RolePermission(
                        role_id=role.id,
                        agent_key=agent_key,
                        can_trigger=can_trigger,
                        can_approve=can_approve,
                    )
                )
    await db.flush()

    if seed_demo_data:
        for email, full_name, role_name, password in USERS:
            exists = (
                await db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if exists:
                continue
            role = role_map.get(role_name)
            if not role:
                continue
            db.add(
                User(
                    email=email,
                    full_name=full_name,
                    role_id=role.id,
                    hashed_password=hash_password(password),
                    is_active=True,
                )
            )

        client_exists = (await db.execute(select(Client).limit(1))).scalar_one_or_none()
        if not client_exists:
            client = Client(
                legal_name="Acme Retail Co.",
                display_name="Acme Retail Co.",
                primary_url="https://www.trafficradius.com",
                industry="Retail / Ecommerce",
                tier="B",
                status="onboarding",
            )
            db.add(client)
            await db.flush()
            db.add(ClientDigitalProfile(client_id=client.id))
    await db.flush()
