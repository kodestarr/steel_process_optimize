"""Canonical paths for code, inputs, and generated outputs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parent
SUBMISSION_ROOT = CODE_ROOT.parent
INPUT_DIR = SUBMISSION_ROOT / "03_赛题与输入数据"
OUTPUT_ROOT = SUBMISSION_ROOT / "05_系统输出"
FRONTEND_BUILD_DIR = CODE_ROOT / "frontend" / "dist"
EVIDENCE_DIR = SUBMISSION_ROOT / "04_仿真实验举证材料"

OUTPUT_DIRS = (OUTPUT_ROOT,)


def find_run_directory(run_id: str) -> Path | None:
    if not OUTPUT_ROOT.exists():
        return None
    suffix = f"_{run_id}"
    for path in OUTPUT_ROOT.iterdir():
        if path.is_dir() and path.name.endswith(suffix):
            return path
    return None


def create_run_directory(run_id: str, created_at: datetime | None = None) -> Path:
    existing = find_run_directory(run_id)
    if existing is not None:
        return existing
    timestamp = (created_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_ROOT / f"运行_{timestamp}_{run_id}"
    (run_dir / "上传数据").mkdir(parents=True, exist_ok=True)
    (run_dir / "排产结果").mkdir(parents=True, exist_ok=True)
    return run_dir


def run_upload_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "上传数据"


def run_result_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "排产结果"


def run_history_file(run_dir: Path) -> Path:
    return Path(run_dir) / "运行历史.json"


def ensure_output_dirs() -> None:
    for path in OUTPUT_DIRS:
        path.mkdir(parents=True, exist_ok=True)


def ensure_inside_submission(path: str | Path) -> Path:
    resolved = (SUBMISSION_ROOT / path).resolve()
    try:
        resolved.relative_to(SUBMISSION_ROOT)
    except ValueError as exc:
        raise ValueError(f"Path escapes submission directory: {path}") from exc
    return resolved


def resolve_recorded_path(path: str | Path) -> Path:
    return ensure_inside_submission(path)


def to_submission_relative(path: str | Path) -> str:
    resolved = ensure_inside_submission(path)
    return resolved.relative_to(SUBMISSION_ROOT).as_posix()
