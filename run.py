import os

from app import create_app


app = create_app()


if __name__ == "__main__":
    from waitress import serve

    host = os.environ.get("XAE_HOST", "0.0.0.0")
    port = int(os.environ.get("XAE_PORT", os.environ.get("PORT", "5000")))
    serve(app, host=host, port=port)
