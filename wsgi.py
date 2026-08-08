"""WSGI entry point for hosts that look for a `wsgi.py` file (PythonAnywhere,
some traditional hosts, etc.). Platforms using a Procfile (Render, Railway,
Heroku-style) can ignore this file and use `gunicorn app:app` directly.

On PythonAnywhere, point your web app's WSGI configuration file to import
`application` from here, or simply copy the two lines below into it.
"""
from app import app as application

if __name__ == "__main__":
    application.run()
