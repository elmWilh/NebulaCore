# nebula_core/core/plugin_grpc_client.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import json
import logging
import os
import time
from typing import Optional
from urllib.parse import urlparse

import grpc
from google.protobuf import empty_pb2, struct_pb2
from google.protobuf.json_format import MessageToJson, ParseDict

SERVICE_NAME = "nebula.plugin.v1.PluginService"
METHOD_HEALTH = f"/{SERVICE_NAME}/Health"
METHOD_SYNC_USERS = f"/{SERVICE_NAME}/SyncUsers"


class GrpcPluginClient:
    def __init__(self, endpoint: str, token: str = "", allow_remote: bool = False):
        self.endpoint = str(endpoint or "").strip()
        self.token = token or ""
        self.allow_remote = bool(allow_remote)
        self._channel = None
        self._health_call = None
        self._sync_users_call = None
        self._disabled_until = 0.0

    def _validate_endpoint(self):
        candidate = self.endpoint
        if "://" in candidate:
            parsed = urlparse(candidate)
            host = parsed.hostname or ""
            port = parsed.port
        else:
            if ":" not in candidate:
                raise ValueError("gRPC plugin endpoint must be host:port")
            host, port_raw = candidate.rsplit(":", 1)
            host = host.strip()
            port = int(port_raw)

        if not host or not port:
            raise ValueError("Invalid gRPC plugin endpoint")

        if not self.allow_remote and host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Remote gRPC plugin endpoints are disabled by policy")

    def _ensure_channel(self):
        if self._channel is not None:
            return
        self._validate_endpoint()
        self._channel = grpc.insecure_channel(self.endpoint)
        self._health_call = self._channel.unary_unary(
            METHOD_HEALTH,
            request_serializer=lambda msg: msg.SerializeToString(),
            response_deserializer=struct_pb2.Struct.FromString,
        )
        self._sync_users_call = self._channel.unary_unary(
            METHOD_SYNC_USERS,
            request_serializer=lambda msg: msg.SerializeToString(),
            response_deserializer=struct_pb2.Struct.FromString,
        )

    def _metadata(self):
        if self.token:
            return (("x-nebula-token", self.token),)
        return None

    def _mark_failure(self):
        self._disabled_until = time.time() + 10.0
        self.close()

    def _can_attempt(self):
        return time.time() >= self._disabled_until

    def health(self, timeout: float = 3.0) -> Optional[dict]:
        if not self._can_attempt():
            return None
        try:
            self._ensure_channel()
            response = self._health_call(empty_pb2.Empty(), timeout=timeout, metadata=self._metadata())
            return json.loads(MessageToJson(response))
        except Exception as exc:
            logging.getLogger("nebula_core.plugins.grpc").warning("Plugin gRPC health failed: %s", exc)
            self._mark_failure()
            return None

    def sync_users(self, payload: Optional[dict] = None, timeout: float = 10.0) -> Optional[dict]:
        if not self._can_attempt():
            return None
        try:
            self._ensure_channel()
            request = struct_pb2.Struct()
            ParseDict(payload or {}, request, ignore_unknown_fields=False)
            response = self._sync_users_call(request, timeout=timeout, metadata=self._metadata())
            return json.loads(MessageToJson(response))
        except Exception as exc:
            logging.getLogger("nebula_core.plugins.grpc").warning("Plugin gRPC sync_users failed: %s", exc)
            self._mark_failure()
            return None

    def close(self):
        if self._channel is not None:
            self._channel.close()
            self._channel = None
            self._health_call = None
            self._sync_users_call = None


def resolve_token(token_env: str = "") -> str:
    key = str(token_env or "").strip()
    if not key:
        return ""
    return os.getenv(key, "")
