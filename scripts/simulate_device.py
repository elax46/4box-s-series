#!/usr/bin/env python3
"""Simulate a 4box S Series device over MQTT for integration development.

Supports all four device families the integration understands, closely
enough to develop and test each platform without owning the hardware yet:

- relay      (P40S, M048B/C/D, M054F, M053B dual-light)
- motor      (M053B configured as a motorized shutter/awning)
- push       (Uniko Push pulsed output)
- thermostat (Morpheos thermostat)

Requirements: `pip install paho-mqtt` (see scripts/requirements-dev.txt)

Usage:
    python simulate_device.py --device-type relay \
        --device-id M048B-30AEA4A6D460
    python simulate_device.py --device-type motor \
        --device-id M053B-30AEA4A6D461
    python simulate_device.py --device-type push \
        --device-id M048B-30AEA4A6D462
    python simulate_device.py --device-type thermostat \
        --device-id M048B-30AEA4A6D463
"""

from __future__ import annotations

import argparse
import random
import re
import threading
import time

import paho.mqtt.client as mqtt

_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")


class _BaseSimulator:
    """Shared MQTT plumbing: connect, LWT, /cmnd subscription."""

    def __init__(self, device_id: str, host: str, port: int) -> None:
        self.device_id = device_id
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sim-{device_id}",
        )
        self.client.will_set(f"{device_id}/connect", payload="false", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(host, port, keepalive=30)

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None) -> None:
        print(f"[{self.device_id}] connected (rc={reason_code})")
        client.subscribe(f"{self.device_id}/cmnd")
        client.publish(f"{self.device_id}/connect", "true", retain=True)
        self.on_ready()

    def _on_message(self, client, userdata, msg) -> None:
        payload = msg.payload.decode(errors="replace")
        print(f"[{self.device_id}] cmnd: {payload}")
        for pair in payload.split("&"):
            key, _, value = pair.partition("=")
            if key:
                self.handle_param(key, value)

    def on_ready(self) -> None:
        """Override: publish initial retained state."""

    def handle_param(self, key: str, value: str) -> None:
        """Override: react to one key=value pair from a /cmnd payload."""

    def publish_info(self, payload: str) -> None:
        self.client.publish(f"{self.device_id}/info", payload)

    def start_background_loop(self) -> None:
        """Override: periodic telemetry, if any."""

    def run(self) -> None:
        self.start_background_loop()
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            self.client.publish(f"{self.device_id}/connect", "false", retain=True)
            self.client.disconnect()


class RelaySimulator(_BaseSimulator):
    """Simulates P40S / M048B/C/D / M054F / M053B dual-light."""

    def __init__(self, device_id: str, host: str, port: int) -> None:
        super().__init__(device_id, host, port)
        self.relay_on = False
        self.energy_kwh = round(random.uniform(5.0, 50.0), 3)

    def on_ready(self) -> None:
        # Deliberately NOT retained: mirrors the real P40S firmware
        # (confirmed on FW MO.14.00). See __init__.py's gpiostatus=GET fetch
        # for how the integration works around this.
        self.client.publish(f"{self.device_id}/stat/relay/1", "off")

    def handle_param(self, key: str, value: str) -> None:
        if key == "action":
            self._handle_action(value)
        elif key == "power" and value == "RELAY1":
            self.publish_info(f"{self._current_power():.1f} (Watt)")
        elif key == "current" and value == "RELAY1":
            self.publish_info(f"{self._current_power() / 230.0:.2f} (Ampere)")
        elif key == "energyActive" and value == "RELAY1":
            self.energy_kwh += round(random.uniform(0, 0.01), 3)
            self.publish_info(f"{self.energy_kwh:.3f} (kWh)")
        elif key == "gpiostatus" and value == "GET":
            # Format confirmed on a real M048D, firmware MO.14.00.
            relay_state = "ON" if self.relay_on else "OFF"
            self.publish_info(
                f"LED1_R:0;LED1_G:0;LED1_B:0;RELAY1:{relay_state};"
                f"SW1_DC:PULL;SW1_AC:PULL;"
            )
        elif key == "reboot" and value == "TRUE":
            self.publish_info("Rebooting....")
        else:
            self.publish_info("DONE")

    def _handle_action(self, value: str) -> None:
        if value == "ON":
            self.relay_on = True
        elif value == "OFF":
            self.relay_on = False
        elif value == "TOGGLE":
            self.relay_on = not self.relay_on
        else:
            self.publish_info("DONE")
            return
        self.client.publish(
            f"{self.device_id}/stat/relay/1", "on" if self.relay_on else "off"
        )
        # Real firmware (M048D, FW MO.14.00) replies with "<ON>"/"<OFF>"
        # reflecting the resulting state here, not "DONE" as the vendor
        # PDF guide documents -- even after TOGGLE. Matching that, since
        # this integration doesn't currently parse this particular
        # response anyway (it relies on the /stat push or the
        # gpiostatus=GET fetch instead), but it's worth being accurate
        # for anyone testing against the simulator.
        self.publish_info(f"<{'ON' if self.relay_on else 'OFF'}>")

    def _current_power(self) -> float:
        return round(random.uniform(80.0, 120.0), 1) if self.relay_on else 0.0

    def start_background_loop(self) -> None:
        def loop() -> None:
            tick = 0
            while True:
                if self.relay_on:
                    power = self._current_power()
                    self.client.publish(f"{self.device_id}/stat/relay/1/power/w", f"{power:.1f}")
                    self.client.publish(
                        f"{self.device_id}/stat/relay/1/current/a", f"{power / 230.0:.2f}"
                    )
                if tick % 15 == 0:
                    self.client.publish(
                        f"{self.device_id}/stat/voltage", f"{round(random.uniform(228, 232), 1)}"
                    )
                    self.client.publish(
                        f"{self.device_id}/stat/temperature",
                        f"{round(random.uniform(25, 32), 1)}",
                    )
                tick += 1
                time.sleep(5)

        threading.Thread(target=loop, daemon=True).start()


class MotorSimulator(_BaseSimulator):
    """Simulates an M053B configured as a motorized shutter."""

    _STEP_PER_TICK = 10  # position units per 0.5s tick -> full travel in ~5s

    def __init__(self, device_id: str, host: str, port: int) -> None:
        super().__init__(device_id, host, port)
        self.position = 50  # 0=closed, 100=open
        self.target: int | None = None
        self.status = "STOPPED"
        self._lock = threading.Lock()

    def on_ready(self) -> None:
        self._publish_stat()

    def handle_param(self, key: str, value: str) -> None:
        if key != "motor":
            self.publish_info("DONE")
            return

        if value == "UP":
            self._set_target(100, "OPENING")
        elif value == "DOWN":
            self._set_target(0, "CLOSING")
        elif value == "STOP":
            with self._lock:
                self.target = None
                self.status = "STOPPED"
            self._publish_stat()
        elif value == "POSITION":
            self.publish_info(str(self.position))
            return
        elif value == "STATUS":
            self._publish_stat()
        elif value == "CALIBRATION":
            self.publish_info("DONE")
            return
        self.publish_info("DONE")

    def handle_composite(self, payload: str) -> None:
        """MOVE&perc=/MOVE&time=/TILT&perc= span two key=value pairs."""
        params = dict(p.split("=", 1) for p in payload.split("&") if "=" in p)
        if params.get("motor") == "MOVE" and "perc" in params:
            perc = max(0, min(100, int(params["perc"])))
            direction = "OPENING" if perc > self.position else "CLOSING"
            self._set_target(perc, direction)
        elif params.get("motor") == "TILT" and "perc" in params:
            # Tilt is simulated as an instant change (no travel time modeled).
            perc = max(0, min(100, int(params["perc"])))
            self.publish_info("DONE")
            return
        self.publish_info("DONE")

    def _on_message(self, client, userdata, msg) -> None:
        payload = msg.payload.decode(errors="replace")
        print(f"[{self.device_id}] cmnd: {payload}")
        if "&" in payload and payload.split("=", 1)[0] == "motor":
            self.handle_composite(payload)
            return
        for pair in payload.split("&"):
            key, _, value = pair.partition("=")
            if key:
                self.handle_param(key, value)

    def _set_target(self, target: int, status: str) -> None:
        with self._lock:
            self.target = target
            self.status = status
        self._publish_stat()

    def _publish_stat(self) -> None:
        payload = f"00000011=>{self.position}=>0.0=>0.0=>{self.status}"
        self.client.publish(f"{self.device_id}/stat", payload)

    def start_background_loop(self) -> None:
        def loop() -> None:
            while True:
                with self._lock:
                    if self.target is not None:
                        if self.position < self.target:
                            self.position = min(self.target, self.position + self._STEP_PER_TICK)
                        elif self.position > self.target:
                            self.position = max(self.target, self.position - self._STEP_PER_TICK)
                        if self.position == self.target:
                            self.target = None
                            self.status = "STOPPED"
                        moved = True
                    else:
                        moved = False
                if moved:
                    self._publish_stat()
                time.sleep(0.5)

        threading.Thread(target=loop, daemon=True).start()


class PushSimulator(_BaseSimulator):
    """Simulates a Uniko Push pulsed output."""

    _PULSE_RE = re.compile(r"PULSETIME<ron>:(\d+)&&RELAY1:ON")

    def on_ready(self) -> None:
        # Also not retained, matching the relay family (see RelaySimulator).
        self.client.publish(f"{self.device_id}/stat/relay/1", "off")

    def _on_message(self, client, userdata, msg) -> None:
        payload = msg.payload.decode(errors="replace")
        print(f"[{self.device_id}] cmnd: {payload}")

        if payload == "pulsetime=GET":
            self.publish_info("no active pulse")
            return

        match = self._PULSE_RE.search(payload)
        if match:
            duration_ms = int(match.group(1))
            self._trigger_pulse(duration_ms)
            self.publish_info("DONE")
            return

        self.publish_info("DONE")

    def _trigger_pulse(self, duration_ms: int) -> None:
        self.client.publish(f"{self.device_id}/stat/relay/1", "on")

        def turn_off() -> None:
            time.sleep(duration_ms / 1000.0)
            self.client.publish(f"{self.device_id}/stat/relay/1", "off")

        threading.Thread(target=turn_off, daemon=True).start()


class ThermostatSimulator(_BaseSimulator):
    """Simulates a Morpheos thermostat (polled, no push telemetry)."""

    def __init__(self, device_id: str, host: str, port: int) -> None:
        super().__init__(device_id, host, port)
        self.mode = 1  # 0=OFF, 1=HEATING, 2=COOLING
        self.setpoint = 20.5
        self.temperature = 19.8
        self.humidity = 45.0

    def handle_param(self, key: str, value: str) -> None:
        if key == "thermostatMode":
            if value == "GET":
                self.publish_info(str(self.mode))
            else:
                self.mode = int(value)
                self.publish_info("DONE")
        elif key == "tS":
            if value == "GET":
                self.publish_info(f"{self.setpoint} (gradi Celsius)")
            else:
                try:
                    self.setpoint = float(value)
                except ValueError:
                    pass
                self.publish_info("DONE")
        elif key == "sht4x":
            if value == "t_compensated":
                # Ambient temperature drifts slowly toward the setpoint.
                self.temperature += (self.setpoint - self.temperature) * 0.05
                self.temperature += random.uniform(-0.1, 0.1)
                self.publish_info(f"{self.temperature:.1f} (gradi Celsius)")
            elif value == "rh_compensated":
                self.humidity += random.uniform(-0.5, 0.5)
                self.publish_info(f"{self.humidity:.1f} (% RH)")
            else:
                self.publish_info("DONE")
        else:
            self.publish_info("DONE")


_SIMULATORS = {
    "relay": RelaySimulator,
    "motor": MotorSimulator,
    "push": PushSimulator,
    "thermostat": ThermostatSimulator,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument(
        "--device-type", choices=list(_SIMULATORS), default="relay"
    )
    parser.add_argument("--device-id", default="M048B-30AEA4A6D460")
    args = parser.parse_args()

    simulator_cls = _SIMULATORS[args.device_type]
    device = simulator_cls(args.device_id, args.host, args.port)
    print(
        f"Simulating {args.device_type} device {args.device_id} on "
        f"{args.host}:{args.port}. Ctrl+C to stop."
    )
    device.run()


if __name__ == "__main__":
    main()
