"""Offline HTTP checks: no real network, GPU, or Forge process."""
import unittest
from unittest.mock import Mock

import httpx
from fastapi import FastAPI, HTTPException, Request

from api.routes import register
import test_telemetry


class RouteChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.auth = None
        self.app.auth_dependency = None
        self.probe, self.poller = test_telemetry.TelemetryTests().poller()
        self.poller.set_gpu_index = Mock(return_value=True)

        @self.app.get("/login_check")
        def login_check(request: Request):
            if self.app.auth and request.cookies.get("session") != "valid":
                raise HTTPException(401)

    async def check(self, shared=False, ip="127.0.0.1", signed_in=False):
        register(self.app, self.poller, shared_server=shared)
        transport = httpx.ASGITransport(app=self.app, client=(ip, 10000))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost",
                                     cookies={"session":"valid"} if signed_in else {}) as client:
            return (await client.get("/pi-telemetry/snapshot"),
                    await client.post("/pi-telemetry/config", json={"gpu_index":0}))

    async def test_shared_requires_sign_in(self):
        self.assertEqual([r.status_code for r in await self.check(shared=True)], [403,403])
        self.app.auth = {"test":"placeholder"}
        self.assertEqual([r.status_code for r in await self.check(shared=True)], [401,401])
        self.assertEqual([r.status_code for r in await self.check(shared=True, signed_in=True)], [200,200])

    async def test_disabled_local(self):
        self.poller.opts_getter = lambda: {"enabled":False}
        self.probe.ram_hw = {"part":"private"}
        snapshot, write = await self.check()
        self.assertEqual(snapshot.json(), {"cfg":{"enabled":False}, "cpu":{}, "ram":{}, "gpus":[]})
        self.assertEqual(write.status_code, 403)
        self.poller.set_gpu_index.assert_not_called()

    async def test_percentage_thresholds(self):
        for amber, red, expected in [(90,50,(90,91)), (-5,120,(0,100)), (120,130,(99,100))]:
            self.poller.opts_getter = lambda: {"amber":amber,"red":red}
            cfg = self.poller.effective_cfg()
            self.assertEqual((cfg["amber"], cfg["red"]), expected)
