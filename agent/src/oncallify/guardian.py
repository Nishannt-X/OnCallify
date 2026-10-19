"""Optional second opinion from IBM Granite Guardian (served by Ollama).

Guardian is advisory and can only ADD caution: if it flags a proposal, the proposal is
marked and a contract may not auto-approve it, so a human must look. It can never make
an action allowed; the allowlist and the approval record do that. If the guardian is
not configured or not reachable, nothing changes (and the incident timeline says so).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

RISK_DEFINITION = (
    "The proposed operational action could cause harm in production: data loss, an outage, "
    "a security weakening, or a change unrelated to the described problem."
)


@dataclass(slots=True)
class GuardianVerdict:
    checked: bool
    flagged: bool
    detail: str


class Guardian:
    def __init__(self, base_url: str, model: str, *, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.model = model
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def review(self, problem: str, action_text: str) -> GuardianVerdict:
        try:
            r = self._http.post("/api/chat", json={
                "model": self.model,
                "stream": False,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": RISK_DEFINITION},
                    {"role": "user", "content": f"Problem: {problem}\nProposed action: {action_text}\n"
                                                "Is the proposed action risky as defined? Answer Yes or No."},
                ],
            })
            r.raise_for_status()
            answer = str(r.json()["message"]["content"]).strip()
        except (httpx.HTTPError, KeyError, ValueError) as e:
            return GuardianVerdict(False, False, f"guardian unavailable: {e}")
        flagged = answer.lower().startswith("yes")
        return GuardianVerdict(True, flagged, answer[:200])
