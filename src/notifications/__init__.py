"""Daily alert delivery: build the message, send it to the configured channels."""
from src.notifications.message import build_html, build_message
from src.notifications.notify import send_notifications

__all__ = ["build_message", "build_html", "send_notifications"]
