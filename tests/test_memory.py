from pathlib import Path

from kenshi_agent.memory import MemoryStore
from kenshi_agent.models import MemoryKind, MemoryWrite


def test_memory_upsert_and_recall(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3", "test")
    try:
        first = store.add(
            "run-a",
            MemoryWrite(kind=MemoryKind.FACT, content="The Hub has a bar.", salience=0.4),
        )
        second = store.add(
            "run-b",
            MemoryWrite(kind=MemoryKind.FACT, content="The Hub has a bar.", salience=0.8),
        )
        assert first == second
        records = store.recall(limit=5, minimum_salience=0.5)
        assert len(records) == 1
        assert records[0].salience == 0.8
    finally:
        store.close()
