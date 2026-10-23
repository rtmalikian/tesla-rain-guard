"""Live fleet snapshot: no-wake reads of each configured vehicle, distilled
into a compact state object the guard, the LLM context builder, and the
command executor share.

Window state comes from vehicle_data's window fields (0 = closed) when the
car reports them; otherwise "unknown", which the guard treats as
not-confirmed-closed (a close command on already-closed windows is a
harmless no-op, so we close rather than risk a wet interior)."""
from __future__ import annotations

from dataclasses import dataclass, field

# canonical window states
CLOSED, VENTED, OPEN, UNKNOWN = "closed", "vented", "open", "unknown"


@dataclass
class VehicleState:
    name: str
    vin: str
    display_name: str = ""
    battery_level: int = 0
    charging_state: str = "Disconnected"
    inside_temp_c: float | None = None
    outside_temp_c: float | None = None
    locked: bool = True
    odometer: float = 0.0
    shift_state: str | None = None          # P | D | R | N
    windows: str = UNKNOWN                   # closed | vented | open | unknown
    lat: float | None = None
    lon: float | None = None
    reachable: bool = True
    error: str = ""

    @property
    def parked(self) -> bool:
        return self.shift_state in (None, "P")


def _climate(d: dict) -> dict:
    return d.get("climate_state") or {}


def _drive(d: dict) -> dict:
    return d.get("drive_state") or {}


def _charge(d: dict) -> dict:
    return d.get("charge_state") or {}


def _window_state(data: dict) -> str:
    """Read the four window positions; Tesla reports 0=closed when present."""
    vs = data.get("vehicle_state") or {}
    fields = ("fd_window", "fp_window", "rd_window", "rp_window")
    values = [vs.get(f) for f in fields]
    if all(v is None for v in values):
        return UNKNOWN
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return UNKNOWN
    if all(v == 0 for v in nums):
        return CLOSED
    if all(v in (0, 1) for v in nums) and any(v == 1 for v in nums):
        return VENTED          # cracked/vent position
    return OPEN


def snapshot_fleet(client, vehicles: list) -> FleetSnapshot:
    """Read every configured vehicle (no-wake reads only) and build a snapshot."""
    snap = FleetSnapshot()
    for vc in vehicles:
        try:
            data = client.vehicle_data(vc.vin)
        except Exception as exc:  # never let one car break the whole fleet view
            snap.vehicles.append(VehicleState(name=vc.name, vin=vc.vin,
                                              reachable=False, error=str(exc)[:120]))
            continue
        cs, cl, dr = _charge(data), _climate(data), _drive(data)
        snap.vehicles.append(VehicleState(
            name=vc.name,
            vin=vc.vin,
            display_name=str(data.get("display_name") or ""),
            battery_level=int(cs.get("battery_level") or 0),
            charging_state=str(cs.get("charging_state") or "Disconnected"),
            inside_temp_c=cl.get("inside_temp"),
            outside_temp_c=cl.get("outside_temp"),
            locked=bool(data.get("locked", True)),
            odometer=float(dr.get("odometer") or 0.0),
            shift_state=dr.get("shift_state"),
            windows=_window_state(data),
            lat=dr.get("latitude"),
            lon=dr.get("longitude"),
        ))
    return snap


@dataclass
class FleetSnapshot:
    vehicles: list[VehicleState] = field(default_factory=list)

    def brief(self) -> str:
        """Compact text the LLM receives as context."""
        lines = []
        for v in self.vehicles:
            if not v.reachable:
                lines.append(f"- {v.name}: unreachable ({v.error})")
                continue
            lines.append(
                f"- {v.name}: windows {v.windows}, "
                f"{'parked' if v.parked else 'driving'}, "
                f"inside {v.inside_temp_c}°C, battery {v.battery_level}%, "
                f"{'locked' if v.locked else 'unlocked'}"
            )
        return "\n".join(lines) or "(no vehicles)"


def find_vehicle(snap: FleetSnapshot, mention: str) -> VehicleState | None:
    """Best-effort vehicle resolution from a free-text mention."""
    needle = mention.strip().lower()
    if not needle:
        return None
    for v in snap.vehicles:
        names = [v.name.lower()] + ([v.display_name.lower()] if v.display_name else [])
        for nm in names:
            if nm and (needle in nm or nm in needle):
                return v
    for v in snap.vehicles:
        for m in ("model 3", "model y", "model s", "model x"):
            if m in needle and m[-1] in v.name.lower():
                return v
    return None
