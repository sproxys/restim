"""
HTTP Basic Authentication handler for the Restim Web UI server.
"""

import base64
import logging

logger = logging.getLogger('restim.webserver.auth')


def check_basic_auth(auth_header: str, username: str, password: str) -> bool:
    """
    Validate HTTP Basic Auth header.

    Args:
        auth_header: The Authorization header value (e.g., "Basic dXNlcjpwYXNz")
        username: Expected username
        password: Expected password. If empty, auth is disabled and always returns True.

    Returns:
        True if authentication is valid or disabled, False otherwise.
    """
    if not password:
        return True

    if not auth_header or not auth_header.startswith('Basic '):
        return False

    try:
        credentials = base64.b64decode(auth_header[6:]).decode('utf-8')
        provided_user, provided_pass = credentials.split(':', 1)
        return provided_user == username and provided_pass == password
    except Exception as e:
        logger.debug(f"Auth decode failed: {e}")
        return False


def create_auth_challenge_headers() -> dict:
    """Create headers for 401 Unauthorized response."""
    return {
        'WWW-Authenticate': 'Basic realm="Restim Web UI"',
        'Content-Type': 'text/plain',
    }
