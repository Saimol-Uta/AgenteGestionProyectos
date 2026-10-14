"""Local H5 web API package with lazy factory export."""

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from agente_gestion.api.app import create_app
        return create_app
    raise AttributeError(name)
