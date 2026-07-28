from pathlib import Path

from kenshi_agent.mutation_ledger import LEDGER_NAME, export_mutation_ledger

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    generated = root / "docs" / "generated"
    print(
        export_mutation_ledger(
            generated,
            repo_root=root,
            existing=generated / LEDGER_NAME,
            artifact_dir=root / "runs" / "mutation",
        )
    )
