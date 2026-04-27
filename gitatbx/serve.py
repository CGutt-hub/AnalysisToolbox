import http.server
import os
import socketserver


def run_serve(args) -> int:
    directory = os.path.abspath(args.dir)
    port = int(args.port)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=directory, **kw)

    print(f"Serving {directory}")
    print(f"Open http://localhost:{port}/")
    with socketserver.TCPServer(("", port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
