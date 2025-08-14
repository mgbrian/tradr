from quart import Quart, render_template


app = Quart(__name__)


@app.get("/")
async def index():
    return await render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
