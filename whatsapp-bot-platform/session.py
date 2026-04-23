import aiohttp

class SharedSession:
    _session = None

    @classmethod
    async def get_session(cls):
        if cls._session is None:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30, ttl_dns_cache=300)
            timeout = aiohttp.ClientTimeout(total=15)
            cls._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return cls._session

    @classmethod
    async def close_session(cls):
        if cls._session:
            await cls._session.close()
            cls._session = None
