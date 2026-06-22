from __future__ import annotations

from contextlib import contextmanager
from threading import RLock


_LOCK = RLock()
_PORT_OWNERS: dict[str, set[str]] = {}


class PortBusyError(RuntimeError):
    pass


def claim_port(port: str, owner: str) -> None:
    port_key = str(port or "").strip()
    owner_name = str(owner or "").strip()
    if not port_key or not owner_name:
        return
    with _LOCK:
        owners = _PORT_OWNERS.setdefault(port_key, set())
        owners.add(owner_name)


def try_claim_port(port: str, owner: str) -> bool:
    port_key = str(port or "").strip()
    owner_name = str(owner or "").strip()
    if not port_key or not owner_name:
        return False
    with _LOCK:
        owners = _PORT_OWNERS.setdefault(port_key, set())
        if owners and owner_name not in owners:
            return False
        owners.add(owner_name)
        return True


@contextmanager
def claim_port_context(port: str, owner: str):
    claimed = try_claim_port(port, owner)
    try:
        yield claimed
    finally:
        if claimed:
            release_port(port, owner)


def release_port(port: str, owner: str | None = None) -> None:
    port_key = str(port or "").strip()
    if not port_key:
        return
    with _LOCK:
        if port_key not in _PORT_OWNERS:
            return
        if owner is None:
            _PORT_OWNERS.pop(port_key, None)
            return
        owner_name = str(owner or "").strip()
        if not owner_name:
            _PORT_OWNERS.pop(port_key, None)
            return
        owners = _PORT_OWNERS[port_key]
        owners.discard(owner_name)
        if not owners:
            _PORT_OWNERS.pop(port_key, None)


def clear_port(port: str) -> None:
    release_port(port, None)


def port_owners(port: str) -> tuple[str, ...]:
    port_key = str(port or "").strip()
    if not port_key:
        return ()
    with _LOCK:
        owners = _PORT_OWNERS.get(port_key, set())
        return tuple(sorted(owners))


def port_owner_label(port: str) -> str:
    owners = port_owners(port)
    if not owners:
        return ""
    if len(owners) == 1:
        return owners[0]
    return ", ".join(owners)


def snapshot_port_ownership() -> dict[str, str]:
    with _LOCK:
        return {port: ", ".join(sorted(owners)) for port, owners in _PORT_OWNERS.items() if owners}
