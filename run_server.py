#!/usr/bin/env python
"""Server startup script with configurable host and port."""

import argparse
import sys
from pathlib import Path


def _candidate_package_dirs() -> list[Path]:
    """返回支持的本地依赖目录，优先使用新的目录名。"""
    root_dir = Path(__file__).resolve().parent
    return [
        root_dir / ".local_packages",
        root_dir / ".python_packages",
    ]


def _is_usable_uvicorn(module) -> bool:
    """校验导入结果是否为可运行的 uvicorn 模块。"""
    return hasattr(module, "run")


def _load_uvicorn():
    """优先使用当前 Python 环境，缺依赖时再回退到仓库本地依赖目录。"""
    try:
        import uvicorn as current_uvicorn
        if _is_usable_uvicorn(current_uvicorn):
            return current_uvicorn
    except ModuleNotFoundError:
        pass

    for package_dir in _candidate_package_dirs():
        if package_dir.is_dir():
            sys.path.insert(0, str(package_dir))
            try:
                import uvicorn as local_uvicorn
                if _is_usable_uvicorn(local_uvicorn):
                    return local_uvicorn
            except ModuleNotFoundError:
                pass

    print("Missing Python dependency: uvicorn")
    print("You can install backend dependencies in either of these ways:")
    print("1. python -m pip install -r requirements.txt")
    print("2. python -m pip install --target .local_packages -r requirements.txt")
    raise SystemExit(1)


uvicorn = _load_uvicorn()


def main():
    parser = argparse.ArgumentParser(description="MHTI Server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1, use 0.0.0.0 for LAN access)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )

    args = parser.parse_args()

    print(f"Starting server at http://{args.host}:{args.port}")
    if args.host == "0.0.0.0":
        print("LAN access enabled - accessible from other devices on the network")

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
