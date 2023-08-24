import asyncio

import aiohttp


async def http_get_bytes(url, **kwargs):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, **kwargs) as resp:
            return await resp.read()


async def http_post_file(url, bytes, params):
    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData(quote_fields=False)
        data.add_field(
            "file", bytes, filename="file", content_type="application/octet-stream"
        )
        for param, value in params.items():
            data.add_field(param, value)
        async with session.post(url, data=data) as resp:
            return await resp.json()
