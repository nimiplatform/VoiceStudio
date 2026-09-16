"""Explicit public-URL import for reference playback/editing; never runs AI."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from business.carrier import until_disconnected

router = APIRouter()
MAX_BYTES = 32 * 1024 * 1024


def public_audio_url(value: str) -> str:
    """Validate URL syntax. DNS and every redirect are checked before connecting."""
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError('A public HTTP(S) audio URL is required')
    value = value.strip()
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or any(ord(char) < 33 for char in value)):
        raise ValueError('Use a public HTTP(S) audio URL without credentials or fragments')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname.lower() == 'localhost' or parsed.hostname.lower().endswith('.localhost'):
            raise ValueError('Reference URLs must be public') from None
    else:
        if not address.is_global:
            raise ValueError('Reference URLs must be public')
    return value


async def download_reference(uri: str) -> tuple[bytes, str]:
    current = httpx.URL(public_audio_url(uri))
    # Disable ambient proxies and cookies. Connect to the validated address,
    # retaining the original Host and TLS name to prevent DNS rebinding.
    async with asyncio.timeout(60), httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20) as client:
        for _ in range(6):
            public_audio_url(str(current))
            port = current.port or (443 if current.scheme == 'https' else 80)
            addresses = await asyncio.get_running_loop().getaddrinfo(current.host, port, type=socket.SOCK_STREAM)
            ips = [entry[4][0] for entry in addresses]
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                raise ValueError('Reference URLs must resolve to public addresses')
            target = current.copy_with(host=ips[0])
            async with client.stream('GET', target, headers={
                'Host': current.netloc.decode('ascii'), 'Accept': 'audio/*', 'Accept-Encoding': 'identity',
            }, extensions={'sni_hostname': current.host}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('Audio redirect has no destination')
                    current = current.join(location)
                    continue
                if response.status_code != 200:
                    raise ValueError(f'Audio download failed (HTTP {response.status_code})')
                size = response.headers.get('content-length')
                if size and int(size) > MAX_BYTES:
                    raise ValueError('Reference audio exceeds 32 MiB')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError('Reference audio exceeds 32 MiB')
                # Sniff the original container, not the URL suffix or server's
                # possibly generic Content-Type. The URI still names these bytes.
                raw = bytes(body)
                if raw[:4] == b'RIFF' and raw[8:12] == b'WAVE':
                    mime = 'audio/wav'
                elif raw[:3] == b'ID3' or (len(raw) > 1 and raw[0] == 255 and raw[1] & 224 == 224):
                    mime = 'audio/mpeg'
                elif raw[4:8] == b'ftyp':
                    mime = 'audio/mp4'
                elif raw[:4] == b'fLaC':
                    mime = 'audio/flac'
                elif raw[:4] == b'OggS':
                    mime = 'audio/ogg'
                else:
                    raise ValueError('The URL did not return a supported audio container')
                return raw, mime
    raise ValueError('Too many audio redirects')


@router.post('/reference/import-url')
async def import_reference_url(request: Request):
    body = await request.json()
    uri = public_audio_url(body.get('url'))
    try:
        data, mime = await until_disconnected(request, download_reference(uri))
    except (httpx.HTTPError, TimeoutError, OSError) as error:
        # Do not echo URLs containing query tokens into logs/errors.
        raise ValueError('Could not download the public audio URL') from error
    return Response(data, media_type=mime)
