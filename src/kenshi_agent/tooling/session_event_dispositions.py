"""Source-derived inventory and reviewed EvoGen dispositions for session events."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = ROOT / "src" / "kenshi_agent"
REVIEWED_PATH = ROOT / "docs" / "reconstruction" / "session_event_dispositions.json"
GENERATED_PATH = ROOT / "docs" / "generated" / "SESSION_EVENT_DISPOSITIONS.json"

ALLOWED_DISPOSITIONS = frozenset(
    {
        "exact_evogen_event",
        "subject_only_raw_evidence",
        "derived_summary",
        "intentionally_ignored",
    }
)
EVOGEN_EVENT_KINDS = frozenset(
    {
        "run_started",
        "observation",
        "observation_delta",
        "affordance_set",
        "decision",
        "binding",
        "dispatch",
        "execution_receipt",
        "outcome_observation",
        "memory_update",
        "human_intervention",
        "recovery",
        "error",
        "goal_blocked",
        "goal_achieved",
        "run_finished",
    }
)


class SessionEventDispositionError(ValueError):
    """The source inventory or reviewed disposition authority is incomplete."""


@dataclass(frozen=True, order=True, slots=True)
class SourceRecord:
    source_file: str
    owner: str
    sink_kind: str
    source_event_type: str


@dataclass(frozen=True, order=True, slots=True)
class OpenSink:
    source_file: str
    owner: str
    sink_kind: str
    expression: str


@dataclass(frozen=True, slots=True)
class SourceInventory:
    records: tuple[SourceRecord, ...]
    open_sinks: tuple[OpenSink, ...]

    @property
    def event_types(self) -> frozenset[str]:
        return frozenset(record.source_event_type for record in self.records)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "open_sinks": [asdict(sink) for sink in self.open_sinks],
                "records": [asdict(record) for record in self.records],
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_LOGGER_RECEIVERS = frozenset(
    {"logger", "self.logger", "self._logger", "self.continuity.logger"}
)
_CALLBACK_METHODS = frozenset(
    {"event", "_event", "_plan_event", "_log_read", "_reservation_event"}
)
_EVENT_METHODS = _CALLBACK_METHODS | {"progress", "write"}

# These are deliberately open string pass-throughs, not event producers.  Their
# source-local callers are inventoried independently.  Any change to this list
# is a new review boundary rather than something the extractor guesses through.
APPROVED_OPEN_SINKS: tuple[OpenSink, ...] = (
    OpenSink(
        "kenshi_agent/continuity_service.py",
        "ContinuityService._log_read",
        "session_logger.write",
        "event_type",
    ),
    OpenSink(
        "kenshi_agent/execution/kernel.py",
        "ExecutionKernel._progress",
        "callback.event",
        "progress.event_type",
    ),
    OpenSink(
        "kenshi_agent/execution/kernel.py",
        "ExecutionKernel._reservation_event",
        "callback.event",
        "event_type",
    ),
    OpenSink(
        "kenshi_agent/native_commands.py",
        "write_native_command_request_atomic",
        "reviewed_non_event.write",
        "payload",
    ),
    OpenSink(
        "kenshi_agent/operation_execution.py",
        "OperationExecutionService.submit",
        "bound_event_method",
        "self.event",
    ),
    OpenSink(
        "kenshi_agent/plan_events.py",
        "PlanEventRecorder.__call__",
        "session_logger.write",
        "event_type",
    ),
    OpenSink(
        "kenshi_agent/reporting.py",
        "ConsoleDecisionReporter._write",
        "reviewed_non_event.write",
        "value",
    ),
    OpenSink(
        "kenshi_agent/run_coordinator.py",
        "RunCoordinator._emit_control_ownership_events",
        "session_logger.write",
        "event.event_type.value",
    ),
    OpenSink(
        "kenshi_agent/run_coordinator.py",
        "RunCoordinator._plan_event",
        "session_logger.write",
        "event_type",
    ),
    OpenSink(
        "kenshi_agent/session_log.py",
        "SessionLogger._separate_unterminated_tail",
        "reviewed_non_event.write",
        "'\\n'",
    ),
    OpenSink(
        "kenshi_agent/session_log.py",
        "SessionLogger.write",
        "reviewed_non_event.write",
        "line + '\\n'",
    ),
    OpenSink(
        "kenshi_agent/speech.py",
        "WindowsSapiSpeaker.speak",
        "reviewed_non_event.write",
        "text + '\\n'",
    ),
    OpenSink(
        "kenshi_agent/telemetry/writer.py",
        "write_snapshot_atomic",
        "reviewed_non_event.write",
        "payload",
    ),
    OpenSink(
        "kenshi_agent/tooling/capability_manifest.py",
        "write_capability_manifest",
        "reviewed_non_event.write",
        "capability_manifest_bytes(manifest)",
    ),
    OpenSink(
        "kenshi_agent/tooling/generation_manifest.py",
        "write_generation_manifest",
        "reviewed_non_event.write",
        "payload",
    ),
    OpenSink(
        "kenshi_agent/tooling/graphics_profile.py",
        "_write_temporary",
        "reviewed_non_event.write",
        "rendered",
    ),
    OpenSink(
        "kenshi_agent/tooling/live_dev.py",
        "_emit_affordance_menu",
        "reviewed_non_event.write",
        "json.dumps(payload, separators=(',', ':')) + '\\n'",
    ),
    OpenSink(
        "kenshi_agent/wiki_corpus.py",
        "write_snapshot",
        "reviewed_non_event.write",
        "'\\n'",
    ),
    OpenSink(
        "kenshi_agent/wiki_corpus.py",
        "write_snapshot",
        "reviewed_non_event.write",
        "json.dumps(article.record(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))",
    ),
)


def _owner(stack: list[str]) -> str:
    return ".".join(stack) if stack else "<module>"


StringDomain = set[str] | None


def _string_values(
    expression: ast.AST,
    domains: dict[str, StringDomain],
) -> StringDomain:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return {expression.value}
    if isinstance(expression, ast.Name):
        values = domains.get(expression.id)
        return None if values is None else set(values)
    if isinstance(expression, ast.IfExp):
        body = _string_values(expression.body, domains)
        orelse = _string_values(expression.orelse, domains)
        if body is None or orelse is None:
            return None
        return body | orelse
    return None


class _LocalDomainVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.domains: dict[str, StringDomain] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Assign(self, node: ast.Assign) -> None:
        values = _string_values(node.value, self.domains)
        for target in node.targets:
            if isinstance(target, ast.Name):
                current = self.domains.get(target.id)
                if values is None or (target.id in self.domains and current is None):
                    self.domains[target.id] = None
                elif current is None:
                    self.domains[target.id] = set(values)
                else:
                    current.update(values)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            values = _string_values(node.value, self.domains)
            current = self.domains.get(node.target.id)
            if values is None or (node.target.id in self.domains and current is None):
                self.domains[node.target.id] = None
            elif current is None:
                self.domains[node.target.id] = set(values)
            else:
                current.update(values)
        self.generic_visit(node)


def _local_domains(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, StringDomain]:
    visitor = _LocalDomainVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.domains


class _SourceVisitor(ast.NodeVisitor):
    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self.stack: list[str] = []
        self.domain_stack: list[dict[str, StringDomain]] = []
        self.records: list[SourceRecord] = []
        self.open_sinks: list[OpenSink] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.domain_stack.append(_local_domains(node))
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        for statement in node.body:
            self.visit(statement)
        self.domain_stack.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _bound_method_alias(self, expression: ast.AST) -> None:
        self._scan_bound_method_alias(expression, invoked=False)

    def _scan_bound_method_alias(self, expression: ast.AST, *, invoked: bool) -> None:
        if isinstance(expression, ast.Attribute):
            if not invoked and expression.attr in _EVENT_METHODS:
                self._open("bound_event_method", expression)
            self._scan_bound_method_alias(expression.value, invoked=False)
            return
        if isinstance(expression, ast.Call):
            if self._is_dynamic_event_method(expression):
                self._open("dynamic_event_method", expression)
            self._scan_bound_method_alias(expression.func, invoked=True)
            for argument in expression.args:
                self._scan_bound_method_alias(argument, invoked=False)
            for keyword in expression.keywords:
                self._scan_bound_method_alias(keyword.value, invoked=False)
            return
        for child in ast.iter_child_nodes(expression):
            self._scan_bound_method_alias(child, invoked=False)

    @staticmethod
    def _is_dynamic_event_method(expression: ast.Call) -> bool:
        return (
            isinstance(expression.func, ast.Name)
            and expression.func.id == "getattr"
            and len(expression.args) >= 2
            and isinstance(expression.args[1], ast.Constant)
            and expression.args[1].value in _EVENT_METHODS
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        self._bound_method_alias(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._bound_method_alias(node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bound_method_alias(node.value)
        self.generic_visit(node)

    def _record(self, sink_kind: str, event_type: str) -> None:
        self.records.append(
            SourceRecord(
                source_file=self.source_file,
                owner=_owner(self.stack),
                sink_kind=sink_kind,
                source_event_type=event_type,
            )
        )

    def _open(self, sink_kind: str, expression: ast.AST) -> None:
        self.open_sinks.append(
            OpenSink(
                source_file=self.source_file,
                owner=_owner(self.stack),
                sink_kind=sink_kind,
                expression=ast.unparse(expression),
            )
        )

    def _emit_expression(self, sink_kind: str, expression: ast.AST) -> None:
        domains = self.domain_stack[-1] if self.domain_stack else {}
        values = _string_values(expression, domains)
        if values is not None:
            for value in sorted(values):
                self._record(sink_kind, value)
        else:
            self._open(sink_kind, expression)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Call) and self._is_dynamic_event_method(node.func):
            self._open("dynamic_event_method", node.func)
            self.generic_visit(node)
            return
        if not isinstance(node.func, ast.Attribute):
            self.generic_visit(node)
            return
        receiver = ast.unparse(node.func.value)
        method = node.func.attr
        event_argument = (
            node.args[0]
            if node.args
            else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "event_type"),
                None,
            )
        )
        if method == "write" and receiver in _LOGGER_RECEIVERS:
            if event_argument is None:
                self._open("session_logger.write", node)
            else:
                self._emit_expression("session_logger.write", event_argument)
        elif method == "write":
            # An unrecognized receiver might be a newly aliased SessionLogger.
            # Existing file/stream writers are explicitly reviewed open sinks;
            # any new receiver therefore fails freshness until classified.
            self._open("reviewed_non_event.write", event_argument or node)
        elif method in _CALLBACK_METHODS:
            if event_argument is None:
                self._open(f"callback.{method}", node)
            else:
                self._emit_expression(f"callback.{method}", event_argument)
        elif method == "progress":
            event_keyword = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "event_type"),
                None,
            )
            if event_keyword is None:
                self._record("operation_progress.default", "plan_step_progress")
            else:
                self._emit_expression("operation_progress.event_type", event_keyword)
        self.generic_visit(node)


def _verify_session_logger_contract(source_root: Path) -> None:
    path = source_root / "session_log.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "SessionLogger":
            continue
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == "write":
                names = [argument.arg for argument in member.args.args]
                if names[:2] == ["self", "event_type"]:
                    return
    raise SessionEventDispositionError(
        "SessionLogger.write(self, event_type, ...) is missing or changed"
    )


def _control_ownership_records(source_root: Path) -> list[SourceRecord]:
    path = source_root / "control_ownership.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    relative = path.relative_to(source_root.parent).as_posix()
    records: list[SourceRecord] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ControlOwnershipEventType":
            continue
        for member in node.body:
            value: ast.AST | None = None
            if isinstance(member, ast.Assign):
                value = member.value
            elif isinstance(member, ast.AnnAssign):
                value = member.value
            else:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                records.append(
                    SourceRecord(
                        source_file=relative,
                        owner="ControlOwnershipEventType",
                        sink_kind="enum.event_type.value",
                        source_event_type=value.value,
                    )
                )
            else:
                raise SessionEventDispositionError(
                    "ControlOwnershipEventType contains a nonliteral event member"
                )
        return records
    raise SessionEventDispositionError("ControlOwnershipEventType is missing")


def discover_source_inventory(
    source_root: Path = SOURCE_ROOT,
    *,
    validate_open_sinks: bool = True,
) -> SourceInventory:
    """Resolve every current source-local event producer without name inference."""

    _verify_session_logger_contract(source_root)
    records: list[SourceRecord] = []
    open_sinks: list[OpenSink] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _SourceVisitor(relative)
        visitor.visit(tree)
        records.extend(visitor.records)
        open_sinks.extend(visitor.open_sinks)
    records.extend(_control_ownership_records(source_root))
    inventory = SourceInventory(
        records=tuple(sorted(records)),
        open_sinks=tuple(sorted(open_sinks)),
    )
    if validate_open_sinks and inventory.open_sinks != APPROVED_OPEN_SINKS:
        raise SessionEventDispositionError(
            "Unreviewed open event sink boundary: "
            f"expected {APPROVED_OPEN_SINKS!r}, found {inventory.open_sinks!r}"
        )
    return inventory


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SessionEventDispositionError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_reviewed_dispositions(path: Path = REVIEWED_PATH) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, OSError) as exc:
        raise SessionEventDispositionError(f"Cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SessionEventDispositionError("Reviewed disposition authority must be an object")
    return value


def validate_reviewed_dispositions(
    inventory: SourceInventory,
    reviewed: dict[str, Any],
) -> list[dict[str, Any]]:
    if reviewed.get("schema_version") != 1:
        raise SessionEventDispositionError("Reviewed disposition schema_version must be 1")
    if set(reviewed) != {"schema_version", "events"}:
        raise SessionEventDispositionError(
            "Reviewed disposition authority must contain exactly schema_version and events"
        )
    rows = reviewed.get("events")
    if not isinstance(rows, list):
        raise SessionEventDispositionError("Reviewed disposition events must be a list")
    validated: dict[str, dict[str, Any]] = {}
    required = {"source_event_type", "disposition", "evogen_kind", "rationale"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != required:
            raise SessionEventDispositionError(
                f"Disposition row {index} must contain exactly {sorted(required)}"
            )
        event_type = row["source_event_type"]
        disposition = row["disposition"]
        evogen_kind = row["evogen_kind"]
        rationale = row["rationale"]
        if not isinstance(event_type, str) or not event_type:
            raise SessionEventDispositionError(f"Disposition row {index} has no event type")
        if event_type in validated:
            raise SessionEventDispositionError(f"Duplicate disposition for {event_type!r}")
        if not isinstance(disposition, str) or disposition not in ALLOWED_DISPOSITIONS:
            raise SessionEventDispositionError(
                f"Disposition for {event_type!r} is invalid: {disposition!r}"
            )
        if not isinstance(rationale, str) or not rationale.strip():
            raise SessionEventDispositionError(
                f"Disposition for {event_type!r} requires a rationale"
            )
        if disposition == "exact_evogen_event":
            if evogen_kind not in EVOGEN_EVENT_KINDS:
                raise SessionEventDispositionError(
                    f"Exact event {event_type!r} requires a valid evogen_kind"
                )
        elif evogen_kind is not None:
            raise SessionEventDispositionError(
                f"Non-exact event {event_type!r} must have evogen_kind null"
            )
        validated[event_type] = row
    missing = sorted(inventory.event_types - validated.keys())
    extra = sorted(validated.keys() - inventory.event_types)
    if missing or extra:
        raise SessionEventDispositionError(
            f"Reviewed dispositions do not match source; missing={missing}, extra={extra}"
        )
    return [validated[event_type] for event_type in sorted(validated)]


def render_generated_dispositions(
    source_root: Path = SOURCE_ROOT,
    reviewed_path: Path = REVIEWED_PATH,
) -> str:
    inventory = discover_source_inventory(source_root)
    rows = validate_reviewed_dispositions(
        inventory,
        load_reviewed_dispositions(reviewed_path),
    )
    payload = {
        "schema_version": 1,
        "allowed_dispositions": sorted(ALLOWED_DISPOSITIONS),
        "source_inventory": {
            "event_type_count": len(inventory.event_types),
            "source_record_count": len(inventory.records),
            "fingerprint": inventory.fingerprint,
            "open_string_sinks": [asdict(sink) for sink in inventory.open_sinks],
            "records": [asdict(record) for record in inventory.records],
        },
        "events": rows,
    }
    return json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"


def export_generated_dispositions(
    destination: Path = GENERATED_PATH,
    *,
    source_root: Path = SOURCE_ROOT,
    reviewed_path: Path = REVIEWED_PATH,
) -> Path:
    rendered = render_generated_dispositions(source_root, reviewed_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    return destination
