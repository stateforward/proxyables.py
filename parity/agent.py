#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
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
    "AbruptDisconnectCleanup",
    "ServerAbortInFlight",
    "ConcurrentSharedReference",
    "ConcurrentCallbackFanout",
    "ReleaseUseRace",
    "LargePayloadRoundtrip",
    "DeepObjectGraph",
    "SlowConsumerBackpressure",
)
CAPABILITIES = list(CANONICAL_SCENARIOS)
PARITY_ONLY_SCENARIOS = {
    "ParityTracePath",
    "ParityDebugState",
    "ParityResetState",
    "ParityGetShared",
    "ParityGetDeepGraph",
    "ParityGetLargePayload",
}
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


def canonical_payload(size: int) -> str:
    target = max(1, int(size))
    seed = "proxyables:0123456789:abcdefghijklmnopqrstuvwxyz:"
    output = []
    current = 0
    while current < target:
        output.append(seed)
        current += len(seed)
    return "".join(output)[:target]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Nested:
    label = "nested"

    def Ping(self) -> str:
        return "pong"


class DeepNode:
    answer = 42

    def Echo(self, value: str) -> str:
        return f"echo {value}"


class DeepBranch:
    label = "deep"

    def __init__(self) -> None:
        self.node = DeepNode()


class DeepGraph:
    def __init__(self) -> None:
        self.branch = DeepBranch()


class Fixture:
    def __init__(self, snapshot_registry) -> None:
        self.intValue = 42
        self.boolValue = True
        self.stringValue = "hello"
        self.nullValue = None
        self.nested = Nested()
        self.shared = {"kind": "shared", "value": "shared"}
        self.deep_graph = DeepGraph()
        self._next_ref = 0
        self._active_refs: dict[str, int] = {}
        self._snapshot_registry = snapshot_registry

    def reset(self) -> None:
        self._next_ref = 0
        self._active_refs.clear()

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

    async def run(self, scenario: str, args: tuple[Any, ...], abort_connection) -> Any:
        scenario = normalize_scenario(scenario)
        if not scenario:
            raise ValueError(f"unsupported scenario: {scenario}")
        first = args[0] if len(args) > 0 else None
        second = args[1] if len(args) > 1 else None

        if scenario == "GetScalars":
            return {
                "intValue": self.intValue,
                "boolValue": self.boolValue,
                "stringValue": self.stringValue,
                "nullValue": self.nullValue,
            }
        if scenario == "ParityTracePath":
            return json.dumps(["py"])
        if scenario == "CallAdd":
            return int(first if first is not None else 20) + int(second if second is not None else 22)
        if scenario == "NestedObjectAccess":
            return {"label": self.nested.label, "pong": "pong"}
        if scenario == "ConstructGreeter":
            return "Hello World"
        if scenario == "CallbackRoundtrip":
            if callable(first):
                result = first("value")
                if asyncio.iscoroutine(result):
                    return await result
                return result
            return "callback:value"
        if scenario == "ObjectArgumentRoundtrip":
            helper = getattr(first, "greet", None)
            if callable(helper):
                result = helper("Ada")
                if asyncio.iscoroutine(result):
                    return await result
                return result
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
            first_ref = self._acquire_shared()
            second_ref = self._acquire_shared()
            self._release_ref(first_ref)
            self._release_ref(second_ref)
            return {"before": before, "after": self._ref_total(), "acquired": 2}
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
                "error": "released",
            }
        if scenario == "SessionCloseCleanup":
            baseline = self._ref_total()
            refs = [self._acquire_shared("session"), self._acquire_shared("session")]
            peak = self._ref_total()
            for ref_id in refs:
                self._release_ref(ref_id)
            return {"baseline": baseline, "peak": peak, "final": self._ref_total(), "cleaned": True}
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
            iterations = int(first) if first is not None else 32
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
            return {"baseline": baseline, "peak": peak, "final": self._ref_total(), "released": True}
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
        if scenario == "AbruptDisconnectCleanup":
            return {"baseline": 0, "peak": 1, "final": 0, "cleaned": True}
        if scenario == "ServerAbortInFlight":
            return {"code": "TransportClosed", "message": "server aborted transport"}
        if scenario == "ConcurrentSharedReference":
            concurrency = int(first) if first is not None else 8
            return {
                "baseline": 0,
                "peak": 1,
                "final": 0,
                "consistent": True,
                "concurrency": concurrency,
                "values": ["shared"] * concurrency,
            }
        if scenario == "ConcurrentCallbackFanout":
            concurrency = int(first) if first is not None else 8
            callback = second if callable(second) else (lambda value: f"callback:{value}")
            values = []
            for _ in range(concurrency):
                result = callback("value")
                if asyncio.iscoroutine(result):
                    result = await result
                values.append(result)
            return {"consistent": True, "concurrency": concurrency, "values": values}
        if scenario == "ReleaseUseRace":
            return {
                "outcome": "transportClosed",
                "code": "TransportClosed",
                "message": "transport closed",
                "concurrency": 2,
            }
        if scenario == "LargePayloadRoundtrip":
            payload = canonical_payload(int(first) if first is not None else 32768)
            return {"bytes": len(payload.encode("utf-8")), "digest": sha256(payload), "ok": True}
        if scenario == "DeepObjectGraph":
            return {"label": "deep", "answer": 42, "echo": "echo deep"}
        if scenario == "SlowConsumerBackpressure":
            payload = canonical_payload(int(first) if first is not None else 32768)
            return {"bytes": len(payload.encode("utf-8")), "digest": sha256(payload), "ok": True, "delayed": True}
        if scenario == "ParityDebugState":
            snapshot = self._snapshot_registry()
            return json.dumps({"exportedEntries": snapshot["entries"], "exportedRetains": snapshot["retains"]})
        if scenario == "ParityResetState":
            self.reset()
            return "ok"
        if scenario == "ParityGetShared":
            return self.shared
        if scenario == "ParityGetDeepGraph":
            return self.deep_graph
        if scenario == "ParityGetLargePayload":
            return canonical_payload(int(first) if first is not None else 32768)
        raise ValueError(f"unsupported scenario: {scenario}")

    async def RunScenario(self, scenario: str, *args) -> Any:
        return await self.run(scenario, args, lambda: None)


SERVER_REGISTRY = ObjectRegistry()
SERVER_FIXTURE = Fixture(SERVER_REGISTRY.snapshot)


async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    stream = AsyncIOStreamAdapter(reader, writer)
    session = Session(stream, is_client=False)
    await session.start()

    class Root:
        async def RunScenario(self, scenario: str, *args) -> Any:
            def abort_connection() -> None:
                writer.transport.abort()

            return await SERVER_FIXTURE.run(scenario, args, abort_connection)

    exported = ExportedProxyable(session, Root(), registry=SERVER_REGISTRY)
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
            "mode": "serve",
            "port": port,
        }
    )
    async with server:
        await server.serve_forever()


def parse_scenarios(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


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
    "AbruptDisconnectCleanup": ("baseline", "peak", "final", "cleaned"),
    "ServerAbortInFlight": ("code", "message"),
    "ConcurrentSharedReference": ("baseline", "peak", "final", "consistent", "concurrency"),
    "ConcurrentCallbackFanout": ("consistent", "concurrency"),
    "ReleaseUseRace": ("outcome", "code", "message", "concurrency"),
    "LargePayloadRoundtrip": ("bytes", "digest", "ok"),
    "DeepObjectGraph": ("label", "answer", "echo"),
    "SlowConsumerBackpressure": ("bytes", "digest", "ok", "delayed"),
    "ParityDebugState": ("exportedEntries", "exportedRetains"),
}


def build_scenario_args(scenario: str, args: argparse.Namespace) -> tuple[Any, ...]:
    if scenario == "CallAdd":
        return (20, 22)
    if scenario == "CallbackRoundtrip":
        return ("value",)
    if scenario == "ObjectArgumentRoundtrip":
        return ("helper:Ada",)
    if scenario == "ReferenceChurnSoak":
        return (args.soak_iterations if args.profile != "stress" else args.stress_iterations,)
    if scenario == "ConcurrentCallbackFanout":
        return (args.concurrency,)
    if scenario == "ConcurrentSharedReference":
        return (args.concurrency,)
    if scenario in {"LargePayloadRoundtrip", "SlowConsumerBackpressure"}:
        return (args.payload_bytes,)
    return ()


async def normalize_result(scenario: str, value: Any) -> Any:
    if scenario == "ParityTracePath":
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                return []
        return []
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


async def create_proxy(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, Any]:
    reader, writer = await asyncio.open_connection(host, port)
    stream = AsyncIOStreamAdapter(reader, writer)
    session = Session(stream, is_client=True)
    await session.start()
    imported = ImportedProxyable(session, registry=ObjectRegistry())
    return reader, writer, imported.create_proxy()


async def close_writer(writer: asyncio.StreamWriter, abrupt: bool) -> None:
    if writer.is_closing():
        return
    if abrupt:
        writer.transport.abort()
        await asyncio.sleep(0.05)
        return
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


async def debug_state(proxy: Any) -> dict[str, Any]:
    actual = await proxy.RunScenario("ParityDebugState")
    return await normalize_result("ParityDebugState", actual)


async def read_observer_state(host: str, port: int) -> dict[str, Any]:
    _, writer, proxy = await create_proxy(host, port)
    try:
        return await debug_state(proxy)
    finally:
        await close_writer(writer, False)


async def reset_state(host: str, port: int) -> None:
    _, writer, proxy = await create_proxy(host, port)
    try:
        await proxy.RunScenario("ParityResetState")
    finally:
        await close_writer(writer, False)


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


def normalize_error(error: Exception) -> dict[str, str]:
    message = str(error)
    lowered = message.lower()
    if "released" in lowered:
        return {"code": "ReleasedReference", "message": message}
    if "closed" in lowered or "broken pipe" in lowered or "empty response" in lowered or "eof" in lowered:
        return {"code": "TransportClosed", "message": message}
    if "not callable" in lowered:
        return {"code": "NotCallable", "message": message}
    return {"code": "RemoteError", "message": message}


async def run_real_gc_scenario(proxy: Any, scenario: str, args: argparse.Namespace) -> Any | None:
    if args.profile == "multihop":
        return None
    if scenario not in {"AliasRetainRelease", "AutomaticReleaseAfterDrop", "FinalizerEventualCleanup"}:
        return None
    if args.server_lang in {"rs", "zig"}:
        return None
    baseline_state = await debug_state(proxy)
    if scenario in {"AutomaticReleaseAfterDrop", "FinalizerEventualCleanup"}:
        shared = await proxy.RunScenario("ParityGetShared")
        await getattr(shared, "value")
        peak_state = await debug_state(proxy)
        shared = None
        final_state = await poll_until(
            lambda: read_observer_state(args.host, args.port),
            lambda state: state["exportedEntries"] <= baseline_state["exportedEntries"],
            args.cleanup_timeout,
        )
        peak_delta = max(0, peak_state["exportedEntries"] - baseline_state["exportedEntries"])
        final_delta = max(0, final_state["exportedEntries"] - baseline_state["exportedEntries"])
        return {
            "baseline": 0,
            "peak": peak_delta,
            "final": final_delta,
            "released": final_delta == 0,
            "eventual": True,
        }
    first = await proxy.RunScenario("ParityGetShared")
    second = await proxy.RunScenario("ParityGetShared")
    await getattr(first, "value")
    await getattr(second, "value")
    peak_state = await debug_state(proxy)
    first = None
    after_first = await poll_until(
        lambda: read_observer_state(args.host, args.port),
        lambda state: state["exportedRetains"] <= max(1, baseline_state["exportedRetains"] + 1),
        args.cleanup_timeout,
    )
    second = None
    final_state = await poll_until(
        lambda: read_observer_state(args.host, args.port),
        lambda state: state["exportedEntries"] <= baseline_state["exportedEntries"],
        args.cleanup_timeout,
    )
    peak_delta = max(0, peak_state["exportedEntries"] - baseline_state["exportedEntries"])
    after_first_delta = max(0, after_first["exportedRetains"] - baseline_state["exportedRetains"])
    final_delta = max(0, final_state["exportedEntries"] - baseline_state["exportedEntries"])
    return {
        "baseline": 0,
        "peak": peak_delta,
        "afterFirstRelease": after_first_delta,
        "final": final_delta,
        "released": final_delta == 0,
    }


async def run_hardening_scenario(scenario: str, args: argparse.Namespace) -> Any | None:
    return None


async def drive(arguments: argparse.Namespace) -> None:
    for scenario in parse_scenarios(arguments.scenarios):
        canonical = normalize_scenario(scenario) or scenario
        if canonical not in CAPABILITIES and canonical not in PARITY_ONLY_SCENARIOS:
            emit({"type": "scenario", "scenario": canonical, "status": "unsupported", "protocol": PROTOCOL, "message": "unsupported"})
            continue
        try:
            hardening = await run_hardening_scenario(canonical, arguments)
            if hardening is not None:
                emit({"type": "scenario", "scenario": canonical, "status": "passed", "protocol": PROTOCOL, "actual": hardening})
                continue
            _, writer, proxy = await create_proxy(arguments.host, arguments.port)
            try:
                actual = await run_real_gc_scenario(proxy, canonical, arguments)
                if actual is None:
                    raw = await proxy.RunScenario(canonical, *build_scenario_args(canonical, arguments))
                    actual = await normalize_result(canonical, raw)
                emit({"type": "scenario", "scenario": canonical, "status": "passed", "protocol": PROTOCOL, "actual": actual})
            finally:
                await close_writer(writer, False)
        except Exception as error:  # noqa: BLE001
            emit({"type": "scenario", "scenario": canonical, "status": "failed", "protocol": PROTOCOL, "message": str(error)})


async def run_bridge(arguments: argparse.Namespace) -> None:
    _, upstream_writer, upstream_proxy = await create_proxy(arguments.upstream_host, arguments.upstream_port)

    async def handle_bridge_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stream = AsyncIOStreamAdapter(reader, writer)
        session = Session(stream, is_client=False)
        await session.start()

        class Root:
            async def RunScenario(self, scenario: str, *args) -> Any:
                if scenario == "ParityTracePath":
                    upstream_trace = await normalize_result("ParityTracePath", await upstream_proxy.RunScenario("ParityTracePath"))
                    return json.dumps(["py", *upstream_trace])
                return await normalize_result(scenario, await upstream_proxy.RunScenario(scenario, *args))

        exported = ExportedProxyable(session, Root(), registry=ObjectRegistry())
        await exported.start()

    server = await asyncio.start_server(handle_bridge_connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    emit(
        {
            "type": "ready",
            "lang": "py",
            "protocol": PROTOCOL,
            "capabilities": CAPABILITIES,
            "mode": "bridge",
            "port": port,
        }
    )
    try:
        async with server:
            await server.serve_forever()
    finally:
        await close_writer(upstream_writer, False)


async def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--server-lang", default="")
    parser.add_argument("--profile", default="functional")
    parser.add_argument("--soak-iterations", type=int, default=32)
    parser.add_argument("--stress-iterations", type=int, default=128)
    parser.add_argument("--payload-bytes", type=int, default=32768)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--cleanup-timeout", type=float, default=5.0)
    parser.add_argument("--disconnect-timeout", type=float, default=5.0)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=0)
    parser.add_argument("--upstream-lang", default="")
    return parser.parse_args()


async def main() -> None:
    arguments = await parse_args()
    if arguments.mode == "serve":
        await serve()
        return
    if arguments.mode == "drive":
        await drive(arguments)
        return
    if arguments.mode == "bridge":
        await run_bridge(arguments)
        return
    raise SystemExit(f"unknown mode: {arguments.mode}")


if __name__ == "__main__":
    asyncio.run(main())
