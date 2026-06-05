"""WSGI entry point for production servers (gunicorn).

Usage: gunicorn wsgi:app
"""

from resumebot.webapp import app  # noqa: F401

if __name__ == "__main__":
    from resumebot.webapp import main

    main()
