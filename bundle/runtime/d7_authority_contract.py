from __future__ import annotations

from typing import Protocol

class AuthorityError(Exception):
    code: str
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code

class Authority(Protocol):
    def privacy_allowed(self, context_id: str, actor_id: str, revealed_names: tuple[str, ...]) -> bool: ...
    def scope_allowed(self, context_id: str, actor_id: str, scope: str) -> bool: ...
    def dependencies_satisfied(self, dependency_ids: tuple[str, ...]) -> bool: ...
    def claims_held(self, claim_ids: tuple[str, ...], actor_id: str) -> bool: ...
    def effective_ceiling(self, context_id: str, action_kind: str) -> object: ...
    def founder_authorized(self, founder_authorization_id: str, actor_id: str, action_kind: str, target: str, scope: str, ceiling: object) -> bool: ...
    def is_done(self, idempotency_key: str) -> bool: ...
    def now_utc(self) -> str: ...
    def sign_handle(self, canonical_payload: bytes) -> str: ...
    def execute(self, handle: object) -> str: ...

class RefusingAuthority:
    def privacy_allowed(self, context_id: str, actor_id: str, revealed_names: tuple[str, ...]) -> bool:
        return False
    def scope_allowed(self, context_id: str, actor_id: str, scope: str) -> bool:
        return False
    def dependencies_satisfied(self, dependency_ids: tuple[str, ...]) -> bool:
        return False
    def claims_held(self, claim_ids: tuple[str, ...], actor_id: str) -> bool:
        return False
    def effective_ceiling(self, context_id: str, action_kind: str) -> object:
        raise AuthorityError("NO-AUTHORITY", "refusing authority has no ceiling")
    def founder_authorized(self, founder_authorization_id: str, actor_id: str, action_kind: str, target: str, scope: str, ceiling: object) -> bool:
        return False
    def is_done(self, idempotency_key: str) -> bool:
        return False
    def now_utc(self) -> str:
        return "1970-01-01T00:00:00+00:00"
    def sign_handle(self, canonical_payload: bytes) -> str:
        raise AuthorityError("NO-AUTHORITY", "refusing authority cannot sign")
    def execute(self, handle: object) -> str:
        raise AuthorityError("NO-AUTHORITY", "refusing authority cannot execute")
