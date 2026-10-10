from fastapi import FastAPI

app = FastAPI(title="TenderOps API")

@app.get("/health")
def health_check():
    return {"status": "ok"}

    # meow meow
