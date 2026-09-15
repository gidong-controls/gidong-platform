import unittest
from datetime import datetime, timezone

from controlstudio_host.agent_client import _safe_history
from controlstudio_host.models import Message, MessageRole


class AgentHistoryBudgetTests(unittest.TestCase):
    def test_history_is_not_arbitrarily_limited_to_one_hundred_messages(self):
        history = [
            Message(
                id=f"message-{index}",
                conversation_id="conversation",
                sequence=index,
                role=MessageRole.USER,
                content=f"고유 요구사항 {index}",
                metadata={},
                created_at=datetime.now(timezone.utc),
            )
            for index in range(150)
        ]

        payload = _safe_history(history)

        self.assertEqual(len(payload), 150)
        self.assertEqual(payload[0]["content"], "고유 요구사항 0")
        self.assertEqual(payload[-1]["content"], "고유 요구사항 149")


if __name__ == "__main__":
    unittest.main()
