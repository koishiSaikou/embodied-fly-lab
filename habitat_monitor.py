"""Loopback FastAPI/WebSocket visual dashboard, sharing the brain monitor state."""
import asyncio
import json
from pathlib import Path
import threading
import time

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
import uvicorn
from activity_monitor import ActivityMonitor
from neuron_annotations import annotations
from prepare_annotations import REPORT_FILE, DATA
from brain_controls import schema as brain_control_schema
from arena_elements import schema as arena_schema
from behavior_model import schema as behavior_schema

ROOT = Path(__file__).resolve().parent


class HabitatMonitor(ActivityMonitor):
    def __init__(self, port=8472, command_callback=None):
        super().__init__(serve_http=False)
        self.url = f"http://127.0.0.1:{port}/"
        self.scene = None
        self.retina_png = None
        self.retina_time = None
        self.command_callback = command_callback
        annotation_store = annotations()
        app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
        app.add_middleware(GZipMiddleware,minimum_size=1000)
        app.mount("/assets",StaticFiles(directory=ROOT/"web"),name="assets")

        @app.get("/")
        def home():
            return FileResponse(ROOT/"web"/"index.html",headers={"Cache-Control":"no-store"})

        @app.get("/brain")
        def brain():
            return FileResponse(ROOT/"activity_monitor.html",headers={"Cache-Control":"no-store"})

        @app.get("/licenses")
        def license_notices():
            return FileResponse(ROOT/"web"/"许可与来源.html",headers={"Cache-Control":"no-store"})

        @app.get("/map")
        def map_view():
            return RedirectResponse('/#arena',status_code=307)

        @app.get("/api/state")
        def state():
            return JSONResponse(self.snapshot(),headers={"Cache-Control":"no-store"})

        @app.get("/api/scene")
        def scene():
            return JSONResponse(self.scene or {},headers={"Cache-Control":"no-store"})

        @app.get("/api/brain-controls")
        def brain_controls():
            return JSONResponse(brain_control_schema(),headers={"Cache-Control":"no-store"})

        @app.get("/api/behavior-controls")
        def behavior_controls():return JSONResponse(behavior_schema(),headers={"Cache-Control":"no-store"})

        @app.get("/api/arena")
        def arena():
            return JSONResponse(arena_schema(),headers={"Cache-Control":"no-store"})

        @app.get("/api/annotations/summary")
        def annotation_summary():
            return JSONResponse(annotation_store.summary)

        @app.get("/api/annotations/report")
        def annotation_report():
            if not REPORT_FILE.exists():
                return JSONResponse({'error': 'Annotation report unavailable'}, status_code=404)
            return FileResponse(REPORT_FILE, media_type='application/json')

        @app.get("/api/annotations/unknown.csv")
        def annotation_unknown():
            path = DATA / 'unknown_neuron_types.csv'
            if not path.exists():
                return JSONResponse({'error': 'Unknown type list unavailable'}, status_code=404)
            return FileResponse(path, filename=path.name, media_type='text/csv')

        @app.get("/api/annotations/search")
        def annotation_search(q: str = Query('', max_length=120),
                              super_class: str = Query('', max_length=80),
                              nt: str = Query('', max_length=80), unknown_only: bool = False,
                              limit: int = Query(20, ge=1, le=100),
                              offset: int = Query(0, ge=0, le=200000)):
            return JSONResponse(annotation_store.search(q, super_class, nt, unknown_only, limit, offset))

        @app.get("/api/annotations/neuron/{identifier}")
        def annotation_neuron(identifier: str):
            row = annotation_store.get(identifier)
            if row is None:
                return JSONResponse({'error': 'ID is not in the current brain model'}, status_code=404)
            return JSONResponse(row)

        @app.get("/api/retina.png")
        def retina():
            with self.lock:
                png,stamp = self.retina_png,self.retina_time
            if png is None:
                return Response(status_code=204)
            return Response(png,media_type="image/png",headers={"Cache-Control":"no-store","X-Model-Time":str(stamp)})

        @app.post("/api/command")
        async def command(request: Request):
            if request.headers.get("origin") not in (None,self.url.rstrip("/")):
                return JSONResponse({"error":"Origin mismatch"},status_code=403)
            body=await request.body()
            if len(body)>4096:
                return JSONResponse({"error":"Command too large"},status_code=400)
            try:
                message=json.loads(body)
                if not isinstance(message,dict):raise ValueError("Command must be an object")
                snap=self.snapshot()
                if snap["status"] in ("finished","error","saving","interrupted") and message.get("type")!="restart":
                    raise ValueError("This experiment has ended; start a new run")
                if message.get("type")=="restart" and not snap.get("recovery",{}).get("can_restart",False):
                    raise ValueError("Wait for cleanup before restarting; check errors if the brain cannot close")
                if snap["status"]=="loading":
                    raise ValueError("Wait for brain loading to complete")
                self.command_callback(message)
                return JSONResponse({"accepted":True},status_code=202)
            except (ValueError,KeyError,TypeError) as exc:
                return JSONResponse({"error":str(exc)},status_code=400)

        @app.websocket("/ws")
        async def websocket(socket: WebSocket):
            if socket.headers.get("origin") not in (None,self.url.rstrip("/")):
                await socket.close(code=1008)
                return
            await socket.accept()
            revision=-1
            try:
                while not self.server.should_exit:
                    snap=self.snapshot()
                    if revision!=snap["revision"]:
                        revision=snap["revision"]
                        # The detailed raster remains on /brain; the visual UI
                        # receives only the current chunk and a short rate curve.
                        snap.pop("events",None)
                        snap["history"]=snap["history"][-100:]
                        await socket.send_json(snap)
                    await asyncio.sleep(.1)
                await socket.close(code=1001)
            except (WebSocketDisconnect,RuntimeError):
                pass

        @app.get("/favicon.ico")
        def favicon():
            return Response(status_code=204)

        self.server = uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,
                                                   log_level="warning",access_log=False,timeout_graceful_shutdown=2))
        self.thread=threading.Thread(target=self.server.run,daemon=True)
        self.thread.start()
        deadline=time.monotonic()+10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic()>deadline:
                raise RuntimeError(f"Cannot start habitat server on {self.url}")
            time.sleep(.025)

    def set_scene(self, scene):
        self.scene=scene

    def lifecycle(self, status=None, **fields):
        with self.lock:
            self.state.update(fields)
            if status is not None:self.state["status"]=status
            self.state["revision"]+=1

    def set_retina(self, png, time_s):
        with self.lock:
            self.retina_png=png
            self.retina_time=time_s

    def close(self):
        self.server.should_exit=True
        self.thread.join(timeout=5)
