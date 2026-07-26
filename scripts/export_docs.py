from pathlib import Path

from kenshi_agent.doc_export import export_docs

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for document in export_docs(root / "docs" / "generated"):
        print(document)
