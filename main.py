import asfquart
from asfquart.auth import Requirements as R

def my_app() -> asfquart.base.QuartApp:
    # Construct the quart service. By default, the oauth gateway is enabled at /oauth.
    app = asfquart.construct("my_app_service")

    @app.route("/")
    async def homepage():
        return "Hello!"

    @app.route("/secret")
    @asfquart.auth.require(R.committer)
    async def secret_page():
        return "Secret stuff!"

    return app

if __name__ == "__main__":
    app = my_app()

    # Run the application in an extended debug mode:
    #  - reload the app when any source / config file get changed
    app.runx(port=5000)
else:
    # Serve the application via an ASGI server, e.g. hypercorn
    app = my_app()
