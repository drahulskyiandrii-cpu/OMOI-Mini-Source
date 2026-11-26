"""Read-only Modellkatalog. Keine Downloads, Modellimporte oder Prozessstarts."""
from dataclasses import asdict, dataclass

from .model_releases import release_for
from .model_installer import RESERVE, validate

@dataclass(frozen=True)
class ModelOption:
    id: str
    kind: str
    name: str
    role: str
    repository: str | None
    size_mb: int | None
    size_scope: str
    upstream_license: str | None
    runtime: str
    note: str

# Upstream-Dateiliste und Lizenzlabel geprüft am 24.11.2026.
# Ungefähre dezimale MB, keine exakte Download-/Disk-Kalkulation.
# Keine Installationsfreigabe: exakte Revision, Dateihashes, Rechte und
# eigenständige Laufzeitqualifikation sind noch erforderlich.
MODELS = (
    ModelOption("gemma4-e2b-gguf", "LLM", "Gemma 4 E2B Q4_0", "Referenz",
                "google/gemma-4-E2B-it-qat-q4_0-gguf", 3350, "GGUF-Textdatei",
                "Apache-2.0", "llama.cpp / Vulkan",
                "Textmodell ohne mmproj; passende llama-server.exe separat."),
    ModelOption("gemma4-12b-gguf", "LLM", "Gemma 4 12B GGUF", "Alternative",
                None, None, "Quantisierung abhängig", None, "llama.cpp / Vulkan",
                "Separates 12B-Profil; lokale GGUF auswählen. VRAM und Geschwindigkeit noch nicht abgenommen."),
    ModelOption("qwen3-tts-cf050", "TTS", "Qwen3-TTS 1.7B + CF-LoRA 50%", "Experiment",
                "siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf", None, "Adapter ca. 35 MB",
                None, "FasterQwen3TTS / CUDA",
                "Gepinnter Adapter und Base-Revision; Rechte offen; keine Weitergabe."),
    ModelOption("whisper-small-cuda", "STT", "Faster-Whisper Small", "Referenz",
                "Systran/faster-whisper-small", 486, "Modellrepository",
                "MIT", "Python 3.11 / CTranslate2 / NVIDIA CUDA",
                "Multilingual; eigenes Python/Overlay erforderlich."),
    ModelOption("qwen3-tts-1.7b", "TTS", "Qwen3-TTS 1.7B Base", "Referenz",
                "Qwen/Qwen3-TTS-12Hz-1.7B-Base", 4540, "Modellrepository",
                "Apache-2.0", "Python 3.11 / CUDA / Mini-Qwen-Worker",
                "Eigene oder erlaubte Stimme; DE/RU Tempo unverändert."),
    ModelOption("whisper-medium-ov", "STT", "Whisper Medium INT8", "Alternative",
                "OpenVINO/whisper-medium-int8-ov", 784, "Modellrepository",
                "Apache-2.0", "Node.js / OpenVINO GenAI",
                "CPU-Alternative; lokaler Node-Worker und Modellordner."),
    ModelOption("qwen3-tts-0.6b", "TTS", "Qwen3-TTS 0.6B Base", "Reserve",
                "Qwen/Qwen3-TTS-12Hz-0.6B-Base", 2520, "Modellrepository",
                "Apache-2.0", "Python 3.11 / CUDA / Mini-Qwen-Worker",
                "Reserve; RU-Tempo 1.15, DE 1.00; kein automatischer Wechsel."),
    ModelOption("piper", "TTS", "Piper", "Deaktiviert",
                None, None, "Stimmenabhängig", None, "Eigenes Python / Piper",
                "Gewichte entfernt; Lizenz jeder Stimme prüfen. Nicht automatisch reaktivieren."),
    ModelOption("custom-gguf", "LLM", "Eigenes GGUF-Modell", "Benutzerauswahl",
                None, None, "Dateiabhängig", None, "Kompatibles llama.cpp",
                "Datei im vorhandenen Dialog wählen. Rechte und Kompatibilität selbst prüfen."),
)


def catalog_rows() -> list[dict]:
    """Nur öffentliche Metadaten; keine lokalen Pfade, Caches oder Geheimnisse."""
    rows = []
    for model in MODELS:
        release = release_for(model.id)
        row = dict(asdict(model), download_allowed=False,
                   integrity_status="Revision und Dateihashes ausstehend")
        if release is not None:
            row.update(revision=release.manifest.revision,
                       exact_size_bytes=validate(release.manifest),
                       required_space_bytes=validate(release.manifest) + RESERVE,
                       file_count=len(release.manifest.files),
                       integrity_status="Dateimanifest gepinnt; Rechte und Runtime ausstehend")
        rows.append(row)
    return rows
