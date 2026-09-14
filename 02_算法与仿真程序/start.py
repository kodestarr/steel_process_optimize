"""Start the scheduling system from a Python interpreter."""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

from project_paths import FRONTEND_BUILD_DIR, ensure_output_dirs


PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"


def log(message: str) -> None:
    print(f"  {message}", flush=True)


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def find_free_port(start: int = 8000, end: int = 8020) -> int:
    for port in range(start, end + 1):
        if port_is_free(port):
            return port
    raise RuntimeError(f"端口 {start}-{end} 全部被占用")


def find_node_binary() -> str:
    node = shutil.which("node")
    if node:
        return node
    raise RuntimeError("未找到 Node.js，请安装 Node 20.19+ 并加入 PATH")


def install_frontend_dependencies() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("未找到 npm，请安装 Node.js 20.19+ 并加入 PATH")
    log("安装前端依赖...")
    subprocess.run(
        [npm, "install", "--silent"],
        cwd=str(FRONTEND_DIR),
        check=True,
    )


def build_frontend(node_bin: str, skip_typescript: bool = False) -> None:
    tsc_js = FRONTEND_DIR / "node_modules" / "typescript" / "bin" / "tsc"
    vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not tsc_js.exists() or not vite_js.exists():
        install_frontend_dependencies()
    log(f"使用 Node: {node_bin}")
    if not skip_typescript:
        subprocess.run([node_bin, str(tsc_js), "-b"], cwd=str(FRONTEND_DIR), check=True)
    subprocess.run([node_bin, str(vite_js), "build"], cwd=str(FRONTEND_DIR), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="钢板齐套感知排产模型启动程序")
    parser.add_argument("--skip-deps", action="store_true", help="跳过 Python 依赖安装")
    parser.add_argument("--skip-build", action="store_true", help="跳过前端构建")
    parser.add_argument("--skip-tsc", action="store_true", help="跳过 TypeScript 类型检查")
    parser.add_argument("--port", type=int, default=None, help="指定服务端口")
    args = parser.parse_args()

    ensure_output_dirs()
    print("=" * 52)
    print("  钢板齐套感知排产模型 - 一键启动")
    print("=" * 52)

    if not args.skip_deps:
        if not REQUIREMENTS_FILE.exists():
            raise FileNotFoundError(f"缺少依赖文件: {REQUIREMENTS_FILE}")
        log("检查 Python 依赖...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE), "-q"],
            cwd=str(PROJECT_DIR),
            check=True,
        )

    if args.skip_build or (FRONTEND_BUILD_DIR / "index.html").exists():
        log("使用现有前端构建")
    else:
        build_frontend(find_node_binary(), skip_typescript=args.skip_tsc)
        log("前端构建完成")

    port = args.port or find_free_port()
    url = f"http://127.0.0.1:{port}"
    log(f"启动服务: {url}")
    webbrowser.open(url)

    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(BACKEND_DIR),
            check=True,
        )
    except KeyboardInterrupt:
        log("服务已停止")


if __name__ == "__main__":
    main()
