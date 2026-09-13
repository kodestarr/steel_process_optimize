"""结果目录版本管理：staging -> current -> generations 原子提交。"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path


class ResultStore:
    CURRENT = "current"
    STAGING = "staging"
    GENERATIONS = "generations"
    POINTER = "current.json"

    def __init__(self, results_root: Path, run_id: str):
        self.results_root = Path(results_root)
        self.run_id = run_id
        self.root = self.results_root / run_id

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / self.STAGING).mkdir(exist_ok=True)
        (self.root / self.GENERATIONS).mkdir(exist_ok=True)

    @property
    def staging_root(self) -> Path:
        return self.root / self.STAGING

    @property
    def current_dir(self) -> Path:
        return self.root / self.CURRENT

    @property
    def generations_dir(self) -> Path:
        return self.root / self.GENERATIONS

    @property
    def pointer_path(self) -> Path:
        return self.root / self.POINTER

    def staging_dir(self, job_id: str) -> Path:
        target = self.staging_root / job_id
        target.mkdir(parents=True, exist_ok=True)
        return target

    def active_dir(self) -> Path:
        """返回当前已提交目录；兼容尚未迁移的旧运行目录。"""
        if self.current_dir.exists():
            return self.current_dir
        return self.root

    def resolve(self, filename: str | None = None) -> Path:
        base = self.active_dir()
        return base if filename is None else base / filename

    def _write_pointer(self, generation: str, committed_at: str) -> None:
        payload = {
            "run_id": self.run_id,
            "generation": generation,
            "current": self.CURRENT,
            "committed_at": committed_at,
        }
        tmp = self.pointer_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.pointer_path)

    def commit(self, staging_dir: Path, job_id: str) -> Path:
        """提交 staging，并将旧 current 归档到 generations。"""
        self.ensure_layout()
        staging_dir = Path(staging_dir)
        if not staging_dir.exists():
            raise FileNotFoundError(f"staging 目录不存在: {staging_dir}")
        generation = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{job_id}"
        if self.current_dir.exists():
            target = self.generations_dir / generation
            if target.exists():
                target = self.generations_dir / f"{generation}_{int(time.time() * 1000)}"
            os.replace(self.current_dir, target)
        else:
            # 兼容旧版直接写在运行根目录的结果，首次提交时先归档。
            legacy_files = [
                path for path in self.root.iterdir()
                if path.is_file() and path.name != self.POINTER
            ]
            if legacy_files:
                legacy_target = self.generations_dir / f"legacy_{generation}"
                legacy_target.mkdir(parents=True, exist_ok=True)
                for path in legacy_files:
                    os.replace(path, legacy_target / path.name)
        os.replace(staging_dir, self.current_dir)
        self._write_pointer(generation, datetime.now().isoformat(timespec="seconds"))
        return self.current_dir

    def discard_staging(self, job_id: str) -> None:
        target = self.staging_root / job_id
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
