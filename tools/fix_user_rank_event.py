from __future__ import annotations

import os

"""
Fix user rank events in the database.

This script fixes the format of RANK type events by:
1. Removing 'Rank.' prefix from scorerank values (e.g., 'Rank.X' -> 'X')
2. Converting mode values from enum format to string format (e.g., 'GameMode.OSU' -> 'osu')

Usage:
    python tools/fix_user_rank_event.py [--dry-run]

Options:
    --dry-run    Show what would be changed without making actual changes
"""

from argparse import ArgumentParser
import asyncio
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.events import Event, EventType
from app.dependencies.database import engine
from app.log import logger

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger.remove()


def fix_scorerank(payload: dict) -> tuple[dict, bool]:
    """
    Fix scorerank field by removing 'Rank.' prefix.

    Returns:
        tuple: (fixed_payload, was_changed)
    """
    fixed_payload = payload.copy()
    changed = False

    if "scorerank" in fixed_payload:
        scorerank = str(fixed_payload["scorerank"])
        if scorerank.startswith("Rank."):
            fixed_payload["scorerank"] = scorerank.replace("Rank.", "")
            changed = True

    return fixed_payload, changed


def fix_mode(payload: dict) -> tuple[dict, bool]:
    """
    Fix mode field by converting from enum format to string format.

    Returns:
        tuple: (fixed_payload, was_changed)
    """
    fixed_payload = payload.copy()
    changed = False

    if "mode" in fixed_payload:
        mode = str(fixed_payload["mode"])
        # Map GameMode enum strings to their values
        mode_mapping = {
            "GameMode.OSU": "osu!",
            "GameMode.TAIKO": "osu!taiko",
            "GameMode.FRUITS": "osu!catch",
            "GameMode.MANIA": "osu!mania",
            "GameMode.OSURX": "osu!relax",
            "GameMode.OSUAP": "osu!autopilot",
            "GameMode.TAIKORX": "taiko relax",
            "GameMode.FRUITSRX": "catch relax",
        }

        if mode in mode_mapping:
            fixed_payload["mode"] = mode_mapping[mode]
            changed = True

    return fixed_payload, changed


def fix_event_payload(payload: dict) -> tuple[dict, bool]:
    """
    Fix both scorerank and mode fields in event payload.

    Returns:
        tuple: (fixed_payload, was_changed)
    """
    fixed_payload = payload.copy()
    total_changed = False

    # Fix scorerank
    fixed_payload, scorerank_changed = fix_scorerank(fixed_payload)

    # Fix mode
    fixed_payload, mode_changed = fix_mode(fixed_payload)

    total_changed = scorerank_changed or mode_changed

    return fixed_payload, total_changed


async def get_rank_events(session: AsyncSession) -> list[Event]:
    """Get all RANK type events from the database."""
    result = await session.exec(select(Event).where(Event.type == EventType.RANK))
    return list(result.all())


async def update_event(session: AsyncSession, event: Event, new_payload: dict) -> None:
    """Update an event's payload in the database."""


async def main():
    parser = ArgumentParser(description="Fix user rank events in the database")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be changed without making actual changes"
    )
    args = parser.parse_args()

    print("🔍 Fetching RANK events from database...")

    async with AsyncSession(engine) as session:
        events = await get_rank_events(session)
        print(f"📊 Found {len(events)} RANK events")

        if not events:
            print("✅ No RANK events found. Nothing to fix.")
            return

        events_to_fix = []

        # Analyze events
        for event in events:
            try:
                payload = event.event_payload
                if not isinstance(payload, dict):
                    print(f"⚠️  Event {event.id}: payload is not a dict, skipping")
                    continue

                fixed_payload, needs_fix = fix_event_payload(payload)

                if needs_fix:
                    events_to_fix.append((event, fixed_payload, payload))

            except Exception as e:
                print(f"❌ Error processing event {event.id}: {e}")
                continue

        print(f"🔧 Found {len(events_to_fix)} events that need fixing")

        if not events_to_fix:
            print("✅ All RANK events are already in correct format!")
            return

        # Show changes
        for event, fixed_payload, original_payload in events_to_fix:
            print(f"\n📝 Event {event.id}:")
            print(f"   Original: {original_payload}")
            print(f"   Fixed:    {fixed_payload}")

            # Show specific changes
            changes = []
            if (
                "scorerank" in original_payload
                and "scorerank" in fixed_payload
                and original_payload["scorerank"] != fixed_payload["scorerank"]
            ):
                changes.append(f"scorerank: {original_payload['scorerank']} → {fixed_payload['scorerank']}")

            if (
                "mode" in original_payload
                and "mode" in fixed_payload
                and original_payload["mode"] != fixed_payload["mode"]
            ):
                changes.append(f"mode: {original_payload['mode']} → {fixed_payload['mode']}")

            if changes:
                print(f"   Changes:  {', '.join(changes)}")

        if args.dry_run:
            print(f"\n🧪 DRY RUN: Would fix {len(events_to_fix)} events")
            print("   Run without --dry-run to apply changes")
            return

        # Apply changes
        print(f"\n💾 Applying fixes to {len(events_to_fix)} events...")

        try:
            for event, fixed_payload, _ in events_to_fix:
                event.event_payload = fixed_payload

            await session.commit()
            print(f"✅ Successfully fixed {len(events_to_fix)} events!")

        except Exception as e:
            await session.rollback()
            print(f"❌ Error applying fixes: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
