"""
钢板齐套感知排产模型 - 一键启动脚本
用法：双击启动.py 或在终端执行 python 启动.py
"""
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"


def log(msg: str):
    print(f"  {msg}")


def find_node_binary() -> str:
    """查找可用的 Node.js 可执行文件，优先使用 Codex 自带运行时。"""
    env_node = os.environ.get("NODE_BIN")
    if env_node and Path(env_node).exists():
        return str(env_node)

    candidates: list[str] = []
    runtime_root = Path.home() / ".cache" / "codex-runtimes"
    if runtime_root.exists():
        for runtime in sorted(runtime_root.iterdir()):
            node = runtime / "dependencies" / "node" / "bin" / "node.exe"
            if node.exists():
                candidates.append(str(node))

    candidates.append(r"C:\Program Files\nodejs\node.exe")
    sys_node = shutil.which("node")
    if sys_node:
        candidates.append(sys_node)

    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("未找到 Node.js，请安装 Node 20.19+ 或设置环境变量 NODE_BIN")


def build_frontend(node_bin: str) -> None:
    """使用指定 Node 直接执行 tsc 和 vite，绕开 npm 入口和部分 Node 版本的构建崩溃。"""
    tsc_js = FRONTEND_DIR / "node_modules" / "typescript" / "bin" / "tsc"
    vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not tsc_js.exists() or not vite_js.exists():
        log("前端依赖不完整，正在安装...")
        subprocess.run(
            ["npm", "install", "--silent"],
            cwd=str(FRONTEND_DIR),
            shell=True,
            check=True,
        )

    log(f"使用 Node: {node_bin}")
    subprocess.run([node_bin, str(tsc_js), "-b"], cwd=str(FRONTEND_DIR), check=True)
    subprocess.run([node_bin, str(vite_js), "build"], cwd=str(FRONTEND_DIR), check=True)


def step(n: int, total: int, msg: str):
    print(f"\n[{n}/{total}] {msg}...")


def main():
    print("=" * 50)
    print("  钢板齐套感知排产模型 - 一键启动")
    print("=" * 50)

    total = 4 if not (FRONTEND_DIST / "index.html").exists() else 2

    # 1. 安装后端依赖
    step(1, total, "检查后端依赖")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(BACKEND_DIR / "requirements.txt"), "-q"],
        cwd=str(PROJECT_DIR),
    )
    log("后端依赖 OK")

    # 2-3. 构建前端（如果还没有）
    if not (FRONTEND_DIST / "index.html").exists():
        step(2, total, "安装前端依赖")
        subprocess.run(
            ["npm", "install", "--silent"],
            cwd=str(FRONTEND_DIR),
            shell=True,
        )
        log("前端依赖 OK")

        step(3, total, "构建前端")
        build_frontend(find_node_binary())
        log("前端构建完成")
    else:
        log("前端已构建，跳过")

    # 4. 启动
    step(total, total, "启动服务器")
    url = "http://127.0.0.1:8000"
    webbrowser.open(url)
    log(f"浏览器已打开 {url}")
    log('按 Ctrl+C 可停止服务器')
    print("\n" + "=" * 50 + "\n")

    subprocess.run(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
        cwd=str(BACKEND_DIR),
    )


if __name__ == "__main__":
    main()
