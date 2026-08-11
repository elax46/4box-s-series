#!/usr/bin/env python3
"""Simulate a 4box P40S device over MQTT for integration development.

This script behaves like the real firmware closely enough to develop and
test the Home Assistant integration without owning the hardware yet:

- Publishes `<ID>/connect` = "true" (retained) on start, "false" on exit.
- Listens on `<ID>/cmnd` and reacts to the payloads documented in the
  MQTT guide: action=ON/OFF/TOGGLE, power=RELAY1, current=RELAY1,
  energyActive=RELAY1, reboot=TRUE.
- Publishes relay state changes on `<ID>/stat/relay/1` (retained).
- Publishes power/current on `<ID>/stat/relay/1/power/w` and
  `<ID>/stat/relay/1/current/a` every few seconds while the relay is on.
- Publishes voltage/temperature on `<ID>/stat/voltage` and
  `<ID>/stat/temperature` periodically.
- Replies to `energyActive=RELAY1` on `<ID>/info` with a slowly
  increasing kWh counter, exactly like the coordinator expects.

Requirements: `pip install paho-mqtt`

Usage:
    python simulate_p40s.py --host localhost --port 1883 \
        --device-id M048B-30AEA4A6D460
"""

from __future__ import annotations

import argparse
import random
import threading
import time

import paho.mqtt.client as mqtt


class FakeP40S:
    def __init__(self, device_id: str, host: str, port: int) -> None:
        self.device_id = device_id
        self.relay_on = False
        self.energy_kwh = round(random.uniform(5.0, 50.0), 3)

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sim-{device_id}",
        )
        self.client.will_set(f"{device_id}/connect", payload="false", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(host, port, keepalive=30)

    # -- MQTT plumbing -----------------------------------------------
    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None) -> None:
        print(f"[{self.device_id}] connected (rc={reason_code})")
        client.subscribe(f"{self.device_id}/cmnd")
        client.publish(f"{self.device_id}/connect", "true", retain=True)
        client.publish(f"{self.device_id}/stat/relay/1", "off", retain=True)

    def _on_message(self, client, userdata, msg) -> None:
        payload = msg.payload.decode(errors="replace")
        print(f"[{self.device_id}] cmnd: {payload}")

        for pair in payload.split("&"):
            if "=" not in pair:
                continue
            key, _, value = pair.partition("=")
            self._handle_param(key, value)

    def _handle_param(self, key: str, value: str) -> None:
        if key == "action":
            self._handle_action(value)
        elif key == "power" and value == "RELAY1":
            self._publish_info(f"{self._current_power():.1f} (Watt)")
        elif key == "current" and value == "RELAY1":
            self._publish_info(f"{self._current_power() / 230.0:.2f} (Ampere)")
        elif key == "energyActive" and value == "RELAY1":
            self.energy_kwh += round(random.uniform(0, 0.01), 3)
            self._publish_info(f"{self.energy_kwh:.3f} (kWh)")
        elif key == "gpiostatus" and value == "GET":
            self._publish_info("00000001" if self.relay_on else "00000000")
        elif key == "reboot" and value == "TRUE":
            self._publish_info("Rebooting....")
        elif key == "firmware" and value == "GET":
            self._publish_info("Morpheos-ESP32IoT-sim-1.0.0")
        else:
            self._publish_info("DONE")

    def _handle_action(self, value: str) -> None:
        if value == "ON":
            self.relay_on = True
        elif value == "OFF":
            self.relay_on = False
        elif value == "TOGGLE":
            self.relay_on = not self.relay_on
        else:
            self._publish_info("DONE")
            return

        self.client.publish(
            f"{self.device_id}/stat/relay/1",
            "on" if self.relay_on else "off",
            retain=True,
        )
        self._publish_info("DONE")

    def _publish_info(self, payload: str) -> None:
        self.client.publish(f"{self.device_id}/info", payload)

    def _current_power(self) -> float:
        return round(random.uniform(80.0, 120.0), 1) if self.relay_on else 0.0

    # -- background telemetry -----------------------------------------
    def start_background_telemetry(self) -> None:
        def loop() -> None:
            tick = 0
            while True:
                if self.relay_on:
                    power = self._current_power()
                    self.client.publish(
                        f"{self.device_id}/stat/relay/1/power/w", f"{power:.1f}"
                    )
                    self.client.publish(
                        f"{self.device_id}/stat/relay/1/current/a",
                        f"{power / 230.0:.2f}",
                    )
                if tick % 15 == 0:
                    self.client.publish(
                        f"{self.device_id}/stat/voltage",
                        f"{round(random.uniform(228, 232), 1)}",
                    )
                    self.client.publish(
                        f"{self.device_id}/stat/temperature",
                        f"{round(random.uniform(25, 32), 1)}",
                    )
                tick += 1
                time.sleep(5)

        threading.Thread(target=loop, daemon=True).start()

    def run(self) -> None:
        self.start_background_telemetry()
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            self.client.publish(f"{self.device_id}/connect", "false", retain=True)
            self.client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--device-id", default="M048B-30AEA4A6D460")
    args = parser.parse_args()

    device = FakeP40S(args.device_id, args.host, args.port)
    print(f"Simulating {args.device_id} on {args.host}:{args.port}. Ctrl+C to stop.")
    device.run()


if __name__ == "__main__":
    main()
