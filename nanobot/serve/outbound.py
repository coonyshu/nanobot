"""
Outbound message consumer — background task that forwards bus messages
to the appropriate WebSocket connections.
"""

import asyncio

from loguru import logger

from .state import ServiceState


async def consume_outbound_messages(bus, svc: ServiceState):
    """Continuously consume ``bus.outbound`` and forward to WebSocket clients."""
    logger.info("Starting outbound message consumer loop")
    try:
        while True:
            msg = await bus.consume_outbound()
            logger.info(
                "Outbound message: channel={}, chat_id={}, content={}...",
                msg.channel, msg.chat_id, msg.content[:50],
            )

            if msg.channel == "voice":
                user_id = msg.chat_id
                _, session_data = svc.get_user_session(user_id)

                if session_data:
                    websocket = session_data["websocket"]
                    session_obj = session_data.get("session")

                    try:
                        await websocket.send_json({"type": "text", "text": msg.content})
                        logger.info("Sent text to WebSocket: user={}", user_id)

                        if session_obj and hasattr(session_obj, "tts_text_queue"):
                            await session_obj.tts_text_queue.put(msg.content)
                    except Exception as e:
                        logger.error("Failed to send message to user {}: {}", user_id, e)
                else:
                    logger.warning("User {} not in active voice sessions, message dropped", user_id)
            else:
                logger.warning("Unsupported channel: {}, message dropped", msg.channel)

    except asyncio.CancelledError:
        logger.info("Outbound message consumer stopped")
        raise
    except Exception as e:
        logger.error("Outbound message consumer error: {}", e, exc_info=True)
