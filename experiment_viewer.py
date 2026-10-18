"""Open local experiment records without loading MuJoCo, PyTorch or the fly brain."""
import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from experiment_library import install_experiment_routes
from startup_support import available_port

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    port = available_port(args.port if args.port is not None else 8476, automatic=args.port is None)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount('/assets', StaticFiles(directory=ROOT / 'web'), name='assets')
    install_experiment_routes(app, ROOT)

    @app.get('/')
    def home():
        return RedirectResponse('/records', status_code=307)

    @app.get('/licenses')
    def license_notices():
        return FileResponse(ROOT / 'web/许可与来源.html', headers={'Cache-Control': 'no-store'})

    print(f'本地实验记录查看器：http://127.0.0.1:{port}/records', flush=True)
    print('此服务不加载脑或身体。可以查看、导入、导出实验包；Ctrl+C 退出。', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=port, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
