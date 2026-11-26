from ipaddress import ip_address

from fastapi import Depends, HTTPException, Request


def _already_registered(app, path: str) -> bool:
    try:
        routes = list(getattr(app, "routes", []))
    except Exception:
        return False
    return any(getattr(route, "path", "") == path for route in routes)


def register(app, poller, *, shared_server=False) -> None:
    # Use the very same session dependency as the installed Gradio version.
    login_check = next((route.endpoint for route in app.routes
                        if getattr(route, "path", None) == "/login_check"), None)

    def check_access(request: Request):
        if login_check is None:
            raise HTTPException(503, "Gradio session check unavailable")
        if app.auth is not None or getattr(app, "auth_dependency", None) is not None:
            return
        try:
            local = request.client and ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if shared_server or not local:
            raise HTTPException(403, "Enable Gradio sign-in for shared telemetry")

    guards = [Depends(check_access)]
    if login_check is not None:
        guards.append(Depends(login_check))
    if not _already_registered(app, "/pi-telemetry/snapshot"):

        def get_snapshot():
            try:
                return poller.snapshot_with_cfg()
            except Exception:
                raise HTTPException(503, "Readings temporarily unavailable")

        app.add_api_route("/pi-telemetry/snapshot", get_snapshot, methods=["GET"], dependencies=guards)

    if not _already_registered(app, "/pi-telemetry/config"):

        async def post_config(request: Request):
            if not poller.effective_cfg()["enabled"]:
                raise HTTPException(403, "Telemetry disabled")
            try:
                body = await request.json()
                gpu_index = body["gpu_index"]
                if type(gpu_index) is not int:
                    raise ValueError
            except Exception:
                return {"ok": False, "error": "expected integer gpu_index"}
            try:
                ok = bool(poller.set_gpu_index(gpu_index))
            except Exception:
                ok = False
            return {"ok": ok, "gpu_index": gpu_index if ok else None}

        app.add_api_route("/pi-telemetry/config", post_config, methods=["POST"], dependencies=guards)
