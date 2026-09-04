# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Marc Duclusaud

"""Simple XL330 bus smoke test over U2D2 (Windows COM port).

scan        扫描电机 + 打印 Homing Offset 建议（H_set 列，需在 Wizard 手动写入）
offset      对在线电机重新打印 Homing Offset 建议
list        读取当前角度
on / off    扭矩开/关
move 14 10  ID14 相对转 +10°
goto 14 0   ID14 转到 0°
neutral     下发站立中立位
wiggle 14 20 3   ID14 小幅摆动 3 秒
quit        关扭矩退出


From the repo root:

    python src/debug/motor_test.py
    python src/debug/motor_test.py --port COM3 --baud 1000000

Assumes motors were configured per docs/assembly.md (1 Mbps, Protocol 2.0).
Default scan list is lower-body IDs only (11-16, 21-26).
"""

from __future__ import annotations

import argparse
import math
import sys
import time

# Standalone: no PYTHONPATH / numpy required.
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

# Model-frame sign convention (same as src/constants.py).
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

NEUTRAL_POSE_DEG: dict[str, float] = {
    "left_hip_yaw": 0.0,
    "left_hip_roll": 5.0,
    "left_hip_pitch": -10.0,
    "left_knee": 0.0,
    "left_ankle_pitch": 0.0,
    "left_ankle_roll": -5.0,
    "right_hip_yaw": 0.0,
    "right_hip_roll": -5.0,
    "right_hip_pitch": -10.0,
    "right_knee": 0.0,
    "right_ankle_pitch": 0.0,
    "right_ankle_roll": 5.0,
}

LEG_IDS: list[int] = list(MOTOR_TO_ID.values())

# XL330: 4096 ticks per revolution; Wizard display uses the same tick units.
TICKS_PER_REV: int = 4096
WIZARD_ZERO_TICK: int = 2048  # rustypot 0 rad ↔ Wizard ~180°
DEG_PER_TICK: float = 360.0 / TICKS_PER_REV


def _import_rustypot():
    try:
        from rustypot import Xl330PyController

        return Xl330PyController
    except ImportError as exc:
        print(f"Python: {sys.executable}", file=sys.stderr)
        print(f"Cannot import rustypot: {exc}", file=sys.stderr)
        print(
            "Install into THIS interpreter (pip and python must match):\n"
            f'  "{sys.executable}" -m pip install rustypot',
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


def _name(motor_id: int) -> str:
    return ID_TO_MOTOR.get(motor_id, f"id_{motor_id}")


def _sign(motor_id: int) -> float:
    name = ID_TO_MOTOR.get(motor_id)
    if name is None:
        return 1.0
    return MOTOR_SIGN[name]


def _to_hw(motor_id: int, model_rad: float) -> float:
    return model_rad * _sign(motor_id)


def _from_hw(motor_id: int, hw_rad: float) -> float:
    return hw_rad * _sign(motor_id)


def _read_scalar(raw) -> float:
    if isinstance(raw, (list, tuple)):
        return float(raw[0])
    return float(raw)


def _read_homing_offset(ctrl, motor_id: int) -> int:
    raw = ctrl.read_homing_offset(motor_id)
    return int(_read_scalar(raw))


def recommended_homing_offset(motor_id: int, q_model_rad: float, homing_current: int) -> int:
    """Homing offset (ticks) so the current pose reads q=0 in model frame."""
    q_hw_rad = q_model_rad / _sign(motor_id)
    delta_ticks = round(-q_hw_rad * TICKS_PER_REV / (2.0 * math.pi))
    return homing_current + delta_ticks


def _wizard_deg_from_hw_rad(hw_rad: float) -> float:
    """Approximate Dynamixel Wizard encoder angle for a rustypot hw angle."""
    tick = WIZARD_ZERO_TICK + hw_rad * TICKS_PER_REV / (2.0 * math.pi)
    return (tick % TICKS_PER_REV) * DEG_PER_TICK


def print_homing_offset_hints(rows: list[tuple[int, float, int, int, float]]) -> None:
    """Print suggested Homing Offset values (read-only; user sets them in Wizard)."""
    if not rows:
        return

    print("\nHoming offset hints (torque OFF in Wizard, then Save — this script does not write EEPROM):")
    print("  Assumes each joint is already held at the mechanical/CAD zero you want as q=0.")
    print(
        f"  {'id':>4}  {'name':20s}  {'q_now':>8}  {'H_now':>7}  {'H_set':>7}  {'dH':>6}  "
        f"{'enc_now':>7}  {'enc~':>7}"
    )
    for motor_id, q_model_rad, homing_current, homing_recommended, hw_rad in rows:
        delta = homing_recommended - homing_current
        q_deg = math.degrees(q_model_rad)
        enc_now = _wizard_deg_from_hw_rad(hw_rad)
        enc_after = (enc_now + delta * DEG_PER_TICK) % 360.0
        note = "ok" if abs(q_deg) < 0.5 else ""
        print(
            f"  {motor_id:4d}  {_name(motor_id):20s}  {q_deg:+7.2f}°  "
            f"{homing_current:7d}  {homing_recommended:7d}  {delta:+6d}  "
            f"{enc_now:6.1f}°  {enc_after:6.1f}°  {note}"
        )


def scan(ctrl, candidate_ids: list[int], *, show_offset_hints: bool = True) -> list[int]:
    found: list[int] = []
    hint_rows: list[tuple[int, float, int, int, float]] = []

    for motor_id in candidate_ids:
        try:
            raw = ctrl.read_present_position(motor_id)
            hw_rad = _read_scalar(raw)
            q_model_rad = _from_hw(motor_id, hw_rad)
            homing_current = _read_homing_offset(ctrl, motor_id)
            homing_recommended = recommended_homing_offset(motor_id, q_model_rad, homing_current)

            found.append(motor_id)
            hint_rows.append((motor_id, q_model_rad, homing_current, homing_recommended, hw_rad))
            print(f"  OK  id={motor_id:2d}  {_name(motor_id):20s}  q={math.degrees(q_model_rad):+7.2f} deg")
        except Exception as exc:  # noqa: BLE001 — missing IDs are expected during probe
            print(f"  --  id={motor_id:2d}  {_name(motor_id):20s}  ({exc})")

    if show_offset_hints:
        print_homing_offset_hints(hint_rows)

    return found


def print_offset_hints(ctrl, ids: list[int]) -> None:
    """Recompute homing offset hints for motors already marked online."""
    if not ids:
        print("No motors online.")
        return

    hint_rows: list[tuple[int, float, int, int, float]] = []
    for motor_id in ids:
        hw_rad = _read_scalar(ctrl.read_present_position(motor_id))
        q_model_rad = _from_hw(motor_id, hw_rad)
        homing_current = _read_homing_offset(ctrl, motor_id)
        homing_recommended = recommended_homing_offset(motor_id, q_model_rad, homing_current)
        hint_rows.append((motor_id, q_model_rad, homing_current, homing_recommended, hw_rad))

    print_homing_offset_hints(hint_rows)


def read_positions(ctrl, ids: list[int]) -> None:
    if not ids:
        print("No motors online.")
        return
    raw = ctrl.sync_read_present_position(ids)
    for motor_id, hw in zip(ids, raw):
        model = _from_hw(motor_id, float(hw))
        print(f"  id={motor_id:2d}  {_name(motor_id):20s}  {math.degrees(model):+7.2f} deg")


def enable_torque(ctrl, ids: list[int], on: bool) -> None:
    if not ids:
        return
    ctrl.sync_write_torque_enable(ids, [on] * len(ids))
    print(f"Torque {'ON' if on else 'OFF'} for {ids}")


def move_relative(ctrl, motor_id: int, delta_deg: float) -> None:
    raw = ctrl.read_present_position(motor_id)
    hw = float(raw[0] if isinstance(raw, (list, tuple)) else raw)
    model = _from_hw(motor_id, hw)
    target = model + math.radians(delta_deg)
    ctrl.sync_write_torque_enable([motor_id], [True])
    ctrl.sync_write_goal_position([motor_id], [_to_hw(motor_id, target)])
    print(
        f"  id={motor_id} {_name(motor_id)}: "
        f"{math.degrees(model):+.2f} -> {math.degrees(target):+.2f} deg"
    )


def move_absolute(ctrl, motor_id: int, target_deg: float) -> None:
    target = math.radians(target_deg)
    ctrl.sync_write_torque_enable([motor_id], [True])
    ctrl.sync_write_goal_position([motor_id], [_to_hw(motor_id, target)])
    print(f"  id={motor_id} {_name(motor_id)} -> {target_deg:+.2f} deg (model frame)")


def go_neutral(ctrl, ids: list[int]) -> None:
    targets_hw: list[float] = []
    for motor_id in ids:
        name = ID_TO_MOTOR.get(motor_id)
        if name is None or name not in NEUTRAL_POSE_DEG:
            raw = ctrl.read_present_position(motor_id)
            hw = float(raw[0] if isinstance(raw, (list, tuple)) else raw)
            targets_hw.append(hw)
        else:
            targets_hw.append(_to_hw(motor_id, math.radians(NEUTRAL_POSE_DEG[name])))
    ctrl.sync_write_torque_enable(ids, [True] * len(ids))
    ctrl.sync_write_goal_position(ids, targets_hw)
    print(f"Sent neutral pose to {len(ids)} motors.")


def wiggle(ctrl, motor_id: int, amp_deg: float = 10.0, seconds: float = 3.0) -> None:
    raw = ctrl.read_present_position(motor_id)
    hw0 = float(raw[0] if isinstance(raw, (list, tuple)) else raw)
    model0 = _from_hw(motor_id, hw0)
    ctrl.sync_write_torque_enable([motor_id], [True])
    print(f"Wiggle id={motor_id} amp={amp_deg} deg for {seconds:.1f}s (Ctrl+C to abort)...")
    t0 = time.perf_counter()
    try:
        while True:
            t = time.perf_counter() - t0
            if t >= seconds:
                break
            target = model0 + math.radians(amp_deg) * math.sin(2.0 * math.pi * 0.5 * t)
            ctrl.sync_write_goal_position([motor_id], [_to_hw(motor_id, target)])
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\nWiggle interrupted.")
    ctrl.sync_write_goal_position([motor_id], [_to_hw(motor_id, model0)])
    print("Returned to start angle.")


def print_help() -> None:
    print(
        """
Commands:
  scan                         probe candidate IDs + homing offset hints
  offset                       reprint homing offset hints (online motors)
  list                         print present positions (online set)
  on / off                     torque enable / disable (online set)
  move <id> <delta_deg>        relative move in model frame (degrees)
  goto <id> <deg>              absolute move in model frame (degrees)
  neutral                      send neutral standing pose to online motors
  wiggle <id> [amp_deg] [s]    small sine motion around current angle
  help                         show this help
  quit                         torque off and exit
""".strip()
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="XL330 COM-port motor smoke test")
    p.add_argument("--port", default="COM3", help="serial port (default: COM3)")
    p.add_argument("--baud", type=int, default=1_000_000, help="baud rate (default: 1000000)")
    p.add_argument(
        "--ids",
        default="legs",
        help="ID set to probe: legs | comma list e.g. 11,12,14 (default: legs)",
    )
    return p.parse_args()


def resolve_candidates(spec: str) -> list[int]:
    key = spec.strip().lower()
    if key == "legs":
        return list(LEG_IDS)
    return [int(x) for x in key.split(",") if x.strip()]


def main() -> int:
    print("Microban motor_test — XL330 over serial", flush=True)

    args = parse_args()
    Xl330PyController = _import_rustypot()
    candidates = resolve_candidates(args.ids)

    print(f"Opening {args.port} @ {args.baud} ...", flush=True)
    try:
        ctrl = Xl330PyController(serial_port=args.port, baudrate=args.baud, timeout=0.1)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to open {args.port}: {exc}", file=sys.stderr)
        print("Check: U2D2 plugged in, correct COM port in Device Manager, no other app using the port.", file=sys.stderr)
        return 1

    print("Scanning...", flush=True)
    online = scan(ctrl, candidates)
    if online:
        try:
            ctrl.sync_write_status_return_level(online, [1] * len(online))
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: status_return_level write failed: {exc}")

    print_help()
    try:
        while True:
            try:
                line = input("motor> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()

            try:
                if cmd in ("q", "quit", "exit"):
                    break
                if cmd == "help":
                    print_help()
                elif cmd == "scan":
                    online = scan(ctrl, candidates)
                elif cmd == "offset":
                    print_offset_hints(ctrl, online)
                elif cmd == "list":
                    read_positions(ctrl, online)
                elif cmd == "on":
                    enable_torque(ctrl, online, True)
                elif cmd == "off":
                    enable_torque(ctrl, online, False)
                elif cmd == "neutral":
                    go_neutral(ctrl, online)
                elif cmd == "move" and len(parts) >= 3:
                    move_relative(ctrl, int(parts[1]), float(parts[2]))
                    if int(parts[1]) not in online:
                        online = sorted(set(online) | {int(parts[1])})
                elif cmd == "goto" and len(parts) >= 3:
                    move_absolute(ctrl, int(parts[1]), float(parts[2]))
                    if int(parts[1]) not in online:
                        online = sorted(set(online) | {int(parts[1])})
                elif cmd == "wiggle" and len(parts) >= 2:
                    amp = float(parts[2]) if len(parts) >= 3 else 10.0
                    secs = float(parts[3]) if len(parts) >= 4 else 3.0
                    wiggle(ctrl, int(parts[1]), amp_deg=amp, seconds=secs)
                else:
                    print("Unknown command. Type 'help'.")
            except Exception as exc:  # noqa: BLE001
                print(f"Error: {exc}")
    finally:
        if online:
            try:
                enable_torque(ctrl, online, False)
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: torque-off failed: {exc}")
        print("Done.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Fatal error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
