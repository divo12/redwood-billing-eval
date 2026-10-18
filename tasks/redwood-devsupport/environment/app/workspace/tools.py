from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool


class Tools(SkillTools, SubagentTools, ToolKitBase):
    @is_tool(ToolType.WRITE)
    def escalate(
        self, account_id: str, team: str, summary: str, details: dict[str, str] | None = None,
    ) -> dict:
        """Insert an escalated case record.

        Args:
            account_id: The account identifier.
            team: The receiving team.
            summary: A case summary.
            details: Named case details.
        """
        self.billing.query("accounts", {"account_id": account_id})
        case = self.billing.insert("cases", {
            "account_id": account_id,
            "team": team,
            "summary": summary,
            "details": details or {},
            "dispute_id": None,
            "status": "escalated",
        })
        return {key: case[key] for key in ("case_id", "team", "status")}
