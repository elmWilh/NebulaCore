# nebula_gui_flask/core/internal_grpc_client.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

import json
import logging
import time
from typing import Optional

import grpc
from google.protobuf import empty_pb2, struct_pb2, wrappers_pb2
from google.protobuf.json_format import MessageToJson

SERVICE_NAME = "nebula.internal.v1.Observability"
METHOD_GET_CURRENT_METRICS = f"/{SERVICE_NAME}/GetCurrentMetrics"
METHOD_GET_LOG_HISTORY = f"/{SERVICE_NAME}/GetLogHistory"


class InternalGrpcClient:
    def __init__(self, target: str, token: str = ""):
        self.target = target
        self.token = token or ""
        self._channel: Optional[grpc.Channel] = None
        self._metrics_call = None
        self._log_history_call = None
        self._disabled_until = 0.0

    def _can_attempt(self) -> bool:
        return time.time() >= self._disabled_until

    def _mark_failure(self, exc: Exception):
        cool_down = 15.0
        if isinstance(exc, grpc.RpcError) and exc.code() in (
            grpc.StatusCode.PERMISSION_DENIED,
            grpc.StatusCode.UNAUTHENTICATED,
        ):
            cool_down = 60.0
        self._disabled_until = time.time() + cool_down
        self.close()

    def _mark_success(self):
        self._disabled_until = 0.0

    def _ensure_channel(self):
        if self._channel is not None:
            return
        self._channel = grpc.insecure_channel(self.target)
        self._metrics_call = self._channel.unary_unary(
            METHOD_GET_CURRENT_METRICS,
            request_serializer=lambda msg: msg.SerializeToString(),
            response_deserializer=struct_pb2.Struct.FromString,
        )
        self._log_history_call = self._channel.unary_unary(
            METHOD_GET_LOG_HISTORY,
            request_serializer=lambda msg: msg.SerializeToString(),
            response_deserializer=struct_pb2.ListValue.FromString,
        )

    def _metadata(self):
        if self.token:
            return (("x-nebula-token", self.token),)
        return None

    def get_current_metrics(self, timeout: float = 1.0) -> Optional[dict]:
        if not self._can_attempt():
            return None
        try:
            self._ensure_channel()
            response = self._metrics_call(empty_pb2.Empty(), timeout=timeout, metadata=self._metadata())
            self._mark_success()
            return json.loads(MessageToJson(response))
        except grpc.RpcError as exc:
            logging.getLogger("NebulaGrpcClient").debug("gRPC metrics failed: %s", exc)
            self._mark_failure(exc)
            return None
        except Exception as exc:
            self._mark_failure(exc)
            return None

    def get_log_history(self, limit: int = 200, timeout: float = 1.0) -> Optional[list]:
        safe_limit = max(1, min(int(limit), 500))
        if not self._can_attempt():
            return None
        try:
            self._ensure_channel()
            response = self._log_history_call(
                wrappers_pb2.Int32Value(value=safe_limit),
                timeout=timeout,
                metadata=self._metadata(),
            )
            self._mark_success()
            return json.loads(MessageToJson(response))
        except grpc.RpcError as exc:
            logging.getLogger("NebulaGrpcClient").debug("gRPC logs failed: %s", exc)
            self._mark_failure(exc)
            return None
        except Exception as exc:
            self._mark_failure(exc)
            return None

    def close(self):
        if self._channel is not None:
            self._channel.close()
            self._channel = None
            self._metrics_call = None
            self._log_history_call = None
