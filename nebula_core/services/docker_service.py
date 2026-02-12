import docker
import time
from ..db import get_connection, SYSTEM_DB
from ..core.context import context

# DockerService should not crash application startup if the Docker daemon/socket
# is unavailable. Attempt to create client, but fall back to a disabled state
# and provide clear errors from methods when called.

class DockerService:
    def __init__(self):
        try:
            self.client = docker.from_env()
            self.available = True
        except Exception as e:
            context.logger.warning(f"Docker client init failed: {e}")
            self.client = None
            self.available = False
        self._net_state = {}

    def ensure_client(self):
        """Attempt to (re)initialize docker client on demand."""
        if self.client is not None:
            return True
        try:
            self.client = docker.from_env()
            self.available = True
            context.logger.info("Docker client connected on-demand")
            return True
        except Exception as e:
            context.logger.debug(f"Docker client on-demand init failed: {e}")
            self.client = None
            self.available = False
            return False

    def resolve_container_id(self, container_id: str) -> str:
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")
        try:
            container = self.client.containers.get(container_id)
            return container.id
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

    def list_containers(self, username: str, is_staff: bool):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        containers = self.client.containers.list(all=True)
        
        with get_connection(SYSTEM_DB) as conn:
            if is_staff:
                permissions = conn.execute("SELECT container_id, username FROM container_permissions").fetchall()
                perm_map = {}
                for p in permissions:
                    perm_map.setdefault(p["container_id"], []).append(p["username"])
            else:
                allowed = conn.execute("SELECT container_id FROM container_permissions WHERE username=?", (username,)).fetchall()
                allowed_ids = {r["container_id"] for r in allowed}

        res = []
        for c in containers:
            if not is_staff and c.id not in allowed_ids:
                continue
            
            res.append({
                "id": c.id[:12],
                "name": c.name,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else "unknown",
                "users": perm_map.get(c.id, []) if is_staff else [username]
            })
        return res

    def get_usage_summary(self, username: str, is_staff: bool):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        containers = self.client.containers.list(all=True)

        if not is_staff:
            with get_connection(SYSTEM_DB) as conn:
                allowed = conn.execute(
                    "SELECT container_id FROM container_permissions WHERE username=?",
                    (username,)
                ).fetchall()
                allowed_ids = {r["container_id"] for r in allowed}
            containers = [c for c in containers if c.id in allowed_ids]

        total = len(containers)
        running = 0
        cpu_total = 0.0
        mem_used_bytes = 0
        mem_limit_bytes = 0
        net_tx_bytes = 0
        net_rx_bytes = 0

        for c in containers:
            try:
                c.reload()
                if c.status == "running":
                    running += 1

                stats = c.stats(stream=False)
                cpu_total += self._calc_cpu_percent(stats)

                mem = stats.get("memory_stats", {}) or {}
                used = float(mem.get("usage") or 0.0)
                limit = float(mem.get("limit") or 0.0)
                mem_used_bytes += max(0.0, used)
                mem_limit_bytes += max(0.0, limit)

                nets = stats.get("networks") or {}
                for _, n in nets.items():
                    net_tx_bytes += int(n.get("tx_bytes") or 0)
                    net_rx_bytes += int(n.get("rx_bytes") or 0)
            except Exception:
                continue

        now = time.time()
        key = f"{username}:{'staff' if is_staff else 'user'}"
        prev = self._net_state.get(key)
        tx_mbps = 0.0
        rx_mbps = 0.0
        if prev:
            p_tx, p_rx, p_ts = prev
            dt = max(0.2, now - p_ts)
            tx_mbps = max(0.0, (net_tx_bytes - p_tx) / dt / 1048576.0)
            rx_mbps = max(0.0, (net_rx_bytes - p_rx) / dt / 1048576.0)
        self._net_state[key] = (net_tx_bytes, net_rx_bytes, now)

        mem_percent = (mem_used_bytes / mem_limit_bytes * 100.0) if mem_limit_bytes > 0 else 0.0

        return {
            "total_containers": total,
            "running_containers": running,
            "cpu_percent": round(cpu_total, 2),
            "memory_used_mb": round(mem_used_bytes / 1048576.0, 2),
            "memory_limit_mb": round(mem_limit_bytes / 1048576.0, 2),
            "memory_percent": round(mem_percent, 2),
            "network_tx_mbps": round(tx_mbps, 2),
            "network_rx_mbps": round(rx_mbps, 2),
        }

    @staticmethod
    def _calc_cpu_percent(stats: dict) -> float:
        try:
            cpu_stats = stats.get("cpu_stats", {}) or {}
            precpu = stats.get("precpu_stats", {}) or {}
            cpu_delta = float(cpu_stats.get("cpu_usage", {}).get("total_usage", 0.0)) - float(
                precpu.get("cpu_usage", {}).get("total_usage", 0.0)
            )
            system_delta = float(cpu_stats.get("system_cpu_usage", 0.0)) - float(
                precpu.get("system_cpu_usage", 0.0)
            )
            online_cpus = float(cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", []) or [1]))
            if cpu_delta > 0 and system_delta > 0 and online_cpus > 0:
                return (cpu_delta / system_delta) * online_cpus * 100.0
        except Exception:
            return 0.0
        return 0.0

    def deploy(self, data: dict):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            self.client.images.get(data['image'])
        except docker.errors.ImageNotFound:
            context.logger.info(f"Image {data['image']} not found locally. Pulling...")
            self.client.images.pull(data['image'])

        container = self.client.containers.run(
            image=data['image'],
            name=data['name'],
            mem_limit=f"{data['ram']}m",
            ports={f"{data['ports'].split(':')[1]}/tcp": data['ports'].split(':')[0]} if ':' in data['ports'] else None,
            detach=True,
            restart_policy={"Name": "always"} if data.get('restart') else None
        )

        with get_connection(SYSTEM_DB) as conn:
            for u in data.get('users', []):
                conn.execute(
                    "INSERT OR IGNORE INTO container_permissions (container_id, username) VALUES (?, ?)",
                    (container.id, u)
                )
        return container.id

    def restart_container(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        container.restart()
        container.reload()
        return {
            "id": container.id[:12],
            "name": container.name,
            "status": container.status
        }

    def start_container(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        container.start()
        container.reload()
        return {
            "id": container.id[:12],
            "name": container.name,
            "status": container.status
        }

    def stop_container(self, container_id: str):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        container.stop()
        container.reload()
        return {
            "id": container.id[:12],
            "name": container.name,
            "status": container.status
        }

    def get_container_logs(self, container_id: str, tail: int = 200):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        raw = container.logs(tail=max(1, min(int(tail), 2000)), timestamps=True)
        logs = raw.decode("utf-8", errors="replace")
        return {
            "id": container.id[:12],
            "name": container.name,
            "logs": logs
        }

    def delete_container(self, container_id: str, force: bool = True):
        if not self.available or self.client is None:
            if not self.ensure_client():
                raise RuntimeError("Docker daemon not available")

        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            raise RuntimeError("Container not found")

        full_id = container.id
        container.remove(force=force)

        with get_connection(SYSTEM_DB) as conn:
            conn.execute(
                "DELETE FROM container_permissions WHERE container_id = ?",
                (full_id,)
            )

        return {
            "id": full_id[:12],
            "status": "deleted"
        }
