# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Marc Duclusaud

"""Single-motor zeroing / Homing Offset debug helper.

Typical workflow (one joint at a time):

  1. use 11          focus motor 11
  2. unlock          Torque Enable = 0 (required to edit Homing Offset)
  3. watch           live-print q / encoder / Homing Offset
     — hold the joint at CAD zero, tweak Homing Offset in Wizard (or `hset`)
       until q ≈ 0° and encoder ≈ 180°
  4. lock            Torque Enable = 1, hold present angle (keeps zero while you move on)
  5. next            focus the next leg ID and repeat

From the repo root (use Miniconda python on this machine):

  C:\\ProgramData\\miniconda3\\python.exe src\\debug\\motor_zero.py
  C:\\ProgramData\\miniconda3\\python.exe src\\debug\\motor_zero.py --port COM3 --id 11

Notes
  - Homing Offset (EEPROM addr 20) can only be written with Torque Enable = 0.
  - "lock" here means Torque Enable ON in position mode (hold), not Operating Mode=0
    (current/torque control). Use `mode` only if you really need that.
"""

from __future__ import annotations

import argparse
import math
import sys
import time

MOTOR_TO_ID: dict[str, int] = {
    "left_hip_yaw": 11,
    "left_hip_roll": 12,
    "left_hip_pitch": 13,
    "left_knee": 14,
    "left_ankle_pitch": 15,
    "left_ankle_roll": 16,
    "right_hip_yaw": 21,
    "right_hip_roll": 22,
    "right_hip_pitch": 23,
    "right_knee": 24,
    "right_ankle_pitch": 25,
    "right_ankle_roll": 26,
}
ID_TO_MOTOR: dict[int, str] = {v: k for k, v in MOTOR_TO_ID.items()}

MOTOR_SIGN: dict[str, float] = {
    "left_hip_yaw": -1.0,
    "left_hip_roll": 1.0,
    "left_hip_pitch": -1.0,
    "left_knee": 1.0,
    "left_ankle_pitch": 1.0,
    "left_ankle_roll": -1.0,
    "right_hip_yaw": -1.0,
    "right_hip_roll": 1.0,
    "right_hip_pitch": 1.0,
    "right_knee": -1.0,
    "right_ankle_pitch": -1.0,
    "right_ankle_roll": -1.0,
}

LEG_IDS: list[int] = list(MOTOR_TO_ID.values())

TICKS_PER_REV = 4096
WIZARD_ZERO_TICK = 2048
DEG_PER_TICK = 360.0 / TICKS_PER_REV

# XL330 Operating Mode (addr 11)
MODE_NAMES = {
    0: "current",
    1: "velocity",
    3: "position",
    4: "ext_position",
    5: "current_based_position",
    16: "pwm",
}


def _import_rustypot():
    try:
        from rustypot import Xl330PyController

        return Xl330PyController
    except ImportError as exc:
        print(f"Python: {sys.executable}", file=sys.stderr)
        print(f"Cannot import rustypot: {exc}", file=sys.stderr)
        print(f'Install with:  "{sys.executable}" -m pip install rustypot', file=sys.stderr)
        raise SystemExit(1) from exc


def _name(motor_id: int) -> str:
    return ID_TO_MOTOR.get(motor_id, f"id_{motor_id}")


def _sign(motor_id: int) -> float:
    name = ID_TO_MOTOR.get(motor_id)
    return MOTOR_SIGN[name] if name in MOTOR_SIGN else 1.0


def _scalar(raw) -> float:
    if isinstance(raw, (list, tuple)):
        return float(raw[0])
    return float(raw)


def _from_hw(motor_id: int, hw_rad: float) -> float:
    return hw_rad * _sign(motor_id)


def _to_hw(motor_id: int, model_rad: float) -> float:
    return model_rad * _sign(motor_id)


def _wizard_deg(hw_rad: float) -> float:
    tick = WIZARD_ZERO_TICK + hw_rad * TICKS_PER_REV / (2.0 * math.pi)
    return (tick % TICKS_PER_REV) * DEG_PER_TICK


def _raw_ticks_from_hw(hw_rad: float) -> int:
    return int(round(WIZARD_ZERO_TICK + hw_rad * TICKS_PER_REV / (2.0 * math.pi))) % TICKS_PER_REV


def recommended_homing(motor_id: int, q_model_rad: float, homing_now: int) -> int:
    q_hw = q_model_rad / _sign(motor_id)
    delta = round(-q_hw * TICKS_PER_REV / (2.0 * math.pi))
    return homing_now + delta


class MotorZeroSession:
    def __init__(self, ctrl, focus_id: int) -> None:
        self.ctrl = ctrl
        self.focus_id = focus_id
        self.locked: set[int] = set()

    def focus(self, motor_id: int) -> None:
        self.focus_id = motor_id
        print(f"Focus -> id={motor_id} ({_name(motor_id)})")

    def read_status(self, motor_id: int | None = None) -> dict:
        mid = self.focus_id if motor_id is None else motor_id
        hw = _scalar(self.ctrl.read_present_position(mid))
        q = _from_hw(mid, hw)
        try:
            raw_tick = int(_scalar(self.ctrl.read_raw_present_position(mid))) % TICKS_PER_REV
        except Exception:
            raw_tick = _raw_ticks_from_hw(hw)
        homing = int(_scalar(self.ctrl.read_homing_offset(mid)))
        torque = bool(int(_scalar(self.ctrl.read_torque_enable(mid))))
        try:
            op_mode = int(_scalar(self.ctrl.read_operating_mode(mid)))
        except Exception:
            op_mode = -1
        h_set = recommended_homing(mid, q, homing)
        return {
            "id": mid,
            "name": _name(mid),
            "q_deg": math.degrees(q),
            "hw_rad": hw,
            "enc_deg": _wizard_deg(hw),
            "enc_tick": raw_tick,
            "homing": homing,
            "h_set": h_set,
            "torque": torque,
            "op_mode": op_mode,
            "sign": _sign(mid),
        }

    def print_status(self, st: dict | None = None, *, end: str = "\n") -> None:
        st = st or self.read_status()
        mode = MODE_NAMES.get(st["op_mode"], str(st["op_mode"]))
        torque = "ON " if st["torque"] else "OFF"
        ok = "  OK" if abs(st["q_deg"]) < 0.5 else ""
        line = (
            f"id={st['id']:2d} {_name(st['id']):20s}  "
            f"q={st['q_deg']:+7.2f}°  "
            f"enc={st['enc_deg']:6.1f}° ({st['enc_tick']:4d} tick)  "
            f"H={st['homing']:+6d}  H_set={st['h_set']:+6d}  "
            f"torque={torque}  mode={mode}{ok}"
        )
        print(line, end=end, flush=True)

    def unlock(self, motor_id: int | None = None) -> None:
        mid = self.focus_id if motor_id is None else motor_id
        self.ctrl.sync_write_torque_enable([mid], [False])
        self.locked.discard(mid)
        print(f"Torque OFF  id={mid} ({_name(mid)}) — Homing Offset editable now")

    def lock(self, motor_id: int | None = None) -> None:
        """Hold present pose with Torque Enable ON (position mode)."""
        mid = self.focus_id if motor_id is None else motor_id
        hw = _scalar(self.ctrl.read_present_position(mid))
        # Keep / restore position operating mode before enabling torque.
        try:
            mode = int(_scalar(self.ctrl.read_operating_mode(mid)))
            if mode != 3:
                self.ctrl.write_operating_mode(mid, 3)
                print(f"Operating Mode set to position (3) for id={mid}")
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: could not set operating mode: {exc}")
        self.ctrl.sync_write_goal_position([mid], [hw])
        self.ctrl.sync_write_torque_enable([mid], [True])
        self.locked.add(mid)
        st = self.read_status(mid)
        print(
            f"Torque ON   id={mid} ({_name(mid)}) holding q={st['q_deg']:+.2f}°  "
            f"(enc≈{st['enc_deg']:.1f}°)"
        )

    def _read_after_eeprom(self, motor_id: int) -> dict:
        """EEPROM writes keep the servo busy; retry status reads."""
        last_exc: Exception | None = None
        for delay in (0.15, 0.25, 0.4):
            time.sleep(delay)
            try:
                return self.read_status(motor_id)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        raise RuntimeError(f"bus still busy after Homing Offset write: {last_exc}") from last_exc

    def set_homing(self, value: int, motor_id: int | None = None) -> None:
        mid = self.focus_id if motor_id is None else motor_id
        if bool(int(_scalar(self.ctrl.read_torque_enable(mid)))):
            print("Refuse: Torque is ON. Run `unlock` first, then set Homing Offset.")
            return
        # Status Return Level is often 1 (ACK only on READ). write_* then times out
        # waiting for a WRITE reply even though EEPROM was updated. Use sync_write.
        self.ctrl.sync_write_homing_offset([mid], [int(value)])
        st = self._read_after_eeprom(mid)
        print(f"Wrote Homing Offset={value}  -> now H={st['homing']}  q={st['q_deg']:+.2f}°")
        self.print_status(st)

    def nudge_homing(self, delta: int) -> None:
        mid = self.focus_id
        now = int(_scalar(self.ctrl.read_homing_offset(mid)))
        self.set_homing(now + delta, mid)

    def apply_recommended(self) -> None:
        st = self.read_status()
        self.set_homing(st["h_set"], st["id"])

    def watch(self, hz: float = 5.0) -> None:
        mid = self.focus_id
        print(
            f"Watching id={mid} ({_name(mid)}) @ {hz:.0f} Hz — "
            "tweak Homing Offset in Wizard (torque OFF). Enter or Ctrl+C to stop."
        )
        print("Hint: leave Wizard connected only if it does not monopolize the COM port.")
        dt = 1.0 / hz
        try:
            while True:
                st = self.read_status(mid)
                print("\r" + " " * 120 + "\r", end="")
                self.print_status(st, end="")
                # Non-blocking-ish: short sleep; user hits Enter in another thread is hard on
                # Windows without select — use short loops and KeyboardInterrupt, or Enter
                # via msvcrt if available.
                if _stdin_ready():
                    _ = sys.stdin.readline()
                    print()
                    break
                time.sleep(dt)
        except KeyboardInterrupt:
            print()
            print("Watch stopped.")

    def next_id(self) -> None:
        if self.focus_id in LEG_IDS:
            i = LEG_IDS.index(self.focus_id)
            self.focus(LEG_IDS[(i + 1) % len(LEG_IDS)])
        else:
            self.focus(LEG_IDS[0])

    def prev_id(self) -> None:
        if self.focus_id in LEG_IDS:
            i = LEG_IDS.index(self.focus_id)
            self.focus(LEG_IDS[(i - 1) % len(LEG_IDS)])
        else:
            self.focus(LEG_IDS[-1])


def _stdin_ready() -> bool:
    try:
        import msvcrt

        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            # Consume rest of line if Enter
            if ch in ("\r", "\n"):
                return True
            # Any other key: keep watching unless Enter
            return False
    except ImportError:
        pass
    try:
        import select

        r, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(r)
    except Exception:
        return False


def print_help() -> None:
    print(
        """
Commands (focus one motor at a time):
  use <id>          focus motor (e.g. use 11)
  s / status        print q, encoder, Homing Offset once
  watch [hz]        live refresh (Enter / Ctrl+C to stop)
  unlock            Torque Enable = 0  (edit Homing Offset in Wizard)
  lock              Torque Enable = 1, hold present pose
  hset <ticks>      write Homing Offset (torque must be OFF)
  h +N / h -N       nudge Homing Offset by N ticks
  hrec              write recommended H_set for current pose
  next / prev       switch focus along leg ID list
  locked            list motors currently torque-locked by this session
  help
  quit              unlock nothing; leave locked motors as-is, exit

Workflow tip:
  use 11 -> unlock -> watch  -> (Wizard: set Homing Offset until q~0)
         -> lock   -> next   -> unlock -> watch -> ...
""".strip()
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Single XL330 motor zeroing helper")
    p.add_argument("--port", default="COM3")
    p.add_argument("--baud", type=int, default=1_000_000)
    p.add_argument("--id", type=int, default=11, help="initial focus motor ID")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    Xl330PyController = _import_rustypot()

    print("Microban motor_zero — single joint Homing Offset debug", flush=True)
    print(f"Opening {args.port} @ {args.baud} ...", flush=True)
    try:
        ctrl = Xl330PyController(serial_port=args.port, baudrate=args.baud, timeout=0.4)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to open {args.port}: {exc}", file=sys.stderr)
        return 1

    session = MotorZeroSession(ctrl, args.id)

    print_help()
    print()
    try:
        session.print_status()
    except Exception as exc:  # noqa: BLE001
        print(f"Cannot talk to id={args.id}: {exc}")
        print("Use `use <id>` after fixing the bus, or check wiring / baud.")

    try:
        while True:
            try:
                line = input(f"zero[{session.focus_id}]> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                try:
                    session.print_status()
                except Exception as exc:  # noqa: BLE001
                    print(f"Error: {exc}")
                continue

            parts = line.split()
            cmd = parts[0].lower()

            try:
                if cmd in ("q", "quit", "exit"):
                    break
                if cmd == "help":
                    print_help()
                elif cmd == "use" and len(parts) >= 2:
                    session.focus(int(parts[1]))
                    session.print_status()
                elif cmd in ("s", "status"):
                    session.print_status()
                elif cmd == "watch":
                    hz = float(parts[1]) if len(parts) >= 2 else 5.0
                    session.watch(hz=hz)
                elif cmd == "unlock":
                    session.unlock()
                    session.print_status()
                elif cmd == "lock":
                    session.lock()
                elif cmd == "hset" and len(parts) >= 2:
                    session.set_homing(int(parts[1]))
                elif cmd == "h" and len(parts) >= 2:
                    session.nudge_homing(int(parts[1]))
                elif cmd == "hrec":
                    session.apply_recommended()
                elif cmd == "next":
                    session.next_id()
                    session.print_status()
                elif cmd == "prev":
                    session.prev_id()
                    session.print_status()
                elif cmd == "locked":
                    if not session.locked:
                        print("(none locked by this session)")
                    else:
                        for mid in sorted(session.locked):
                            session.print_status(session.read_status(mid))
                else:
                    print("Unknown command. Type 'help'.")
            except Exception as exc:  # noqa: BLE001
                print(f"Error: {exc}")
    finally:
        print("Done. Locked motors left as-is:", sorted(session.locked) or "(none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
