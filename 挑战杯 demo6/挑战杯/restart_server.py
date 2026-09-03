"""钢板齐套感知排产模型 — 一键启动/重启脚本（智能端口管理）"""
import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
import time
from pathlib import Path

# Fix GBK encoding on Windows terminals
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PROJECT_DIR = Path(__file__).parent
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"

# 首选端口，被占用则自动递增
PREFERRED_PORT = 8000
MAX_PORT_SCAN = 8020


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


def build_frontend(node_bin: str, skip_tsc: bool = False) -> None:
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
    if not skip_tsc:
        subprocess.run([node_bin, str(tsc_js), "-b"], cwd=str(FRONTEND_DIR), check=True)
    subprocess.run([node_bin, str(vite_js), "build"], cwd=str(FRONTEND_DIR), check=True)


# ═══════════════════════════════════════════════════════════
#  端口进程管理
# ═══════════════════════════════════════════════════════════

def find_port_pids(port: int) -> list[int]:
    """查找占用指定端口的所有 PID。"""
    pids = set()
    try:
        # 同时匹配 127.0.0.1:PORT 和 0.0.0.0:PORT
        out = subprocess.check_output(
            f'netstat -ano | findstr ":{port} "',
            shell=True, text=True,
        )
        for line in out.strip().split('\n'):
            if 'LISTENING' not in line:
                continue
            parts = line.strip().split()
            if len(parts) >= 5:
                try:
                    pids.add(int(parts[-1]))
                except ValueError:
                    pass
    except subprocess.CalledProcessError:
        pass
    return sorted(pids)


def kill_pid_force(pid: int) -> bool:
    """三级强制杀进程：taskkill /F → taskkill /F /T → tskill。返回是否成功。"""
    strategies = [
        f'taskkill /F /PID {pid}',
        f'taskkill /F /T /PID {pid}',
        f'tskill {pid}',
    ]
    for cmd in strategies:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return True
        except Exception:
            continue
    return False


def free_port(port: int) -> bool:
    """清理指定端口的所有占用进程。返回端口是否已释放。"""
    pids = find_port_pids(port)
    if not pids:
        return True  # 本来就是空闲的

    log(f"发现 {len(pids)} 个进程占用端口 {port}，正在清理...")
    killed = 0
    for pid in pids:
        if kill_pid_force(pid):
            killed += 1
        else:
            print(f"    [WARN] PID {pid} - permission denied")

    time.sleep(1.0)  # 等系统释放端口

    remaining = find_port_pids(port)
    if remaining:
        log(f"[WARN] {len(remaining)} processes still on port {port} (PID: {remaining})")
        return False
    else:
        log(f"端口 {port} 已释放（清理了 {killed} 个进程）")
        return True


def find_free_port(start: int = PREFERRED_PORT, max_port: int = MAX_PORT_SCAN) -> int:
    """从 start 开始扫描，返回第一个空闲端口。"""
    for port in range(start, max_port + 1):
        if not find_port_pids(port):
            return port
    raise RuntimeError(f"端口 {start}-{max_port} 全部被占用，请手动释放后重试")


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="钢板齐套感知排产模型启动/重启")
    parser.add_argument("--skip-deps", action="store_true", help="跳过 pip 依赖安装")
    parser.add_argument("--skip-build", action="store_true", help="跳过前端构建，直接使用现有 dist")
    parser.add_argument("--skip-tsc", action="store_true", help="只运行 vite build，跳过 tsc 类型检查")
    args = parser.parse_args()

    print("=" * 52)
    print("  钢板齐套感知排产模型 — 一键启动")
    print("=" * 52)

    # ── 1. 依赖 ──
    print("\n[1/3] 检查后端依赖...")
    if args.skip_deps:
        log("跳过依赖检查")
    else:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r",
             str(BACKEND_DIR / "requirements.txt"), "-q"],
            cwd=str(PROJECT_DIR),
        )
        log("后端依赖 OK")

    # ── 2. 端口清理 + 前端构建 ──
    print("\n[2/3] 准备端口 & 构建前端...")

    # 先尝试释放首选端口
    freed = free_port(PREFERRED_PORT)

    # 如果释放失败，找下一个空闲端口
    if freed:
        port = PREFERRED_PORT
    else:
        port = find_free_port(PREFERRED_PORT + 1)
        if port != PREFERRED_PORT:
            print(f"  [WARN] Port {PREFERRED_PORT} busy -> using port {port}")

    # 前端构建
    node_bin = find_node_binary()
    if args.skip_build:
        log("跳过前端构建，使用现有 dist")
    else:
        build_frontend(node_bin, skip_tsc=args.skip_tsc)
        log("前端构建完成")

    # ── 3. 启动 ──
    print(f"\n[3/3] 启动服务器 → http://127.0.0.1:{port} ...")
    webbrowser.open(f"http://127.0.0.1:{port}")
    log(f"浏览器已打开")
    log('按 Ctrl+C 可停止服务器')
    print("\n" + "=" * 52 + "\n")

    subprocess.run(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND_DIR),
    )


if __name__ == "__main__":
    main()
