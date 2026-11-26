"""Shared test helpers."""


def iter_routes(routes):
    """All routes, including those of routers added with include_router (FastAPI keeps those
    nested as one entry that holds the original router)."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from iter_routes(inner.routes)
        else:
            yield r
