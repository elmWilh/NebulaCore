import argparse
import os

from flask import Flask, render_template


app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


def get_port() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Nebula Flask demo site."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", 5000)),
        help="Port to run the Flask app on.",
    )
    args = parser.parse_args()
    return args.port


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=get_port(), debug=True)
