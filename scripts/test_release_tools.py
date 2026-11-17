"""Regressions for local startup and portable release inputs."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

from tools.windows.build import REQUIRED_PROJECT_FILES, copy_release_sources, is_host_file

ROOT = Path(__file__).resolve().parents[1]


class ReleaseSourceTests(unittest.TestCase):
    def test_only_runtime_sources_enter_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, dst = root / "source", root / "release"
            include = [*REQUIRED_PROJECT_FILES, "backend/app/main.py", "tools/ui-test/image-search-worker.mjs"]
            exclude = ["backend/app/.env.local", "backend/app/session.sqlite3",
                       "backend/app/models.py.orig", "backend/app/interaction.py.bak",
                       "backend/config/unrelated.json", "tools/ui-test/protocol.test.mjs"]
            for name in include + exclude:
                path = src / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic fixture")
            copy_release_sources(src, dst)
            self.assertEqual({p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file()}, set(include))
            for name in exclude[:4]:
                self.assertTrue(is_host_file(Path(name)), name)


@unittest.skipIf(os.name == "nt", "run-local.sh targets Linux and macOS")
class LocalStartupTests(unittest.TestCase):
    def test_startup_checks_dependencies_port_and_health(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(ROOT / "run-local.sh", root / "run-local.sh")
            bin_dir, deps = root / "bin", root / "deps"
            for path in [bin_dir, deps, root / "node_modules", root / "backend/.venv/bin"]:
                path.mkdir(parents=True)
            for name, script in {
                "pnpm": '#!/bin/sh\nprintf "front %s %s\\n" "$VITE_BACKEND_URL" "$*"\n',
                "adb": "#!/bin/sh\nexit 84\n",
            }.items():
                executable = bin_dir / name
                executable.write_text(script)
                executable.chmod(0o755)
            env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], PYTHONPATH=str(deps))

            def run(port):
                return subprocess.run(["bash", "run-local.sh"], cwd=root, env=dict(env, BACKEND_PORT=str(port)),
                                      capture_output=True, encoding="utf-8", errors="replace", timeout=8)

            missing = run(8090)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("后端环境未就绪", missing.stderr)
            python = root / "backend/.venv/bin/python"
            python.write_text("#!/bin/sh\nexit 1\n")
            python.chmod(0o755)
            missing_package = run(8090)
            self.assertNotEqual(missing_package.returncode, 0)
            self.assertIn("后端环境未就绪", missing_package.stderr)
            python.unlink()
            python.symlink_to(sys.executable)

            (deps / "fastapi.py").touch()
            (deps / "uvicorn.py").write_text("""
import http.server
import sys

class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/api/health' else 404)
        self.end_headers()

if __name__ == '__main__':
    port = int(sys.argv[sys.argv.index('--port') + 1])
    http.server.HTTPServer(('127.0.0.1', port), Health).serve_forever()
""")
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                port = occupied.getsockname()[1]
                blocked = run(port)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("端口", blocked.stderr)
            self.assertNotIn("通过健康检查", blocked.stdout)
            started = run(port)
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertIn("通过健康检查", started.stdout, repr(started))
            self.assertIn(f"front http://127.0.0.1:{port} dev --host 127.0.0.1", started.stdout)
            with socket.socket() as check:
                self.assertNotEqual(check.connect_ex(("127.0.0.1", port)), 0)


if __name__ == "__main__":
    unittest.main()
