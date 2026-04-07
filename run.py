import os

from app import create_app


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("XAE_HOST", "0.0.0.0")
    port = int(os.environ.get("XAE_PORT", os.environ.get("PORT", "5000")))
    app.run(host=host, port=port)
