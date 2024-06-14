from dbtunnel.tunnels import DbTunnel


class RayAppTunnel(DbTunnel):

    def __init__(self, ray, port: int = 8080):
        super().__init__(port, flavor="ray")
        self._ray = ray

    def _imports(self):
        try:
            from fastapi import FastAPI
            import uvicorn
            import nest_asyncio
            import ray
        except ImportError as e:
            self._log.info("ImportError: Make sure you have ray, fastapi and nest_asyncio installed;"
                  "pip install ray, fastapi nest_asyncio uvicorn")
            raise e

    def _run(self):
        self.display()
        self._log.info("Starting server...")
        from ray import serve
        from ray.serve.config import HTTPOptions

        import nest_asyncio
        nest_asyncio.apply()

        # uvicorn.run(app, host="0.0.0.0", port=self._port)
        # Start the server
        async def start():
            http_options: HTTPOptions = HTTPOptions(host="0.0.0.0", port=self._port)
            serve.start(http_options=http_options)
            await serve.run(self._ray, route_prefix=self._proxy_settings.url_base_path.rstrip("/"))

        # Run the asyncio event loop instead of uvloop to enable re entrance
        import asyncio
        self._log.info(f"Use this link: \n{self._proxy_settings.proxy_url}")
        asyncio.run(start())

    def _display_url(self):
        # must end with a "/" for it to not redirect
        return f'<a href="{self._proxy_settings.proxy_url}">Click to go to {self._flavor} App!</a>'
