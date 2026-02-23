"""
Persistent executor session with Docker sandbox support.

This module provides a code-interpreter-like execution API.
It supports two backends:
- docker: run and persist state inside an isolated container session.
- local: fallback local subprocess backend.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


_WORKER_SCRIPT = r'''
import io
import json
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr

ns = {}

def emit(payload):
    raw = json.dumps(payload, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(raw.encode("utf-8"))
    sys.stdout.buffer.flush()

def safe_emit(payload):
    try:
        emit(payload)
    except Exception:
        try:
            fallback = {k: (repr(v)[:200] if not isinstance(v, (bool, int, float, type(None))) else v) for k, v in payload.items()}
            emit(fallback)
        except Exception:
            pass

while True:
    raw = sys.stdin.buffer.readline()
    if not raw:
        break
    line = raw.decode("utf-8", errors="replace").strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception as e:
        safe_emit({"id": None, "ok": False, "error": f"invalid_json: {e}"})
        continue

    req_id = msg.get("id")
    op = msg.get("op")

    if op == "close":
        emit({"id": req_id, "ok": True})
        break

    if op == "reset":
        ns = {}
        emit({"id": req_id, "ok": True})
        continue

    if op == "ping":
        emit({"id": req_id, "ok": True, "message": "pong"})
        continue

    if op == "exec":
        code = msg.get("code", "")
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(code, ns, ns)
            emit({
                "id": req_id,
                "ok": True,
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
            })
        except Exception as e:
            safe_emit({
                "id": req_id,
                "ok": False,
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
        continue

    emit({"id": req_id, "ok": False, "error": f"unknown_op: {op}"})
'''


@dataclass
class ExecutionResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    traceback: Optional[str] = None
    exit_code: int = 0
    timed_out: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "traceback": self.traceback,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
        }


class ExecutorSession:
    """
    Persistent execution session.

    Backends:
    - docker: isolated container sandbox, auto-started by the model workflow.
    - local: host subprocess fallback (for environments without Docker).
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        workdir: Optional[str | Path] = None,
        python_bin: Optional[str] = None,
        auto_start: bool = True,
        persistent: bool = True,
        backend: str = "auto",  # auto | docker | local
        docker_bin: str = "docker",
        docker_image: Optional[str] = None,
        docker_auto_pull: bool = True,
        docker_disable_network: bool = False,
        docker_mount_workdir: bool = True,
        docker_extra_run_args: Optional[list[str]] = None,
    ):
        self.session_id = session_id or uuid.uuid4().hex[:10]
        self.workdir = Path(workdir or Path.cwd())
        self.python_bin = python_bin or sys.executable
        self.auto_start = auto_start
        self.persistent = persistent

        self.backend = (backend or "auto").strip().lower()
        self.docker_bin = docker_bin
        self.docker_image = (
            docker_image
            or os.getenv("PYGEN_SANDBOX_IMAGE")
            or "mcr.microsoft.com/playwright/python:v1.41.0-jammy"
        )
        self.docker_auto_pull = bool(docker_auto_pull)
        self.docker_disable_network = bool(docker_disable_network)
        self.docker_mount_workdir = bool(docker_mount_workdir)
        self.docker_extra_run_args = list(docker_extra_run_args or [])

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

        self._active_backend: Optional[str] = None
        self._container_name: Optional[str] = None

    @property
    def started(self) -> bool:
        if self._active_backend == "docker":
            if not self._container_name:
                return False
            if not self.persistent:
                return True
            return self._proc is not None and self._proc.returncode is None
        return self._proc is not None and self._proc.returncode is None

    def _run_host_cmd(self, args: list[str], timeout_sec: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )

    def _docker_available(self) -> tuple[bool, str]:
        try:
            proc = self._run_host_cmd([self.docker_bin, "info", "--format", "{{.ServerVersion}}"], timeout_sec=15)
        except FileNotFoundError:
            return False, "docker_cli_not_found"
        except Exception as exc:
            return False, str(exc)

        if proc.returncode == 0:
            return True, (proc.stdout or "").strip()
        return False, ((proc.stderr or proc.stdout or "docker_unavailable").strip()[:400])

    def _resolve_backend(self) -> str:
        if self.backend in {"docker", "local"}:
            return self.backend

        ok, _ = self._docker_available()
        return "docker" if ok else "local"

    def _ensure_docker_image(self) -> None:
        inspect = self._run_host_cmd([self.docker_bin, "image", "inspect", self.docker_image], timeout_sec=30)
        if inspect.returncode == 0:
            return

        if not self.docker_auto_pull:
            reason = (inspect.stderr or inspect.stdout or "image_not_found").strip()[:400]
            raise RuntimeError(f"Docker image unavailable: {self.docker_image}. {reason}")

        pull = self._run_host_cmd([self.docker_bin, "pull", self.docker_image], timeout_sec=900)
        if pull.returncode != 0:
            reason = (pull.stderr or pull.stdout or "docker_pull_failed").strip()[:800]
            raise RuntimeError(f"Docker pull failed for {self.docker_image}: {reason}")

    def _docker_remove_container_if_exists(self, name: str) -> None:
        self._run_host_cmd([self.docker_bin, "rm", "-f", name], timeout_sec=30)

    def _workdir_mount_path(self) -> str:
        # Docker Desktop accepts drive-style paths like D:/... on Windows.
        return self.workdir.resolve().as_posix()

    def _docker_start_container(self) -> str:
        container_name = f"pygen-exec-{self.session_id}"
        self._docker_remove_container_if_exists(container_name)

        args = [
            self.docker_bin,
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-w",
            "/workspace",
        ]

        if self.docker_mount_workdir:
            args.extend(["-v", f"{self._workdir_mount_path()}:/workspace"])

        if self.docker_disable_network:
            args.extend(["--network", "none"])

        if self.docker_extra_run_args:
            args.extend(self.docker_extra_run_args)

        args.extend([self.docker_image, "tail", "-f", "/dev/null"])

        proc = self._run_host_cmd(args, timeout_sec=120)
        if proc.returncode != 0:
            reason = (proc.stderr or proc.stdout or "docker_run_failed").strip()[:800]
            raise RuntimeError(f"Failed to start sandbox container: {reason}")

        cid = (proc.stdout or "").strip()
        if not cid:
            raise RuntimeError("Failed to start sandbox container: empty container id")

        return container_name

    async def _start_local_worker(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            self.python_bin,
            "-u",
            "-c",
            _WORKER_SCRIPT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workdir),
        )

    async def _start_docker_worker(self) -> None:
        await asyncio.to_thread(self._ensure_docker_image)
        self._container_name = await asyncio.to_thread(self._docker_start_container)

        if not self.persistent:
            return

        self._proc = await asyncio.create_subprocess_exec(
            self.docker_bin,
            "exec",
            "-i",
            self._container_name,
            "python",
            "-u",
            "-c",
            _WORKER_SCRIPT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        ping = await self._request({"op": "ping"}, timeout_sec=20)
        if not ping.get("ok"):
            err = ping.get("error", "docker_worker_ping_failed")
            await self.close(force=True)
            raise RuntimeError(f"Sandbox worker failed to start: {err}")

    async def start(self) -> None:
        async with self._lock:
            if self.started:
                return

            self.workdir.mkdir(parents=True, exist_ok=True)
            self._active_backend = self._resolve_backend()

            if self._active_backend == "docker":
                ok, reason = self._docker_available()
                if not ok:
                    if self.backend == "docker":
                        raise RuntimeError(f"Docker backend required but unavailable: {reason}")
                    self._active_backend = "local"

            if self._active_backend == "docker":
                await self._start_docker_worker()
            else:
                await self._start_local_worker()

    async def _ensure_started(self) -> None:
        if self.started:
            return
        if self.auto_start:
            await self.start()
        else:
            raise RuntimeError("Executor session not started")

    async def _request(self, payload: Dict[str, Any], timeout_sec: int = 120) -> Dict[str, Any]:
        await self._ensure_started()

        if not self.persistent:
            return {
                "id": payload.get("id") or uuid.uuid4().hex[:12],
                "ok": False,
                "error": "persistent_session_required",
            }

        assert self._proc is not None
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None

        request_id = payload.get("id") or uuid.uuid4().hex[:12]
        payload["id"] = request_id

        encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self._proc.stdin.write(encoded)
        await self._proc.stdin.drain()

        try:
            while True:
                line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=timeout_sec)
                if not line:
                    stderr = ""
                    if self._proc.stderr:
                        pending = await self._proc.stderr.read()
                        stderr = pending.decode("utf-8", errors="replace")
                    raise RuntimeError(f"Executor closed unexpectedly. stderr={stderr[:1200]}")

                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                msg = json.loads(decoded)
                if msg.get("id") == request_id or msg.get("id") is None:
                    return msg
        except asyncio.CancelledError:
            await self.close(force=True)
            raise
        except asyncio.TimeoutError:
            await self.close(force=True)
            return {
                "id": request_id,
                "ok": False,
                "error": f"Execution timed out after {timeout_sec}s",
                "timed_out": True,
            }

    async def run_python(self, code: str, timeout_sec: int = 120) -> ExecutionResult:
        if not self.persistent:
            return await self._run_python_one_shot(code=code, timeout_sec=timeout_sec)

        response = await self._request({"op": "exec", "code": code}, timeout_sec=timeout_sec)
        return ExecutionResult(
            success=bool(response.get("ok", False)),
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
            error=response.get("error"),
            traceback=response.get("traceback"),
            exit_code=0 if response.get("ok", False) else 1,
            timed_out=bool(response.get("timed_out", False)),
        )

    async def _run_python_one_shot(self, code: str, timeout_sec: int = 120) -> ExecutionResult:
        await self._ensure_started()

        if self._active_backend == "docker":
            assert self._container_name is not None
            proc = await asyncio.create_subprocess_exec(
                self.docker_bin,
                "exec",
                "-i",
                self._container_name,
                "python",
                "-u",
                "-c",
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                self.python_bin,
                "-u",
                "-c",
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workdir),
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                error=None if proc.returncode == 0 else f"Process exited with {proc.returncode}",
                exit_code=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ExecutionResult(
                success=False,
                error=f"Execution timed out after {timeout_sec}s",
                timed_out=True,
                exit_code=124,
            )

    async def run_shell(
        self,
        command: str,
        timeout_sec: int = 120,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        await self._ensure_started()

        if self._active_backend == "docker":
            assert self._container_name is not None
            args = [self.docker_bin, "exec"]
            if env:
                for k, v in env.items():
                    args.extend(["-e", f"{k}={v}"])
            args.extend([self._container_name, "sh", "-lc", command])

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            merged_env = os.environ.copy()
            if env:
                merged_env.update(env)

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workdir),
                env=merged_env,
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                error=None if proc.returncode == 0 else f"Shell exited with {proc.returncode}",
                exit_code=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ExecutionResult(
                success=False,
                error=f"Shell command timed out after {timeout_sec}s",
                timed_out=True,
                exit_code=124,
            )

    async def check_python_requirements(self, requirements: list[str], timeout_sec: int = 90) -> Dict[str, Any]:
        """
        Check whether requirement specifiers are already satisfied in current Python environment.
        Returns:
          {
            "checked": [...],
            "satisfied": [...],
            "missing": [...],
            "conflicts": [...],
            "details": {req: {"installed": bool, "version": "...", "ok": bool, "reason": "..."}}
          }
        """
        reqs_json = json.dumps(requirements or [])
        check_code = f"""
import json
import importlib.metadata as md
out = {{
  "checked": [],
  "satisfied": [],
  "missing": [],
  "conflicts": [],
  "details": {{}}
}}
reqs = json.loads(r'''{reqs_json}''')
try:
    from packaging.requirements import Requirement
except Exception:
    Requirement = None

for raw in reqs:
    req = str(raw).strip()
    if not req:
        continue
    out["checked"].append(req)

    if Requirement is None:
        name = req.split("==")[0].split(">=")[0].split("<=")[0].split(">")[0].split("<")[0].split("~=")[0].split("[")[0].strip()
        try:
            ver = md.version(name)
            out["satisfied"].append(req)
            out["details"][req] = {{"installed": True, "version": ver, "ok": True, "reason": "installed(no_spec_check)"}}
        except Exception:
            out["missing"].append(req)
            out["details"][req] = {{"installed": False, "version": None, "ok": False, "reason": "missing"}}
        continue

    try:
        parsed = Requirement(req)
        name = parsed.name
        try:
            ver = md.version(name)
            if parsed.specifier and (ver not in parsed.specifier):
                out["conflicts"].append(req)
                out["details"][req] = {{"installed": True, "version": ver, "ok": False, "reason": "version_conflict"}}
            else:
                out["satisfied"].append(req)
                out["details"][req] = {{"installed": True, "version": ver, "ok": True, "reason": "satisfied"}}
        except md.PackageNotFoundError:
            out["missing"].append(req)
            out["details"][req] = {{"installed": False, "version": None, "ok": False, "reason": "missing"}}
    except Exception as e:
        out["conflicts"].append(req)
        out["details"][req] = {{"installed": None, "version": None, "ok": False, "reason": f"parse_error: {{e}}"}}

print(json.dumps(out, ensure_ascii=False))
"""
        result = await self.run_python(check_code, timeout_sec=timeout_sec)
        if not result.success:
            return {
                "checked": requirements or [],
                "satisfied": [],
                "missing": requirements or [],
                "conflicts": [],
                "details": {},
                "error": result.error or "requirement_check_failed",
            }

        try:
            parsed = json.loads((result.stdout or "").strip().splitlines()[-1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        return {
            "checked": requirements or [],
            "satisfied": [],
            "missing": requirements or [],
            "conflicts": [],
            "details": {},
            "error": "requirement_check_parse_failed",
        }

    async def install_python_packages(self, packages: list[str], timeout_sec: int = 900) -> ExecutionResult:
        if not packages:
            return ExecutionResult(success=False, error="No packages provided", exit_code=2)

        if self._active_backend == "docker":
            quoted = " ".join([shlex.quote(pkg) for pkg in packages])
            cmd = f"python -m pip install {quoted}"
        else:
            quoted = " ".join([f'"{pkg}"' for pkg in packages])
            cmd = f"\"{self.python_bin}\" -m pip install {quoted}"

        return await self.run_shell(command=cmd, timeout_sec=timeout_sec)

    async def reset(self) -> bool:
        if not self.persistent:
            return True
        response = await self._request({"op": "reset"}, timeout_sec=30)
        return bool(response.get("ok", False))

    async def _stop_container(self) -> None:
        if not self._container_name:
            return
        name = self._container_name
        self._container_name = None
        await asyncio.to_thread(self._docker_remove_container_if_exists, name)

    async def close(self, force: bool = False) -> None:
        async with self._lock:
            proc = self._proc
            self._proc = None

            if proc is not None:
                try:
                    if proc.returncode is None and not force and self.persistent:
                        if proc.stdin:
                            proc.stdin.write((json.dumps({"id": "close", "op": "close"}) + "\n").encode("utf-8"))
                            await proc.stdin.drain()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=3)
                        except asyncio.TimeoutError:
                            proc.terminate()
                            await asyncio.wait_for(proc.wait(), timeout=3)
                    elif proc.returncode is None:
                        proc.kill()
                        await proc.wait()
                except BaseException:
                    if proc.returncode is None:
                        proc.kill()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=3)
                        except Exception:
                            pass

            if self._active_backend == "docker":
                try:
                    await self._stop_container()
                except Exception:
                    pass

            self._active_backend = None


__all__ = ["ExecutionResult", "ExecutorSession"]
