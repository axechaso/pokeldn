"""One-shot bridge from durable trade permits to local HostTradeEngine commands."""

from dataclasses import dataclass, replace
import time

from pokeldn.frlg.remote.trade_coordinator import CommandPermit


@dataclass(frozen=True)
class FormalEngineEvent:
    kind: str
    fields: tuple


@dataclass(frozen=True)
class CommandAudit:
    permit_id: str
    trade_id: str
    selection_epoch: int
    command: str
    issued_seq: int
    consumed_at: float | None
    executed_at: float | None
    observed_at: float | None
    execution_count: int
    local_state: str
    engine_event: str | None = None
    error: str | None = None


class FormalTradeAdapter:
    """Apply coordinator permits on the engine thread; this object owns no socket."""

    _METHODS = {
        "SET_MONS_TO_TRADE": "authorize_set_mons",
        "START_TRADE": "authorize_start_trade",
        "CONFIRM_FINISH_TRADE": "authorize_finish_trade",
    }

    def __init__(self, coordinator, engine, side, *, clock=time.monotonic):
        if side not in ("A", "B"):
            raise ValueError("side must be A or B")
        if coordinator is None or engine is None:
            raise ValueError("coordinator and engine are required")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.coordinator = coordinator
        self.engine = engine
        self.side = side
        self._clock = clock
        self._active_permit = None
        self._events = []
        self.audit = []
        self._permit_by_command = {}
        self._audit_index = {}
        engine.attach_formal_adapter(self)

    def record_engine_event(self, kind, **fields):
        if not isinstance(kind, str) or not kind:
            raise ValueError("engine event kind must be non-empty")
        self._events.append(FormalEngineEvent(kind, tuple(sorted(fields.items()))))

    def take_events(self):
        events, self._events = self._events, []
        return events

    def is_applying(self, permit, command=None):
        return (self._active_permit == permit
                and (command is None or permit.command == command))

    def is_applying_command(self, command):
        return self._active_permit is not None and self._active_permit.command == command

    def apply_permit(self, permit):
        if not isinstance(permit, CommandPermit):
            raise TypeError("permit must be a CommandPermit")
        if permit.side != self.side:
            raise ValueError("permit belongs to the other side")
        method_name = self._METHODS.get(permit.command)
        if method_name is None:
            raise ValueError("permit authorizes an unsupported game command")

        status = self.coordinator.permit_status(permit)
        if status != "issued":
            return self._duplicate_audit(permit, status)

        if not self.coordinator.consume_permit(permit):
            return self._duplicate_audit(permit, self.coordinator.permit_status(permit))

        consumed_at = self._clock()
        count_before = self.engine._formal_command_counts[permit.command]
        self._active_permit = permit
        try:
            validator = getattr(self.engine, "validate_formal_permit", None)
            if not callable(validator):
                raise RuntimeError("engine has no formal permit validation entry")
            validator(permit)
            getattr(self.engine, method_name)(permit)
            self.coordinator.command_executed(permit, local_state=self.engine.state)
            executed_at = self._clock()
            record = CommandAudit(
                permit.permit_id, permit.trade_id, permit.selection_epoch,
                permit.command, permit.issued_seq, consumed_at, executed_at, None, 1,
                self.engine.state, engine_event=self.command_event(permit.command))
        except Exception as exc:
            execution_count = int(
                self.engine._formal_command_counts[permit.command] > count_before)
            try:
                if not execution_count:
                    self.coordinator.command_failed(
                        permit, error=f"{type(exc).__name__}: {exc}",
                        local_state=getattr(self.engine, "state", "unknown"))
            finally:
                record = CommandAudit(
                    permit.permit_id, permit.trade_id, permit.selection_epoch,
                    permit.command, permit.issued_seq, consumed_at,
                    self._clock() if execution_count else None, None, execution_count,
                    getattr(self.engine, "state", "unknown"),
                    engine_event=(self.command_event(permit.command)
                                  if execution_count else None),
                    error=f"{type(exc).__name__}: {exc}")
                self.audit.append(record)
                self._active_permit = None
            raise
        self.audit.append(record)
        self._permit_by_command[permit.command] = permit
        self._audit_index[permit.permit_id] = len(self.audit) - 1
        self._active_permit = None
        return record

    def _duplicate_audit(self, permit, status):
        return CommandAudit(
            permit.permit_id, permit.trade_id, permit.selection_epoch,
            permit.command, permit.issued_seq, None, None, None, 0,
            getattr(self.engine, "state", "unknown"),
            engine_event=f"already_{status or 'unknown'}")

    def observe_command(self, permit, engine_event):
        expected = {
            "SET_MONS_TO_TRADE": "INIT_BLOCK",
            "START_TRADE": "READY_FINISH_TRADE",
            "CONFIRM_FINISH_TRADE": "SAVE_STARTED",
        }.get(permit.command)
        if engine_event != expected:
            raise ValueError("engine event does not observe the permitted command")
        if self._permit_by_command.get(permit.command) != permit:
            raise ValueError("permit has not executed through this adapter")
        self.coordinator.command_observed(permit, engine_event=engine_event)
        index = self._audit_index[permit.permit_id]
        record = replace(self.audit[index], observed_at=self._clock(),
                         engine_event=engine_event)
        self.audit[index] = record
        return record

    @staticmethod
    def command_event(command):
        return {
            "SET_MONS_TO_TRADE": "set_mons_queued",
            "START_TRADE": "start_trade_queued",
            "CONFIRM_FINISH_TRADE": "finish_trade_queued",
        }[command]
