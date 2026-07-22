from pathlib import Path

from kenshi_agent.schema_export import export_schemas


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for schema in export_schemas(root / "schemas"):
        print(schema)
