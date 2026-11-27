"""Versioned draw-difference rehearsal, not an authenticated 2027 draw.

Stable slot identifiers must come from a documented source contract. Never
match slots by player name, infer a lucky-loser designation or overwrite the
original forecast when participants change.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json


@dataclass(frozen=True)
class DrawSlot:
    slot_id: str
    match_id: int | None
    player_1_id: int | None
    player_2_id: int | None
    entry_1: str
    entry_2: str
    scheduled_start: datetime | None
    status: str


@dataclass(frozen=True)
class DrawSnapshot:
    edition_id: str
    source: str
    revision_id: str
    observed_at: datetime
    slots: tuple[DrawSlot, ...]


def _aware(value):
    return isinstance(value, datetime) and value.utcoffset() is not None


def _inventory(snapshot):
    if not all(isinstance(v, str) and v.strip() for v in (
            snapshot.edition_id, snapshot.source, snapshot.revision_id)):
        raise ValueError("versioned source and edition required")
    if not _aware(snapshot.observed_at):
        raise ValueError("aware observation time required")
    result = {}
    for slot in snapshot.slots:
        if not slot.slot_id or slot.slot_id in result:
            raise ValueError("missing or duplicate stable draw slot")
        if slot.status not in ("SCHEDULED", "TBD", "BYE", "CANCELLED"):
            raise ValueError("unknown slot status")
        for player, entry in ((slot.player_1_id, slot.entry_1),
                              (slot.player_2_id, slot.entry_2)):
            if entry not in ("DIRECT", "QUALIFIER", "LUCKY_LOSER", "WILD_CARD",
                             "BYE", "TBD", "UNKNOWN"):
                raise ValueError("unknown entry designation")
            if player is not None and (type(player) is not int or player <= 0):
                raise ValueError("invalid player identifier")
            if entry in ("BYE", "TBD") and player is not None:
                raise ValueError("placeholder cannot carry a player")
        if slot.player_1_id is not None and slot.player_1_id == slot.player_2_id:
            raise ValueError("participant collision")
        if slot.scheduled_start is not None and not _aware(slot.scheduled_start):
            raise ValueError("aware scheduled start required")
        result[slot.slot_id] = slot
    return result


def _digest(snapshot):
    payload = asdict(snapshot)
    payload["slots"] = sorted(payload["slots"], key=lambda row:row["slot_id"])
    return sha256(json.dumps(payload,sort_keys=True,default=str,
                            separators=(",",":")).encode()).hexdigest()


def compare_synthetic_draws(before, after):
    """Compare two synthetic snapshots; actual provider acceptance stays closed."""
    old, new = _inventory(before), _inventory(after)
    if (before.edition_id != after.edition_id or before.source != after.source
            or before.revision_id == after.revision_id
            or before.observed_at >= after.observed_at):
        raise ValueError("same edition/source and advancing revision required")
    changes = []
    for key in sorted(old.keys() | new.keys()):
        a,b = old.get(key),new.get(key)
        if a is None:
            flags = ["NEW_SLOT"]
        elif b is None:
            flags = ["SLOT_MISSING_NOT_PROVEN_WITHDRAWAL"]
        else:
            flags = []
            if a.match_id != b.match_id:
                flags.append("CANONICAL_MATCH_CHANGED")
            if (a.player_1_id,a.player_2_id) != (b.player_1_id,b.player_2_id):
                if (a.player_1_id,a.player_2_id) == (b.player_2_id,b.player_1_id):
                    flags.append("PARTICIPANT_ORIENTATION_CHANGED")
                else:
                    flags.append("PARTICIPANT_REPLACEMENT")
                for prior,current,entry in (
                        (a.player_1_id,b.player_1_id,b.entry_1),
                        (a.player_2_id,b.player_2_id,b.entry_2)):
                    if prior != current and current is not None and entry in (
                            "LUCKY_LOSER","QUALIFIER","WILD_CARD"):
                        flags.append("DECLARED_"+entry)
            if a.scheduled_start != b.scheduled_start:
                flags.append("RESCHEDULED")
            if a.status != b.status:
                flags.append("STATUS_CHANGED_"+b.status)
            if (a.entry_1,a.entry_2) != (b.entry_1,b.entry_2):
                flags.append("ENTRY_DESIGNATION_CHANGED")
        if flags:
            changes.append({"slot_id":key,"changes":sorted(set(flags)),
                            "original":asdict(a) if a else None,
                            "revised":asdict(b) if b else None,
                            "preserve_original_forecast":True,
                            "requires_reconciliation":True})
    return {"classification":"SYNTHETIC_DRAW_DIFF_ONLY",
            "before_sha256":_digest(before),"after_sha256":_digest(after),
            "before_slots":len(old),"after_slots":len(new),"changes":changes,
            "actual_2027_draw_verified":False,"can_promote":False}
