#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import re

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
CAPABILITY_SET = set(CAPABILITIES)
WORD_BOUNDARY = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


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
    if canonical in CAPABILITY_SET:
        return canonical
    return ""


def emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


class Greeter:
    def __init__(self, prefix: str):
        self.prefix = prefix

    def greet(self, name: str) -> str:
        return f"{self.prefix} {name}"


class Nested:
    label = "nested"

    def ping(self) -> str:
        return "pong"


class Shared:
    kind = "shared"
    value = "shared"


class Fixture:
    def __init__(self) -> None:
        self.intValue = 42
        self.boolValue = True
        self.stringValue = "hello"
        self.nullValue = None
        self.nested = Nested()
        self.shared = Shared()
        self.Greeter = Greeter
        self._next_ref = 0
        self._active_refs: set[str] = set()

    def add(self, a: int, b: int) -> int:
        return a + b

    def echo(self, value):
        return value

    def runCallback(self, cb, value: str):
        result = cb(value)
        return result

    def useHelper(self, helper, name: str):
        return helper.greet(name)

    def explode(self):
        raise ValueError("Boom")

    def getShared(self):
        return self.shared

    def acquireShared(self):
        self._next_ref += 1
        ref_id = f"shared-{self._next_ref}"
        self._active_refs.add(ref_id)
        return {"kind": "shared", "value": "shared", "__refId": ref_id}

    def releaseShared(self, ref):
        if isinstance(ref, str):
            self._active_refs.discard(ref)
            return
        if isinstance(ref, dict):
            self._active_refs.discard(ref.get("__refId", ""))

    def debugStats(self):
        return {"active": len(self._active_refs), "total": self._next_ref}


async def run_scenario(fixture: Fixture, scenario: str):
    scenario = normalize_scenario(scenario)
    if not scenario:
        raise ValueError(f"unknown scenario: {scenario}")

    if scenario == "GetScalars":
        return {
            "intValue": fixture.intValue,
            "boolValue": fixture.boolValue,
            "stringValue": fixture.stringValue,
            "nullValue": fixture.nullValue,
        }

    if scenario == "CallAdd":
        return fixture.add(20, 22)

    if scenario == "NestedObjectAccess":
        return {
            "label": fixture.nested.label,
            "pong": fixture.nested.ping(),
        }

    if scenario == "ConstructGreeter":
        return Greeter("Hello").greet("World")

    if scenario == "CallbackRoundtrip":
        return fixture.runCallback(lambda value: f"callback:{value}", "value")

    if scenario == "ObjectArgumentRoundtrip":

        class Helper:
            def greet(self, name):
                return f"helper:{name}"

        return fixture.useHelper(Helper(), "Ada")

    if scenario == "ErrorPropagation":
        try:
            fixture.explode()
        except Exception as error:
            return str(error)
        raise RuntimeError("expected failure")

    if scenario == "SharedReferenceConsistency":
        first = fixture.getShared()
        second = fixture.getShared()
        return {
            "firstKind": first.kind,
            "secondKind": second.kind,
            "firstValue": first.value,
            "secondValue": second.value,
        }

    if scenario == "ExplicitRelease":
        before = fixture.debugStats()
        first = fixture.acquireShared()
        second = fixture.acquireShared()
        fixture.releaseShared(first)
        fixture.releaseShared(second)
        after = fixture.debugStats()
        return {
            "before": before["active"],
            "after": after["active"],
            "acquired": 2,
        }

    raise ValueError(f"unknown scenario: {scenario}")


def parse_scenarios(raw: str) -> list[str]:
    return [
        normalize_scenario(item.strip()) or item.strip()
        for item in raw.split(",")
        if item.strip()
    ]


async def serve() -> None:
    async def handle_conn(reader, writer):
        request = await reader.read()
        scenarios = parse_scenarios(request.decode("utf-8", errors="replace"))
        fixture = shared_fixture

        for scenario in scenarios:
            canonical = normalize_scenario(scenario)
            if not canonical:
                payload = {
                    "type": "scenario",
                    "scenario": scenario,
                    "status": "unsupported",
                    "protocol": PROTOCOL,
                    "message": "unsupported",
                }
                writer.write((json.dumps(payload) + "\n").encode("utf-8"))
                continue

            try:
                actual = await run_scenario(fixture, canonical)
            except Exception as error:
                payload = {
                    "type": "scenario",
                    "scenario": canonical,
                    "status": "failed",
                    "protocol": PROTOCOL,
                    "message": str(error),
                }
            else:
                payload = {
                    "type": "scenario",
                    "scenario": canonical,
                    "status": "passed",
                    "protocol": PROTOCOL,
                    "actual": actual,
                }
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))

        await writer.drain()
        writer.close()
        await writer.wait_closed()

    shared_fixture = Fixture()
    server = await asyncio.start_server(handle_conn, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    emit({"type": "ready", "lang": "py", "protocol": PROTOCOL, "capabilities": CAPABILITIES, "port": port})

    async with server:
        await server.serve_forever()


async def drive(host: str, port: int, scenarios: list[str]) -> None:
    request = ",".join(scenarios) + "\n"
    reader, writer = await asyncio.open_connection(host, port)

    writer.write(request.encode("utf-8"))
    await writer.drain()
    try:
        writer.write_eof()
    except (AttributeError, OSError):
        writer.close()

    seen: set[str] = set()
    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            payload = json.loads(line.decode("utf-8"))
            emit(payload)
            if isinstance(payload, dict) and payload.get("type") == "scenario":
                scenario = payload.get("scenario")
                if isinstance(scenario, str):
                    seen.add(scenario)
        except Exception:
            continue

    for scenario in scenarios:
        if scenario not in seen:
            emit({
                "type": "scenario",
                "scenario": scenario,
                "status": "failed",
                "protocol": PROTOCOL,
                "message": "server did not emit a result",
            })

    writer.close()
    await writer.wait_closed()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["serve", "drive"])
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--server-lang", default="")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if args.mode == "serve":
        await serve()
    else:
        await drive(args.host, args.port, parse_scenarios(args.scenarios))


if __name__ == "__main__":
    asyncio.run(main())
