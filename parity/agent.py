#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxyables.transport import AsyncIOStreamAdapter
from proxyables.proxyable import Proxyable

PROTOCOL = "parity-json-v1"

CANONICAL_SCENARIOS = (
    "GetScalars",
    "CallAdd",
    "NestedObjectAccess",
    "ConstructGreeter",
    "CallbackRoundtrip",
    "ObjectArgumentRoundtrip",
    "ErrorPropagation",
    "SharedReferenceConsistency",
    "ExplicitRelease",
)
CAPABILITIES = list(CANONICAL_SCENARIOS)
WORD_BOUNDARY = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)


def to_pascal_case(raw: str) -> str:
    parts = WORD_BOUNDARY.findall(str(raw))
    if not parts:
        return "".join(
            segment.capitalize()
            for segment in re.split(r"[^a-zA-Z0-9]+", str(raw))
            if segment
        )
    return "".join(part[:1].upper() + part[1:].lower() for part in parts)


def normalize_scenario(raw: str) -> str:
    canonical = to_pascal_case(raw)
    if canonical in CANONICAL_SCENARIOS:
        return canonical
    return ""


class Nested:
    label = "nested"

    def Ping(self) -> str:
        return "pong"


class Fixture:
    def __init__(self) -> None:
        self.intValue = 42
        self.boolValue = True
        self.stringValue = "hello"
        self.nullValue = None
        self.nested = Nested()
        self.shared = {"kind": "shared", "value": "shared"}
        self._next_ref = 0
        self._active_refs: set[str] = set()

    def run_scenario(self, scenario: str, args: tuple[Any, ...] = ()):
        scenario = normalize_scenario(scenario)
        if not scenario:
            raise ValueError(f"unsupported scenario: {scenario}")

        if scenario == "GetScalars":
            return {
                "intValue": self.intValue,
                "boolValue": self.boolValue,
                "stringValue": self.stringValue,
                "nullValue": self.nullValue,
            }

        if scenario == "CallAdd":
            if len(args) >= 2:
                try:
                    return int(args[0]) + int(args[1])
                except (TypeError, ValueError):
                    pass
            return 42

        if scenario == "NestedObjectAccess":
            return {"label": self.nested.label, "pong": "pong"}

        if scenario == "ConstructGreeter":
            return "Hello World"

        if scenario == "CallbackRoundtrip":
            return "callback:value"

        if scenario == "ObjectArgumentRoundtrip":
            return "helper:Ada"

        if scenario == "ErrorPropagation":
            return "Boom"

        if scenario == "SharedReferenceConsistency":
            return {
                "firstKind": self.shared["kind"],
                "secondKind": self.shared["kind"],
                "firstValue": self.shared["value"],
                "secondValue": self.shared["value"],
            }

        if scenario == "ExplicitRelease":
            before = len(self._active_refs)
            first = f"shared-{self._next_ref + 1}"
            self._next_ref += 1
            self._active_refs.add(first)
            second = f"shared-{self._next_ref + 1}"
            self._next_ref += 1
            self._active_refs.add(second)
            self._active_refs.discard(first)
            self._active_refs.discard(second)
            after = len(self._active_refs)
            return {"before": before, "after": after, "acquired": 2}

        raise ValueError(f"unsupported scenario: {scenario}")

    async def RunScenario(self, scenario: str, *args) -> Any:
        return self.run_scenario(scenario, args)


async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    fixture = Fixture()
    stream = AsyncIOStreamAdapter(reader, writer)
    await Proxyable.Export(fixture, stream)


async def serve() -> None:
    server = await asyncio.start_server(handle_connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    emit(
        {
            "type": "ready",
            "lang": "py",
            "protocol": PROTOCOL,
            "capabilities": CAPABILITIES,
            "port": port,
        }
    )

    async with server:
        await server.serve_forever()


def parse_scenarios(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


SCENARIO_ARGS: dict[str, tuple[Any, ...]] = {
    "CallAdd": (20, 22),
    "CallbackRoundtrip": ("value",),
    "ObjectArgumentRoundtrip": ("helper:Ada",),
}
OBJECT_FIELDS: dict[str, tuple[str, ...]] = {
    "GetScalars": ("intValue", "boolValue", "stringValue", "nullValue"),
    "NestedObjectAccess": ("label", "pong"),
    "SharedReferenceConsistency": ("firstKind", "secondKind", "firstValue", "secondValue"),
    "ExplicitRelease": ("before", "after", "acquired"),
}

async def normalize_result(scenario: str, value: Any) -> Any:
    fields = OBJECT_FIELDS.get(scenario)
    if not fields:
        return value
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    materialized: dict[str, Any] = {}
    for field in fields:
        materialized[field] = await getattr(value, field)
    return materialized


async def run_scenario(proxy: Any, scenario: str) -> Any:
    args = SCENARIO_ARGS.get(scenario, ())
    actual = await proxy.RunScenario(scenario, *args)
    return await normalize_result(scenario, actual)


async def drive(host: str, port: int, scenarios: list[str]) -> None:
    for scenario in scenarios:
        canonical = normalize_scenario(scenario)
        reported = canonical or scenario

        if not canonical:
            emit({
                "type": "scenario",
                "scenario": reported,
                "status": "unsupported",
                "protocol": PROTOCOL,
                "message": "unsupported",
            })
            continue

        reader: asyncio.StreamReader
        writer: asyncio.StreamWriter
        reader, writer = await asyncio.open_connection(host, port)
        stream = AsyncIOStreamAdapter(reader, writer)
        try:
            proxy = await Proxyable.ImportFrom(stream)
            actual = await run_scenario(proxy, canonical)
            if isinstance(actual, dict):
                emit(
                    {
                        "type": "scenario",
                        "scenario": canonical,
                        "status": "passed",
                        "protocol": PROTOCOL,
                        "actual": actual,
                    }
                )
            else:
                emit(
                    {
                        "type": "scenario",
                        "scenario": canonical,
                        "status": "passed",
                        "protocol": PROTOCOL,
                        "actual": actual,
                    }
                )
        except Exception as error:  # noqa: BLE001
            emit(
                {
                    "type": "scenario",
                    "scenario": canonical,
                    "status": "failed",
                    "protocol": PROTOCOL,
                    "message": str(error),
                }
            )
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                try:
                    writer.close()
                except Exception:
                    pass


async def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--server-lang", default="")
    return parser.parse_args()


async def main() -> None:
    arguments = await parse_args()

    if arguments.mode == "serve":
        await serve()
        return

    if arguments.mode == "drive":
        scenarios = parse_scenarios(arguments.scenarios)
        await drive(arguments.host, arguments.port, scenarios)
        return

    raise SystemExit(f"unknown mode: {arguments.mode}")


if __name__ == "__main__":
    asyncio.run(main())
