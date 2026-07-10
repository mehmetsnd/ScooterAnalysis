"""FastAPI uygulaması — imperative shell. Analiz endpoint'leri sonraki adımda eklenecek."""

from fastapi import FastAPI

app = FastAPI(title="Binbin Scooter Analysis")


@app.get("/health")
def health() -> dict:
    """Servis sağlık kontrolü."""
    return {"status": "ok"}
