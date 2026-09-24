from fastapi import Request


def _already_registered(app, path: str) -> bool:
    try:
        routes = list(getattr(app, "routes", []))
    except Exception:
        return False
    return any(getattr(route, "path", "") == path for route in routes)


def register(app, poller) -> None:
    if not _already_registered(app, "/pi-telemetry/snapshot"):

        def get_snapshot():
            try:
                return poller.snapshot_with_cfg()
            except Exception as error:
                return {"error": f"{error}"}

        app.add_api_route("/pi-telemetry/snapshot", get_snapshot, methods=["GET"])

    if not _already_registered(app, "/pi-telemetry/config"):

        async def post_config(request: Request):
            try:
                body = await request.json()
                gpu_index = int(body.get("gpu_index"))
            except Exception:
                return {"ok": False, "error": "expected integer gpu_index"}
            try:
                ok = bool(poller.set_gpu_index(gpu_index))
            except Exception:
                ok = False
            return {"ok": ok, "gpu_index": gpu_index if ok else None}

        app.add_api_route("/pi-telemetry/config", post_config, methods=["POST"])
