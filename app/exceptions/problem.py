"""An HTTP error that also carries a machine-readable `code` (and optional extra keys), for the blog and news endpoints.

The body keeps `detail` as the plain sentence every other endpoint returns, so a client that only shows `detail` keeps
working, and adds the rest next to it:

    {"detail": "Another post in your business already has this title", "code": "DUPLICATE_TITLE", "field": "title"}
"""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


class ProblemHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str, **extra):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.extra = extra

    def body(self) -> dict:
        return {"detail": self.detail, "code": self.code, **self.extra}


def problem(status_code: int, detail: str, code: str, **extra) -> ProblemHTTPException:
    return ProblemHTTPException(status_code, detail, code, **extra)


async def _handle(_request: Request, exc: ProblemHTTPException):
    return JSONResponse(status_code=exc.status_code, content=exc.body(), headers=getattr(exc, "headers", None))


def install(app: FastAPI) -> None:
    app.add_exception_handler(ProblemHTTPException, _handle)
