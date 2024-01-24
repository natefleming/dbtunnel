import fnmatch
from typing import AsyncGenerator, Union

import aiohttp
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from dbtunnel.vendor.asgiproxy.context import ProxyContext
from dbtunnel.vendor.asgiproxy.utils.streams import read_stream_in_chunks

# TODO: make these configurable?
INCOMING_STREAMING_THRESHOLD = 512 * 1024
OUTGOING_STREAMING_THRESHOLD = 1024 * 1024 * 5


def determine_incoming_streaming(request: Request) -> bool:
    if request.method in ("GET", "HEAD"):
        return False

    try:
        return int(request.headers["content-length"]) < INCOMING_STREAMING_THRESHOLD
    except (TypeError, ValueError, KeyError):
        # Malformed or missing content-length header; assume a very large payload
        return True


def determine_outgoing_streaming(proxy_response: aiohttp.ClientResponse) -> bool:
    if proxy_response.status != 200:
        return False
    try:
        return (
            int(proxy_response.headers["content-length"]) > OUTGOING_STREAMING_THRESHOLD
        )
    except (TypeError, ValueError, KeyError):
        # Malformed or missing content-length header; assume a streaming payload
        return True


async def get_proxy_response(
    *,
    context: ProxyContext,
    scope: Scope,
    receive: Receive,
) -> aiohttp.ClientResponse:
    request = Request(scope, receive)
    should_stream_incoming = determine_incoming_streaming(request)
    async with context.semaphore:
        data: Union[None, AsyncGenerator[bytes, None], bytes] = None
        if request.method not in ("GET", "HEAD"):
            if should_stream_incoming:
                data = request.stream()
            else:
                data = await request.body()

        kwargs = context.config.get_upstream_http_options(
            scope=scope, client_request=request, data=data
        )

        return await context.session.request(**kwargs)


async def convert_proxy_response_to_user_response(
    *,
    context: ProxyContext,
    scope: Scope,
    proxy_response: aiohttp.ClientResponse,
) -> Response:
    headers_to_client = context.config.process_upstream_headers(
        scope=scope, proxy_response=proxy_response
    )
    status_to_client = proxy_response.status
    if determine_outgoing_streaming(proxy_response):
        return StreamingResponse(
            content=read_stream_in_chunks(proxy_response.content),
            status_code=status_to_client,
            headers=headers_to_client,  # type: ignore
        )
    response_content = await proxy_response.read()
    new_headers = headers_to_client

    # Forked code
    if response_content is not None and len(response_content) > 0:
        for path_pattern, modify_func in context.config.modify_content.items():
            if fnmatch.fnmatch(scope["path"], path_pattern):
                response_content = modify_func(response_content)
                new_headers = {k:v for k, v in headers_to_client.items()}
                new_headers["Content-Length"] = str(len(response_content))
    return Response(
        content=response_content,
        status_code=status_to_client,
        headers=new_headers,  # type: ignore
    )


async def proxy_http(
    *,
    context: ProxyContext,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:

    root_path = scope["root_path"]
    if scope["path"].startswith(root_path):
        scope["path"] = scope["path"].replace(root_path, "")

    proxy_response = await get_proxy_response(
        context=context, scope=scope, receive=receive
    )
    user_response = await convert_proxy_response_to_user_response(
        context=context, scope=scope, proxy_response=proxy_response
    )
    return await user_response(scope, receive, send)
