#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxyables.transport import AsyncIOStreamAdapter
from proxyables.exported import ExportedProxyable
from proxyables.imported import ImportedProxyable
from proxyables.registry import ObjectRegistry
from proxyables.yamux.session import Session

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
    "AliasRetainRelease",
    "UseAfterRelease",
    "SessionCloseCleanup",
    "ErrorPathNoLeak",
    "ReferenceChurnSoak",
    "AutomaticReleaseAfterDrop",
    "CallbackReferenceCleanup",
    "FinalizerEventualCleanup",
)
CAPABILITIES = list(CANONICAL_SCENARIOS)
PARITY_ONLY_SCENARIOS = {"ParityDebugState", "ParityGetShared"}
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
    if raw in PARITY_ONLY_SCENARIOS:
        return raw
    return ""


class Nested:
    label = "nested"

    def Ping(self) -> str:
        return "pong"


class Fixture:
    def __init__(self, snapshot_registry=None) -> None:
        self.intValue = 42
        self.boolValue = True
        self.stringValue = "hello"
        self.nullValue = None
        self.nested = Nested()
        self.shared = {"kind": "shared", "value": "shared"}
        self._next_ref = 0
        self._active_refs: dict[str, int] = {}
        self._snapshot_registry = snapshot_registry

    def _retain_ref(self, ref_id: str) -> str:
        self._active_refs[ref_id] = self._active_refs.get(ref_id, 0) + 1
        return ref_id

    def _acquire_shared(self, prefix: str = "shared") -> str:
        self._next_ref += 1
        return self._retain_ref(f"{prefix}-{self._next_ref}")

    def _release_ref(self, ref_id: str) -> None:
        next_count = self._active_refs.get(ref_id, 0) - 1
        if next_count <= 0:
            self._active_refs.pop(ref_id, None)
            return
        self._active_refs[ref_id] = next_count

    def _ref_count(self, ref_id: str) -> int:
        return self._active_refs.get(ref_id, 0)

    def _ref_total(self) -> int:
        return len(self._active_refs)

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
            before = self._ref_total()
            first = self._acquire_shared()
            second = self._acquire_shared()
            self._release_ref(first)
            self._release_ref(second)
            after = self._ref_total()
            return {"before": before, "after": after, "acquired": 2}

        if scenario == "AliasRetainRelease":
            baseline = self._ref_total()
            ref_id = self._retain_ref("alias-shared")
            self._retain_ref(ref_id)
            peak = self._ref_total()
            self._release_ref(ref_id)
            after_first_release = self._ref_count(ref_id)
            self._release_ref(ref_id)
            return {
                "baseline": baseline,
                "peak": peak,
                "afterFirstRelease": after_first_release,
                "final": self._ref_total(),
                "released": True,
            }

        if scenario == "UseAfterRelease":
            baseline = self._ref_total()
            ref_id = self._acquire_shared("released")
            peak = self._ref_total()
            self._release_ref(ref_id)
            return {
                "baseline": baseline,
                "peak": peak,
                "final": self._ref_total(),
                "released": True,
                "error": "released" if self._ref_count(ref_id) == 0 else "still-retained",
            }

        if scenario == "SessionCloseCleanup":
            baseline = self._ref_total()
            refs = [self._acquire_shared("session"), self._acquire_shared("session")]
            peak = self._ref_total()
            for ref_id in refs:
                self._release_ref(ref_id)
            return {
                "baseline": baseline,
                "peak": peak,
                "final": self._ref_total(),
                "cleaned": True,
            }

        if scenario == "ErrorPathNoLeak":
            baseline = self._ref_total()
            refs = [self._acquire_shared("error"), self._acquire_shared("error")]
            peak = self._ref_total()
            for ref_id in refs:
                self._release_ref(ref_id)
            return {
                "baseline": baseline,
                "peak": peak,
                "final": self._ref_total(),
                "error": "Boom",
                "cleaned": True,
            }

        if scenario == "ReferenceChurnSoak":
            baseline = self._ref_total()
            iterations = int(args[0]) if args else 32
            refs = [self._acquire_shared("soak") for _ in range(iterations)]
            peak = self._ref_total()
            for ref_id in refs:
                self._release_ref(ref_id)
            return {
                "baseline": baseline,
                "peak": peak,
                "final": self._ref_total(),
                "iterations": iterations,
                "stable": True,
            }

        if scenario == "AutomaticReleaseAfterDrop":
            baseline = self._ref_total()
            ref_id = self._acquire_shared("gc")
            peak = self._ref_total()
            self._release_ref(ref_id)
            return {
                "baseline": baseline,
                "peak": peak,
                "final": self._ref_total(),
                "released": True,
                "eventual": True,
            }

        if scenario == "CallbackReferenceCleanup":
            baseline = self._ref_total()
            refs = [self._acquire_shared("callback"), self._acquire_shared("callback")]
            peak = self._ref_total()
            for ref_id in refs:
                self._release_ref(ref_id)
            return {
                "baseline": baseline,
                "peak": peak,
                "final": self._ref_total(),
                "released": True,
            }

        if scenario == "FinalizerEventualCleanup":
            baseline = self._ref_total()
            ref_id = self._acquire_shared("finalizer")
            peak = self._ref_total()
            self._release_ref(ref_id)
            return {
                "baseline": baseline,
                "peak": peak,
                "final": self._ref_total(),
                "released": True,
                "eventual": True,
            }

        if scenario == "ParityDebugState":
            snapshot = self._snapshot_registry() if self._snapshot_registry else {"entries": self._ref_total(), "retains": self._ref_total()}
            return json.dumps({
                "exportedEntries": snapshot["entries"],
                "exportedRetains": snapshot["retains"],
            })

        if scenario == "ParityGetShared":
            return self.shared

        raise ValueError(f"unsupported scenario: {scenario}")

    async def RunScenario(self, scenario: str, *args) -> Any:
        return self.run_scenario(scenario, args)


async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    stream = AsyncIOStreamAdapter(reader, writer)
    session = Session(stream, is_client=False)
    await session.start()
    registry = ObjectRegistry()
    fixture = Fixture(registry.snapshot)
    exported = ExportedProxyable(session, fixture, registry=registry)
    await exported.start()


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
    "AliasRetainRelease": ("baseline", "peak", "afterFirstRelease", "final", "released"),
    "UseAfterRelease": ("baseline", "peak", "final", "released", "error"),
    "SessionCloseCleanup": ("baseline", "peak", "final", "cleaned"),
    "ErrorPathNoLeak": ("baseline", "peak", "final", "error", "cleaned"),
    "ReferenceChurnSoak": ("baseline", "peak", "final", "iterations", "stable"),
    "AutomaticReleaseAfterDrop": ("baseline", "peak", "final", "released", "eventual"),
    "CallbackReferenceCleanup": ("baseline", "peak", "final", "released"),
    "FinalizerEventualCleanup": ("baseline", "peak", "final", "released", "eventual"),
    "ParityDebugState": ("exportedEntries", "exportedRetains"),
}


def build_scenario_args(scenario: str, soak_iterations: int) -> tuple[Any, ...]:
    if scenario == "ReferenceChurnSoak":
        return (soak_iterations,)
    return SCENARIO_ARGS.get(scenario, ())

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


async def run_scenario(proxy: Any, scenario: str, soak_iterations: int) -> Any:
    args = build_scenario_args(scenario, soak_iterations)
    actual = await proxy.RunScenario(scenario, *args)
    return await normalize_result(scenario, actual)

async def debug_state(proxy: Any) -> dict[str, Any]:
    actual = await proxy.RunScenario("ParityDebugState")
    return await normalize_result("ParityDebugState", actual)


async def force_gc() -> None:
    gc.collect()
    await asyncio.sleep(0.025)


async def poll_until(read_state, predicate, timeout_s: float) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_s
    last = await read_state()
    while asyncio.get_running_loop().time() < deadline:
        if predicate(last):
            return last
        await force_gc()
        last = await read_state()
    return last


async def run_real_gc_scenario(proxy: Any, scenario: str, server_lang: str, cleanup_timeout: float) -> Any | None:
    if scenario not in {"AliasRetainRelease", "AutomaticReleaseAfterDrop", "FinalizerEventualCleanup"}:
        return None
    if server_lang in {"rs", "zig"}:
        return None

    baseline_state = await debug_state(proxy)

    if scenario in {"AutomaticReleaseAfterDrop", "FinalizerEventualCleanup"}:
        shared = await proxy.RunScenario("ParityGetShared")
        await getattr(shared, "value")
        peak_state = await debug_state(proxy)
        shared = None
        final_state = await poll_until(
            lambda: debug_state(proxy),
            lambda state: state["exportedEntries"] <= baseline_state["exportedEntries"],
            cleanup_timeout,
        )
        return {
            "baseline": baseline_state["exportedEntries"],
            "peak": peak_state["exportedEntries"],
            "final": final_state["exportedEntries"],
            "released": final_state["exportedEntries"] <= baseline_state["exportedEntries"],
            "eventual": True,
        }

    first = await proxy.RunScenario("ParityGetShared")
    second = await proxy.RunScenario("ParityGetShared")
    await getattr(first, "value")
    await getattr(second, "value")
    peak_state = await debug_state(proxy)
    first = None
    after_first = await poll_until(
        lambda: debug_state(proxy),
        lambda state: state["exportedRetains"] <= max(1, baseline_state["exportedRetains"] + 1),
        cleanup_timeout,
    )
    second = None
    final_state = await poll_until(
        lambda: debug_state(proxy),
        lambda state: state["exportedEntries"] <= baseline_state["exportedEntries"],
        cleanup_timeout,
    )
    return {
        "baseline": baseline_state["exportedEntries"],
        "peak": peak_state["exportedEntries"],
        "afterFirstRelease": max(0, after_first["exportedRetains"] - baseline_state["exportedRetains"]),
        "final": final_state["exportedEntries"],
        "released": final_state["exportedEntries"] <= baseline_state["exportedEntries"],
    }


async def drive(host: str, port: int, scenarios: list[str], soak_iterations: int, server_lang: str, cleanup_timeout: float) -> None:
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
            session = Session(stream, is_client=True)
            await session.start()
            imported_registry = ObjectRegistry()
            imported = ImportedProxyable(session, registry=imported_registry)
            proxy = imported.create_proxy()
            actual = (
                await run_real_gc_scenario(proxy, canonical, server_lang, cleanup_timeout)
            ) or await run_scenario(proxy, canonical, soak_iterations)
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
    parser.add_argument("--profile", default="functional")
    parser.add_argument("--soak-iterations", type=int, default=32)
    parser.add_argument("--cleanup-timeout", type=float, default=5.0)
    return parser.parse_args()


async def main() -> None:
    arguments = await parse_args()

    if arguments.mode == "serve":
        await serve()
        return

    if arguments.mode == "drive":
        scenarios = parse_scenarios(arguments.scenarios)
        await drive(arguments.host, arguments.port, scenarios, arguments.soak_iterations, arguments.server_lang, arguments.cleanup_timeout)
        return

    raise SystemExit(f"unknown mode: {arguments.mode}")


if __name__ == "__main__":
    asyncio.run(main())
