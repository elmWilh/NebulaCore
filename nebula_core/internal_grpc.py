# nebula_core/internal_grpc.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import json
import os
from typing import Optional

import grpc
from google.protobuf import empty_pb2, struct_pb2, wrappers_pb2
from google.protobuf.json_format import MessageToJson, Parse, ParseDict

from .api.logs import get_log_history_snapshot
from .api.metrics import collect_current_metrics
from .api.security import INTERNAL_AUTH_KEY

SERVICE_NAME = "nebula.internal.v1.Observability"
METHOD_GET_CURRENT_METRICS = "GetCurrentMetrics"
METHOD_GET_LOG_HISTORY = "GetLogHistory"

DEFAULT_GRPC_HOST = os.getenv("NEBULA_CORE_GRPC_HOST", "127.0.0.1")
DEFAULT_GRPC_PORT = int(os.getenv("NEBULA_CORE_GRPC_PORT", "50051"))


class ObservabilityGrpcService:
    @staticmethod
    async def _authorize(context: grpc.aio.ServicerContext) -> bool:
        if not INTERNAL_AUTH_KEY:
            return True
        metadata = {k.lower(): v for k, v in context.invocation_metadata()}
        if metadata.get("x-nebula-token") == INTERNAL_AUTH_KEY:
            return True
        await context.abort(grpc.StatusCode.PERMISSION_DENIED, "Forbidden")
        return False

    async def get_current_metrics(self, request: empty_pb2.Empty, context: grpc.aio.ServicerContext) -> struct_pb2.Struct:
        await self._authorize(context)
        payload = await collect_current_metrics()
        response = struct_pb2.Struct()
        ParseDict(payload or {}, response, ignore_unknown_fields=False)
        return response

    async def get_log_history(self, request: wrappers_pb2.Int32Value, context: grpc.aio.ServicerContext) -> struct_pb2.ListValue:
        await self._authorize(context)
        payload = get_log_history_snapshot(request.value or 200)
        response = struct_pb2.ListValue()
        Parse(json.dumps(payload), response, ignore_unknown_fields=False)
        return response


class InternalGrpcServer:
    def __init__(self, host: str = DEFAULT_GRPC_HOST, port: int = DEFAULT_GRPC_PORT):
        self.host = host
        self.port = int(port)
        self._server: Optional[grpc.aio.Server] = None

    @property
    def bind_target(self) -> str:
        return f"{self.host}:{self.port}"

    async def start(self):
        if self._server is not None:
            return

        service = ObservabilityGrpcService()
        method_handlers = {
            METHOD_GET_CURRENT_METRICS: grpc.unary_unary_rpc_method_handler(
                service.get_current_metrics,
                request_deserializer=empty_pb2.Empty.FromString,
                response_serializer=lambda msg: msg.SerializeToString(),
            ),
            METHOD_GET_LOG_HISTORY: grpc.unary_unary_rpc_method_handler(
                service.get_log_history,
                request_deserializer=wrappers_pb2.Int32Value.FromString,
                response_serializer=lambda msg: msg.SerializeToString(),
            ),
        }

        generic_handler = grpc.method_handlers_generic_handler(SERVICE_NAME, method_handlers)
        self._server = grpc.aio.server()
        self._server.add_generic_rpc_handlers((generic_handler,))
        self._server.add_insecure_port(self.bind_target)
        await self._server.start()

    async def stop(self, grace: float = 3.0):
        if self._server is None:
            return
        await self._server.stop(grace)
        self._server = None


def struct_to_dict(struct_msg: struct_pb2.Struct) -> dict:
    return json.loads(MessageToJson(struct_msg)) if struct_msg is not None else {}


def list_value_to_list(list_msg: struct_pb2.ListValue) -> list:
    return json.loads(MessageToJson(list_msg)) if list_msg is not None else []
