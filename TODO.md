# Uncaught exceptions

## Unknown Race Condition

INFO:__main__:DEBUG: This is an unhandled exception when scoring peers: read() called while another coroutine is already waiting for incoming data

Is it possible that we have a race because two different coroutines are trying
to use the same asyncio reader/writer with the same socket? Everything else is
protected by locks, but that's a read operation that is not protected by a
lock.

## Alternative Connection Failure Message

INFO:__main__:DEBUG: This is an unhandled exception when scoring peers: [Errno 104] Connection reset by peer

What is the best way to catch this exception? It does not have a more specific
type.
