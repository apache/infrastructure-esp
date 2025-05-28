import asfquart
import quart


def my_app() -> asfquart.base.QuartApp:
    # Construct the quart service. By default, the oauth gateway is enabled at /oauth.
    app = asfquart.construct("my_app_service")

    import esp.routes.ingress_store
    import esp.routes.status

    @app.route("/")
    async def homepage():
        return f"Hello, {quart.request.remote_addr}!"

    return app


if __name__ == "__main__":
    app = my_app()

    # Run the application in an extended debug mode:
    #  - reload the app when any source / config file get changed
    app.runx(port=5000)
else:
    # Serve the application via an ASGI server, e.g. hypercorn
    app = my_app()

