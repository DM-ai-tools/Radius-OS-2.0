"""Clear CDP summaries, findings, chat cards, and related rows from a phase onward.

Usage:
  python scripts/clear_phases_from.py --from-phase 4 --client "Click Trends"
  python scripts/clear_phases_from.py --from-phase 5 --to-phase 6 --client "Click Trends"
  python scripts/clear_phases_from.py --from-phase 5 --client-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Phase → agent keys (runtime RolePermission keys)
PHASE_AGENTS: dict[int, list[str]] = {
    4: ["competitor_market_agent"],
    5: ["search_demand"],
    6: ["content_strategy", "site_architecture"],
    7: ["technical_seo"],
    8: ["content_audit"],
    9: ["content_planning"],
    10: ["content_production"],
    11: ["on_page_seo"],
    12: ["publishing"],
}

# Phase → CDP summary + status column pairs
PHASE_CDP_FIELDS: dict[int, list[tuple[str, str]]] = {
    4: [("competitive_landscape_summary", "competitor_status")],
    5: [("search_demand_summary", "search_demand_status")],
    6: [
        ("seo_strategy_summary", "seo_strategy_status"),
        ("site_architecture_summary", "site_architecture_status"),
    ],
    7: [("technical_seo_summary", "technical_seo_status")],
    8: [("content_audit_summary", "content_audit_status")],
    9: [("content_planning_summary", "content_planning_status")],
    10: [("content_production_summary", "content_production_status")],
    11: [("on_page_seo_summary", "on_page_seo_status")],
    12: [("publishing_summary", "publishing_status")],
}

PHASE_CARD_TYPES: dict[int, list[str]] = {
    4: ["competitor_landscape"],
    5: ["search_demand_report"],
    6: ["content_strategy_report", "site_architecture_blueprint"],
    7: ["technical_seo_report"],
    8: ["content_audit_report"],
    9: ["content_planning_report"],
    10: ["content_production_report"],
    11: ["on_page_seo_report"],
    12: ["publishing_report"],
}


def _database_url() -> str:
    env = Path(__file__).resolve().parents[2] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("DATABASE_URL missing from .env")


def _agents_from_phase(from_phase: int, to_phase: int | None = None) -> list[str]:
    end = to_phase if to_phase is not None else 12
    out: list[str] = []
    for phase in range(from_phase, end + 1):
        out.extend(PHASE_AGENTS.get(phase, []))
    return sorted(set(out))


def _cdp_fields_from_phase(from_phase: int, to_phase: int | None = None) -> list[tuple[str, str]]:
    end = to_phase if to_phase is not None else 12
    out: list[tuple[str, str]] = []
    for phase in range(from_phase, end + 1):
        out.extend(PHASE_CDP_FIELDS.get(phase, []))
    return out


def _card_types_from_phase(from_phase: int, to_phase: int | None = None) -> list[str]:
    end = to_phase if to_phase is not None else 12
    out: list[str] = []
    for phase in range(from_phase, end + 1):
        out.extend(PHASE_CARD_TYPES.get(phase, []))
    return sorted(set(out))


async def _resolve_client_id(session: AsyncSession, *, name: str | None, client_id: str | None) -> UUID:
    if client_id:
        return UUID(client_id)
    if not name:
        raise SystemExit("Provide --client or --client-id")
    row = await session.execute(
        text("SELECT id FROM clients WHERE display_name ILIKE :name LIMIT 1"),
        {"name": name.strip()},
    )
    cid = row.scalar()
    if not cid:
        raise SystemExit(f"Client not found: {name!r}")
    return UUID(str(cid))


async def clear_phases(
    *,
    client_name: str | None,
    client_id: str | None,
    from_phase: int,
    to_phase: int | None = None,
) -> None:
    end = to_phase if to_phase is not None else 12
    if end < from_phase:
        raise SystemExit("--to-phase must be >= --from-phase")
    agents = _agents_from_phase(from_phase, to_phase)
    cdp_fields = _cdp_fields_from_phase(from_phase, to_phase)
    card_types = _card_types_from_phase(from_phase, to_phase)

    engine = create_async_engine(_database_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        cid = await _resolve_client_id(session, name=client_name, client_id=client_id)
        print(f"client_id={cid} from_phase={from_phase} to_phase={end}")

        # --- CDP summaries + statuses ----------------------------------------
        set_bits = ["updated_at = NOW()"]
        for summary_col, status_col in cdp_fields:
            set_bits.append(f"{summary_col} = '{{}}'::json")
            set_bits.append(f"{status_col} = 'not_started'")
        sql = f"UPDATE client_digital_profiles SET {', '.join(set_bits)} WHERE client_id = :cid"
        r = await session.execute(text(sql), {"cid": str(cid)})
        print(f"cdp_columns_reset={r.rowcount}")

        # --- Competitor tables (phase 4+) ------------------------------------
        if from_phase <= 4:
            comp_rows = await session.execute(
                text("SELECT id FROM competitor_profiles WHERE client_id = :cid"),
                {"cid": str(cid)},
            )
            comp_ids = [str(row[0]) for row in comp_rows]
            if comp_ids:
                await session.execute(
                    text(
                        "DELETE FROM competitor_rankings WHERE competitor_profile_id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": comp_ids},
                )
                await session.execute(
                    text(
                        "DELETE FROM backlink_snapshots WHERE competitor_profile_id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": comp_ids},
                )
                r = await session.execute(
                    text("DELETE FROM competitor_profiles WHERE client_id = :cid"),
                    {"cid": str(cid)},
                )
                print(f"competitor_profiles_deleted={r.rowcount}")

        # --- Findings + validations + jobs -----------------------------------
        r = await session.execute(
            text(
                "DELETE FROM findings_ledger WHERE client_id = :cid AND agent_key IN :agents"
            ).bindparams(bindparam("agents", expanding=True)),
            {"cid": str(cid), "agents": agents},
        )
        print(f"findings_deleted={r.rowcount}")

        r = await session.execute(
            text(
                "DELETE FROM phase_validations WHERE client_id = :cid AND agent_key IN :agents"
            ).bindparams(bindparam("agents", expanding=True)),
            {"cid": str(cid), "agents": agents},
        )
        print(f"phase_validations_deleted={r.rowcount}")

        r = await session.execute(
            text(
                """
                DELETE FROM agent_jobs
                WHERE session_id IN (SELECT id FROM chat_sessions WHERE client_id = :cid)
                  AND agent_key IN :agents
                """
            ).bindparams(bindparam("agents", expanding=True)),
            {"cid": str(cid), "agents": agents},
        )
        print(f"agent_jobs_deleted={r.rowcount}")

        # --- Chat messages (cards + agent turns) -----------------------------
        r = await session.execute(
            text(
                """
                DELETE FROM chat_messages
                WHERE session_id IN (SELECT id FROM chat_sessions WHERE client_id = :cid)
                  AND (
                    agent_key IN :agents
                    OR structured_payload->>'card_type' IN :cards
                    OR (
                      structured_payload->>'card_type' = 'phase_validation_report'
                      AND agent_key IN :agents
                    )
                  )
                """
            ).bindparams(
                bindparam("agents", expanding=True),
                bindparam("cards", expanding=True),
            ),
            {"cid": str(cid), "agents": agents, "cards": card_types},
        )
        print(f"chat_messages_deleted={r.rowcount}")

        await session.commit()

    # --- Redis competitor cache --------------------------------------------
    if from_phase <= 4:
        try:
            from app.services.cache import cache_delete, competitor_cache_key

            cache_delete(competitor_cache_key(str(cid)))
            print("competitor_cache_cleared=1")
        except Exception as exc:  # noqa: BLE001
            print(f"competitor_cache_cleared=0 ({exc})")

    # --- Recompute readiness (phases 1–4 only) -----------------------------
    async with Session() as session:
        from app.services.readiness import recompute_readiness

        profile = await recompute_readiness(session, cid)
        await session.commit()
        print(
            json.dumps(
                {
                    "overall_readiness_score": float(profile.overall_readiness_score or 0),
                    "discovery_status": profile.discovery_status,
                    "tracking_status": profile.tracking_status,
                    "website_status": profile.website_status,
                    "competitor_status": profile.competitor_status,
                    "search_demand_status": profile.search_demand_status,
                    "ready_for_phase5": profile.ready_for_phase5,
                }
            )
        )

    await engine.dispose()
    print("done")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear client data from a phase onward.")
    parser.add_argument("--from-phase", type=int, default=4, help="First phase to clear (default: 4)")
    parser.add_argument(
        "--to-phase",
        type=int,
        default=None,
        help="Last phase to clear inclusive (default: 12 — all phases from --from-phase onward)",
    )
    parser.add_argument("--client", type=str, default="Click Trends", help="Client display_name")
    parser.add_argument("--client-id", type=str, default=None, help="Client UUID (overrides --client)")
    args = parser.parse_args()
    if args.from_phase < 1 or args.from_phase > 12:
        raise SystemExit("--from-phase must be between 1 and 12")
    if args.to_phase is not None and (args.to_phase < 1 or args.to_phase > 12):
        raise SystemExit("--to-phase must be between 1 and 12")
    asyncio.run(
        clear_phases(
            client_name=None if args.client_id else args.client,
            client_id=args.client_id,
            from_phase=args.from_phase,
            to_phase=args.to_phase,
        )
    )


if __name__ == "__main__":
    main()
