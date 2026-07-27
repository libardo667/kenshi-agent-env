from pathlib import Path

from kenshi_agent.native_contract_export import export_gameplay_capabilities_header

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    manifest = (
        root
        / "native"
        / "KenshiAgentTelemetry"
        / "GameplayCapabilities.json"
    )
    generated = export_gameplay_capabilities_header(manifest, manifest.parent)
    print(generated)
