"""ST-010: the configurable allowlist.

Default is DENY. A tenant policy independently expresses permitted domains,
permitted route patterns (regex on path[+query]), permitted action types, and a
risk policy for the risky/irreversible class.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

RiskDisposition = Literal["block", "require_confirmation", "flag"]


class RiskPolicy(BaseModel):
    risky_irreversible: RiskDisposition = "require_confirmation"
    risky_route_patterns: list[str] = Field(default_factory=list)
    risky_action_types: list[str] = Field(default_factory=list)


class TenantPolicy(BaseModel):
    domains: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    action_types: list[str] = Field(default_factory=list)
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)

    def allows_domain(self, host: str) -> bool:
        return any(host == d or host.endswith("." + d) for d in self.domains)

    def allows_route(self, path_q: str) -> bool:
        return any(re.search(p, path_q) for p in self.routes)

    def allows_action(self, action_type: str) -> bool:
        return action_type in self.action_types

    def route_is_risky(self, path_q: str) -> bool:
        return any(re.search(p, path_q) for p in self.risk_policy.risky_route_patterns)


class Allowlist(BaseModel):
    tenants: dict[str, TenantPolicy] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Allowlist:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(tenants={k: TenantPolicy(**v) for k, v in raw.get("tenants", {}).items()})

    def for_tenant(self, tenant_id: str) -> TenantPolicy | None:
        return self.tenants.get(tenant_id)
