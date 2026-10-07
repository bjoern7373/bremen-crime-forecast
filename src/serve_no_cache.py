"""
Tiny static file server, identical to `python -m http.server` except it
sends Cache-Control: no-store on every response. Without this, Edge (or any
browser) can silently keep serving an old cached copy of map_local.html
after we update the file on disk -- "the fix isn't here yet" when it's
actually just a stale cache, which is confusing to debug from the outside.
This app's pages are tiny and change often, so there's no real cost to
disabling caching entirely.

Usage: python src/serve_no_cache.py <port>   (serves the current directory)
"""
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8731
    handler = partial(NoCacheHandler, directory=".")
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
