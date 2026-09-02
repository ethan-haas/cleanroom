import socket

# Connects to a port with no service declared and none available in a clean
# container. No mocking, no try/except -- the point is that this repo never
# declared the service it depends on.
def test_connects_to_backing_service():
    s = socket.create_connection(("127.0.0.1", 59999), timeout=2)
    s.close()
