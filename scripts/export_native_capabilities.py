from pathlib import Path

from kenshi_agent.tooling.native_contract_export import (
    export_gameplay_capabilities_header,
    export_task_type_vocabulary_header,
)

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    manifest = (
        root
        / "native"
        / "KenshiAgentTelemetry"
        / "GameplayCapabilities.json"
    )
    print(export_gameplay_capabilities_header(manifest, manifest.parent))
    print(export_task_type_vocabulary_header(manifest.parent))
